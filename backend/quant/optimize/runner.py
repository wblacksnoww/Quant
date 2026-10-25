"""参数寻优执行器。

两种模式：

``search``
    在给定区间上跑遍参数空间，按目标指标排序。**结果只用于了解参数敏感性**，
    那个"最优参数"本身带有严重的样本内偏差 —— 想上实盘请用 walk_forward。

``walk_forward``
    滚动切分，训练段选参、测试段验证，输出样本外表现与 WFE。

两者共用同一份「跑一次回测」的实现，区别只在区间与参数怎么组合。数据只加载
一次（``BacktestEngine(frames=...)``），否则寻优 90% 的时间会花在重复读盘上。
"""

from __future__ import annotations

import logging
import math
import statistics
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..engine import BacktestEngine
from ..engine.base import EngineConfig
from .space import expand_space, sample_space
from .walkforward import Fold, make_folds

log = logging.getLogger(__name__)

__all__ = [
    "FoldResult",
    "OptimizeRequest",
    "OptimizeResult",
    "Optimizer",
    "Trial",
]

# 可作为寻优目标的指标（都在 PerformanceReport 里，都是"越大越好"或已取负号）
OBJECTIVES: dict[str, str] = {
    "sharpe": "夏普比率",
    "sortino": "索提诺比率",
    "calmar": "卡玛比率（年化 / 最大回撤）",
    "annual_return": "年化收益",
    "total_return": "区间总收益",
    "win_rate": "胜率",
    "profit_factor": "盈亏比",
}


@dataclass
class Trial:
    """一组参数的运行结果。"""

    index: int
    params: dict[str, Any]
    status: str  # ok / failed / skipped
    metrics: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    run_id: str = ""

    @property
    def score(self) -> float | None:
        """目标值；NaN/inf 视为无效（不可比较）。"""
        value = self.metrics.get("objective")
        if value is None:
            return None
        if not math.isfinite(value):
            return None
        return float(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "params": self.params,
            "status": self.status,
            "metrics": self.metrics,
            "error": self.error,
            "run_id": self.run_id,
        }


@dataclass
class FoldResult:
    """一折 walk-forward 的结果。"""

    fold: Fold
    best_params: dict[str, Any]
    train_metrics: dict[str, float]
    test_metrics: dict[str, float]
    trial_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold": self.fold.to_dict(),
            "best_params": self.best_params,
            "train_metrics": self.train_metrics,
            "test_metrics": self.test_metrics,
            "trial_count": self.trial_count,
            "error": self.error,
        }


@dataclass
class OptimizeRequest:
    """一次寻优的完整输入。"""

    strategy_id: str
    symbols: list[str]
    start: date
    end: date
    space: Mapping[str, Any]
    objective: str = "sharpe"
    initial_cash: float = 100_000.0
    cost: dict[str, Any] = field(default_factory=dict)
    risk: list[dict[str, Any]] | None = None
    benchmark: str | None = None
    base_params: dict[str, Any] = field(default_factory=dict)
    # 少于该成交次数的组合视为"没真正参与市场"，不参与排名。
    # 没有这个门槛，寻优几乎总会选出"只交易 1 次侥幸赚钱"的参数。
    min_trades: int = 5
    method: str = "grid"  # grid / random
    sample_size: int | None = None
    seed: int | None = None
    # walk-forward 专用
    train_days: int = 365
    test_days: int = 90
    step_days: int | None = None

    def validate(self) -> None:
        if self.objective not in OBJECTIVES:
            raise ValueError(f"未知的寻优目标 {self.objective!r}，可选：{', '.join(OBJECTIVES)}")
        if self.method not in {"grid", "random"}:
            raise ValueError(f"未知的搜索方式 {self.method!r}，可选：grid / random")
        if self.end <= self.start:
            raise ValueError("结束日期必须晚于开始日期")
        if self.initial_cash <= 0:
            raise ValueError("初始资金必须大于 0")


@dataclass
class OptimizeResult:
    mode: str
    objective: str
    trials: list[Trial] = field(default_factory=list)
    folds: list[FoldResult] = field(default_factory=list)
    best: Trial | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "objective": self.objective,
            "trials": [t.to_dict() for t in self.trials],
            "folds": [f.to_dict() for f in self.folds],
            "best": self.best.to_dict() if self.best else None,
            "summary": self.summary,
            "error": self.error,
        }


class OptimizeCancelled(RuntimeError):
    """用户取消。"""


ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class Optimizer:
    """执行寻优。构造时传入数据访问层与新闻源。"""

    def __init__(
        self,
        data: Any,
        *,
        news_for: Callable[[date, date], Any] | None = None,
        progress: ProgressFn | None = None,
        cancel: CancelFn | None = None,
    ) -> None:
        self.data = data
        # 新闻源必须按"每次 trial 的区间"构造：walk-forward 的训练段/测试段
        # 各不相同，用同一份整段新闻会让样本外结果混入未来数据。
        self._news_for = news_for
        self._progress = progress or (lambda _p, _m: None)
        self._cancel = cancel or (lambda: False)
        self._frames: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    def _ensure_frames(self, request: OptimizeRequest) -> dict[str, Any]:
        """整段区间只加载一次数据，后续所有 trial 复用。"""
        if self._frames is not None:
            return self._frames
        cfg = self._config(request, request.start, request.end, {}, request.base_params)
        engine = BacktestEngine(cfg, self.data)
        # 用引擎自己的加载逻辑，保证复权/字段口径与正常回测完全一致
        self._frames = engine._load_frames()  # noqa: SLF001 - 同类协作，避免复制加载逻辑
        if not self._frames:
            raise RuntimeError("没有可用的行情数据，请先检查数据源或日期区间")
        return self._frames

    def _slice_frames(
        self, frames: dict[str, Any], start: date, end: date, request: OptimizeRequest
    ) -> dict[str, Any]:
        """按日期切出子区间。

        walk-forward 的训练段/测试段是整段区间的子集，切片比重读一遍数据快得多。
        帧本身不能被修改（C++ 侧只读，但 extras 列是按帧新建的），所以只切不拷贝。
        """
        if start == request.start and end == request.end:
            return frames

        import pandas as pd

        lo = pd.Timestamp(start)
        hi = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        sliced: dict[str, Any] = {}
        for symbol, frame in frames.items():
            dates = pd.to_datetime(frame["date"])
            sub = frame.loc[(dates >= lo) & (dates <= hi)]
            if len(sub):
                sliced[symbol] = sub.reset_index(drop=True)
        return sliced

    @staticmethod
    def _config(
        request: OptimizeRequest,
        start: date,
        end: date,
        params: dict[str, Any],
        base_params: dict[str, Any] | None = None,
    ) -> EngineConfig:
        merged = {**(base_params or {}), **params}
        return EngineConfig(
            strategy_id=request.strategy_id,
            symbols=list(request.symbols),
            start=start,
            end=end,
            initial_cash=request.initial_cash,
            params=merged,
            cost=dict(request.cost or {}),
            risk=request.risk,
            benchmark=request.benchmark,
            run_id=f"opt_{uuid.uuid4().hex[:12]}",
        )

    def _run_one(
        self,
        request: OptimizeRequest,
        start: date,
        end: date,
        params: dict[str, Any],
        index: int,
        *,
        apply_min_trades: bool = True,
    ) -> Trial:
        cfg = self._config(request, start, end, params, request.base_params)
        trial = Trial(index=index, params=params, status="ok", run_id=cfg.run_id)
        try:
            frames = self._slice_frames(self._ensure_frames(request), start, end, request)
            if not frames:
                trial.status = "failed"
                trial.error = f"{start} ~ {end} 区间内没有数据"
                return trial
            news = self._news_for(start, end) if self._news_for else None
            engine = BacktestEngine(cfg, self.data, news=news, frames=frames)
            result = engine.run()
        except Exception as exc:  # noqa: BLE001 - 单组参数失败不能中断整个寻优
            trial.status = "failed"
            trial.error = f"{type(exc).__name__}: {exc}"
            return trial

        if result.status != "finished":
            trial.status = "failed"
            trial.error = result.error or f"回测状态 {result.status}"
            return trial

        metrics = result.metrics.to_dict()
        picked = {
            "objective": float(metrics.get(request.objective) or 0.0),
            "total_return": float(metrics.get("total_return") or 0.0),
            "annual_return": float(metrics.get("annual_return") or 0.0),
            "sharpe": float(metrics.get("sharpe") or 0.0),
            "sortino": float(metrics.get("sortino") or 0.0),
            "calmar": float(metrics.get("calmar") or 0.0),
            "max_drawdown": float(metrics.get("max_drawdown") or 0.0),
            "trade_count": float(metrics.get("trade_count") or 0.0),
            "win_rate": float(metrics.get("win_rate") or 0.0),
        }
        trial.metrics = picked
        if apply_min_trades and picked["trade_count"] < request.min_trades:
            # 成交太少：不是"不好"，而是"没意义"。单独标记，避免污染排名。
            trial.status = "skipped"
            trial.error = f"成交 {int(picked['trade_count'])} 次 < 门槛 {request.min_trades}"
        return trial

    def _combos(self, request: OptimizeRequest) -> list[dict[str, Any]]:
        if request.method == "random":
            count = request.sample_size or 50
            return sample_space(request.space, count, seed=request.seed)
        return expand_space(request.space)

    def _check_cancel(self) -> None:
        if self._cancel():
            raise OptimizeCancelled("寻优已取消")

    # ------------------------------------------------------------------ #
    def search(self, request: OptimizeRequest) -> OptimizeResult:
        request.validate()
        combos = self._combos(request)
        result = OptimizeResult(mode="search", objective=request.objective)
        total = len(combos)
        self._progress(0.0, f"共 {total} 组参数，开始寻优")

        for i, params in enumerate(combos, start=1):
            self._check_cancel()
            result.trials.append(self._run_one(request, request.start, request.end, params, i))
            self._progress(i / total * 0.95, f"已完成 {i}/{total} 组")

        result.best = self._pick_best(result.trials)
        result.summary = self._summarize(result.trials, request)
        self._progress(1.0, "寻优完成")
        return result

    def walk_forward(self, request: OptimizeRequest) -> OptimizeResult:
        request.validate()
        folds = make_folds(
            request.start,
            request.end,
            train_days=request.train_days,
            test_days=request.test_days,
            step_days=request.step_days,
        )
        combos = self._combos(request)
        result = OptimizeResult(mode="walk_forward", objective=request.objective)
        total_steps = len(folds) * (len(combos) + 1)
        done = 0
        self._progress(0.0, f"{len(folds)} 折 × {len(combos)} 组参数")

        for fold in folds:
            self._check_cancel()
            train_trials: list[Trial] = []
            for i, params in enumerate(combos, start=1):
                self._check_cancel()
                train_trials.append(self._run_one(request, fold.train_start, fold.train_end, params, i))
                done += 1
                self._progress(done / total_steps * 0.95, f"第 {fold.index} 折：训练段 {i}/{len(combos)}")

            best = self._pick_best(train_trials)
            fold_result = FoldResult(
                fold=fold,
                best_params=best.params if best else {},
                train_metrics=best.metrics if best else {},
                test_metrics={},
                trial_count=len(train_trials),
                error=None if best else "训练段没有满足门槛的参数组合",
            )
            if best is not None:
                # 测试段不套用 min_trades 门槛：门槛用于"选参数"，不该用于
                # "评估结果"。样本外交易少本身就是结论（参数失灵了），
                # 把它当无效样本丢掉，只会让 walk-forward 看起来比实际好。
                oos = self._run_one(
                    request,
                    fold.test_start,
                    fold.test_end,
                    best.params,
                    0,
                    apply_min_trades=False,
                )
                if oos.status == "ok":
                    fold_result.test_metrics = oos.metrics
                else:
                    fold_result.error = oos.error
            done += 1
            result.folds.append(fold_result)
            self._progress(done / total_steps * 0.95, f"第 {fold.index} 折测试段完成")

        result.summary = self._summarize_walk_forward(result.folds, request)
        self._progress(1.0, "walk-forward 完成")
        return result

    # ------------------------------------------------------------------ #
    @staticmethod
    def _pick_best(trials: Sequence[Trial]) -> Trial | None:
        """按目标值挑最优。只考虑 status=ok 且目标值有限的组合。"""
        candidates = [t for t in trials if t.status == "ok" and t.score is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.score or float("-inf"))

    @staticmethod
    def _summarize(trials: Sequence[Trial], request: OptimizeRequest) -> dict[str, Any]:
        scores = [t.score for t in trials if t.status == "ok" and t.score is not None]
        summary: dict[str, Any] = {
            "total": len(trials),
            "ok": sum(1 for t in trials if t.status == "ok"),
            "failed": sum(1 for t in trials if t.status == "failed"),
            "skipped": sum(1 for t in trials if t.status == "skipped"),
            "min_trades": request.min_trades,
            "objective_label": OBJECTIVES.get(request.objective, request.objective),
        }
        if scores:
            summary.update(
                {
                    "score_min": min(scores),
                    "score_median": statistics.median(scores),
                    "score_max": max(scores),
                }
            )
        else:
            # 一组有效结果都没有，通常意味着数据不足或参数空间全踩在坑里。
            # 这种情况必须显式说出来，否则前端只会看到一张空表。
            summary["note"] = "没有任何参数组合产出有效结果，请检查数据区间与成交门槛"
        return summary

    @staticmethod
    def _summarize_walk_forward(folds: Sequence[FoldResult], request: OptimizeRequest) -> dict[str, Any]:
        tested = [f for f in folds if f.test_metrics]
        if not tested:
            return {
                "folds": len(folds),
                "note": "所有折都没有产出样本外结果，请放宽成交门槛或检查数据",
            }

        def compound(key: str) -> float:
            value = 1.0
            for f in tested:
                value *= 1.0 + float(f.test_metrics.get(key) or 0.0)
            return value - 1.0

        oos_return = compound("total_return")
        is_return = compound("annual_return")

        # WFE = 样本外年化 / 样本内年化。用年化而不是总收益，是为了让各折
        # 长度不同（或训练/测试段长度不同）时仍可比。
        oos_annual = float(statistics.fmean([f.test_metrics.get("annual_return", 0.0) for f in tested]))
        is_annual = float(statistics.fmean([f.train_metrics.get("annual_return", 0.0) for f in tested]))
        wfe = (oos_annual / is_annual) if is_annual > 1e-9 else None

        # 参数稳定性：各折选出的参数一致，说明这个参数组合稳健；
        # 每折都跳到别处，说明它只是在拟合各自的训练段。
        param_sets = [tuple(sorted(f.best_params.items(), key=lambda kv: kv[0])) for f in tested]
        stable = len(set(param_sets)) == 1

        return {
            "folds": len(folds),
            "folds_with_oos": len(tested),
            "oos_return": oos_return,
            "is_return": is_return,
            "oos_annual": oos_annual,
            "is_annual": is_annual,
            "wfe": wfe,
            "params_stable": stable,
            "objective_label": OBJECTIVES.get(request.objective, request.objective),
            "wfe_hint": (
                "样本外效率不足 0.5：参数对历史过度敏感，实盘表现通常要打折"
                if wfe is not None and wfe < 0.5
                else ""
            ),
        }
