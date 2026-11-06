"""多实例会话租约/命令与持久化回测队列。"""

from __future__ import annotations

import time
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import update

from quant.engine.base import EngineConfig
from quant.server.context import build_context
from quant.services import paper_service
from quant.storage.backtest_jobs import BacktestJobRepository
from quant.storage.models import BacktestJob, utcnow_naive


def _paper_body() -> SimpleNamespace:
    return SimpleNamespace(
        strategy_id="buy_hold",
        symbols=["CN:600519"],
        params={},
        initial_cash=100_000.0,
        start=None,
        end=None,
        cost={},
        risk=None,
        benchmark=None,
        risk_free_rate=0.0,
        mode="replay",
        replay_days=60,
        replay_interval=0.05,
        poll_interval=5.0,
        respect_market_hours=True,
    )


def _shutdown(ctx) -> None:
    if ctx.coordinator is not None:
        ctx.coordinator.stop()
    ctx.paper.stop_all(timeout=5)
    ctx.live.stop_all(timeout=5)
    if ctx.backtests is not None:
        ctx.backtests.shutdown(timeout=5)
    ctx.db.dispose()


def test_lease_store_is_exclusive_and_releasable(context):
    from quant.services.coordinator import LeaseStore

    store_a = LeaseStore(context.db, "instance-a")
    store_b = LeaseStore(context.db, "instance-b")
    key = "live:1:9"
    assert store_a.acquire(key, kind="live", user_id=1, run_id="run_x", state=None, ttl=5)
    assert not store_b.acquire(key, kind="live", user_id=1, run_id="run_x", state=None, ttl=5)
    assert store_a.get(key)["owner"] == "instance-a"
    store_a.release(key)
    assert store_b.acquire(key, kind="live", user_id=1, run_id="run_x", state=None, ttl=5)


def test_commands_are_polled_once(context):
    from quant.services.coordinator import LeaseStore

    store = LeaseStore(context.db, "instance-a")
    store.enqueue("paper:r1", "stop")
    assert [item["action"] for item in store.poll("paper:r1")] == ["stop"]
    assert store.poll("paper:r1") == []


def test_paper_session_visible_across_instances_and_remote_stop(settings):
    """两个上下文共享数据库：B 能看到 A 的会话并下发停止命令。"""
    ctx_a = build_context(settings)
    ctx_b = build_context(settings)
    try:
        ctx_a.coordinator.start()
        ctx_b.coordinator.start()
        run_id = paper_service.start(ctx_a, user_id=1, body=_paper_body())
        time.sleep(0.8)
        ctx_a.coordinator._tick()

        remote = paper_service.sessions(ctx_b, user_id=1)
        assert [item["run_id"] for item in remote] == [run_id]
        assert remote[0]["status"] in ("running", "preparing")

        stopped = paper_service.stop(ctx_b, user_id=1, run_id=run_id)
        assert stopped == 1
        ctx_a.coordinator._tick()
        time.sleep(0.3)
        assert not ctx_a.paper.running(1, run_id)
    finally:
        _shutdown(ctx_a)
        _shutdown(ctx_b)


def test_backtest_job_claim_and_stale_requeue(context):
    jobs = BacktestJobRepository(context.db)
    jobs.create("run_job_1", user_id=1, config={"strategy_id": "buy_hold", "symbols": ["CN:600519"]})
    first = jobs.claim("worker-a")
    assert first is not None and first["run_id"] == "run_job_1"
    assert jobs.claim("worker-b") is None
    assert jobs.status("run_job_1") == "running"

    # 模拟心跳超时后由另一个实例接管
    with context.db.session() as session:
        session.execute(
            update(BacktestJob)
            .where(BacktestJob.run_id == "run_job_1")
            .values(updated_at=utcnow_naive() - timedelta(minutes=10))
        )
    assert jobs.requeue_stale(stale_seconds=60) == 1
    second = jobs.claim("worker-b")
    assert second is not None and second["run_id"] == "run_job_1"


def test_backtest_queue_runs_to_completion(context):
    config = EngineConfig(
        strategy_id="buy_hold",
        symbols=["CN:600519"],
        start=date(2023, 1, 3),
        end=date(2024, 6, 28),
        initial_cash=100_000.0,
        run_id="run_queue_test",
        user_id=1,
    )
    context.backtests.submit(config)
    deadline = time.time() + 20
    while time.time() < deadline:
        if context.backtests.jobs.status(config.run_id) in ("done", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert context.backtests.jobs.status(config.run_id) == "done"
    with context.db.session() as session:
        from quant.storage.repositories.runs import RunRepository

        record = RunRepository(session).get(config.run_id)
        assert record is not None and record.status == "finished"


# --------------------------------------------------------------------------- #
# 停止会话后的状态一致性
# --------------------------------------------------------------------------- #


class _FakeLeases:
    def __init__(self, rows):
        self.rows = rows

    def active_for_user(self, user_id):  # noqa: ANN001
        return [dict(row) for row in self.rows]


def _state(run_id: str, status: str = "running") -> dict:
    return {
        "run_id": run_id,
        "strategy_id": "dual_ma",
        "symbols": ["CRYPTO:BTC/USDT"],
        "mode": "replay",
        "status": status,
    }


def _bare_coordinator(instance_id: str, rows):
    from quant.services.coordinator import SessionCoordinator

    coord = object.__new__(SessionCoordinator)
    coord.instance_id = instance_id
    coord.leases = _FakeLeases(rows)
    return coord


def test_remote_state_ignores_own_instance_snapshot():
    """本实例的租约快照不是"远程状态"。

    会话停掉后本地已经查不到，但租约里还留着上一次心跳的 running 快照。
    如果 state 回落到这份快照，前端点了停止会一直显示"运行中"。
    """
    coord = _bare_coordinator(
        "me",
        [
            {"kind": "paper", "owner": "me", "run_id": "run_mine", "state": _state("run_mine")},
            {"kind": "paper", "owner": "other", "run_id": "run_other", "state": _state("run_other")},
        ],
    )
    got = coord.remote_state(1, kind="paper")
    assert got is not None
    assert got["run_id"] == "run_other", "把自己的陈旧快照当成了远程会话状态"

    # 指定自己那个 run_id 时，必须返回 None（本地没有就是真没有）
    assert coord.remote_state(1, run_id="run_mine", kind="paper") is None


def test_paper_stop_all_releases_every_local_lease():
    """不传 run_id 的"全部停止"也要逐个释放租约，不能只释放第一个。"""
    released: list[str] = []

    class _FakeCoordinator:
        instance_id = "me"

        def __init__(self) -> None:
            self.leases = _FakeLeases([])

        def release(self, key: str) -> None:
            released.append(key)

        def request(self, key, action, payload=None):  # noqa: ANN001, D401
            raise AssertionError("本地会话不该走远程命令")

    class _Runner:
        running = True

    class _FakePaper:
        def running(self, user_id, run_id=None):  # noqa: ANN001
            return True

        def runners(self, user_id):  # noqa: ANN001
            return {"run_a": _Runner(), "run_b": _Runner()}

        def stop(self, user_id, run_id=None, timeout=10.0):  # noqa: ANN001
            return 2

    ctx = SimpleNamespace(paper=_FakePaper(), coordinator=_FakeCoordinator())
    stopped = paper_service.stop(ctx, user_id=1)

    assert stopped == 2
    assert set(released) == {"paper:run_a", "paper:run_b"}, (
        f"停止全部会话只释放了 {released}，残留租约会让状态查询返回陈旧的 running 快照"
    )
