"""纸盘按用户隔离，以及 WebSocket 事件定向投递。"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from quant.engine.paper import PaperManager
from quant.server.ws import EventHub


class FakeEngine:
    """最小可运行的纸盘替身：阻塞到 stop() 被调用。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.config = SimpleNamespace(run_id=run_id)
        self._stop = threading.Event()
        self.started = threading.Event()

    def run(self) -> None:
        self.started.set()
        self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()

    def state(self) -> dict:
        return {"run_id": self.run_id, "status": "running"}


def _wait_started(engine: FakeEngine) -> None:
    assert engine.started.wait(2), "纸盘线程未启动"


def test_two_users_can_run_paper_simultaneously():
    manager = PaperManager()
    first, second = FakeEngine("paper:a"), FakeEngine("paper:b")

    manager.start(1, first)
    manager.start(2, second)
    _wait_started(first)
    _wait_started(second)

    assert manager.running(1) and manager.running(2)
    assert set(manager.active_users) == {1, 2}
    assert manager.state(1)["run_id"] == "paper:a"
    assert manager.state(2)["run_id"] == "paper:b"

    assert manager.stop(1)
    assert manager.stop(2)
    assert not manager.running(1) and not manager.running(2)


def test_same_user_can_run_multiple_sessions():
    manager = PaperManager()
    first, second = FakeEngine("paper:a"), FakeEngine("paper:b")
    manager.start(7, first)
    manager.start(7, second)
    _wait_started(first)
    _wait_started(second)

    assert manager.running(7)
    assert len(manager.sessions(7)) == 2
    assert {item["run_id"] for item in manager.sessions(7)} == {"paper:a", "paper:b"}

    # 同一 run_id 不允许重复启动
    with pytest.raises(RuntimeError):
        manager.start(7, FakeEngine("paper:a"))

    # 指定 run_id 只停一个
    assert manager.stop(7, "paper:a") == 1
    assert manager.running(7, "paper:b")
    assert not manager.running(7, "paper:a")
    manager.stop(7)


def test_session_limit_per_user():
    manager = PaperManager(max_sessions_per_user=2)
    engines = [FakeEngine(f"paper:{i}") for i in range(3)]
    manager.start(1, engines[0])
    manager.start(1, engines[1])
    with pytest.raises(RuntimeError, match="不能超过"):
        manager.start(1, engines[2])
    manager.stop(1)


def test_state_is_per_user():
    manager = PaperManager()
    engine = FakeEngine("paper:a")
    manager.start(1, engine)
    _wait_started(engine)
    try:
        assert manager.state(2) is None
        assert manager.engine(2) is None
        assert manager.stop(2) == 0
    finally:
        manager.stop(1)


def test_stop_all():
    manager = PaperManager()
    engines = [FakeEngine(f"paper:{i}") for i in (1, 2)]
    for user_id, engine in zip((1, 2), engines):
        manager.start(user_id, engine)
        _wait_started(engine)
    manager.stop_all()
    assert manager.active_users == []


def test_event_hub_routes_private_events_to_owner_only():
    hub = EventHub()
    loop = asyncio.new_event_loop()
    hub.bind(loop)
    try:
        owner = hub.subscribe(user_id=1)
        other = hub.subscribe(user_id=2)

        hub.publish("run.fill", {"symbol": "600519"}, user_id=1)
        loop.run_until_complete(asyncio.sleep(0.01))

        assert owner.get_nowait()["type"] == "run.fill"
        assert other.empty()

        # 公共事件仍然广播
        hub.publish("system.notice", {"message": "maintenance"})
        loop.run_until_complete(asyncio.sleep(0.01))
        assert other.get_nowait()["type"] == "system.notice"
    finally:
        loop.close()


def test_event_hub_replay_respects_ownership():
    hub = EventHub()
    hub.publish("run.fill", {"symbol": "600519"}, user_id=1)
    hub.publish("system.notice", {"message": "ok"})

    private = hub.subscribe(user_id=2, replay=10)
    assert private.get_nowait()["type"] == "system.notice"
    assert private.empty()

    own = hub.subscribe(user_id=1, replay=10)
    assert own.get_nowait()["type"] == "run.fill"
    assert own.get_nowait()["type"] == "system.notice"
