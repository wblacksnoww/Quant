"""参数寻优与 walk-forward 的请求/响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .runs import CostInput, RiskRuleInput, dedupe_symbols

__all__ = [
    "OptimizeJobOut",
    "OptimizeJobSummary",
    "OptimizeStartRequest",
    "WalkForwardOptions",
]

OBJECTIVE = Literal[
    "sharpe",
    "sortino",
    "calmar",
    "annual_return",
    "total_return",
    "win_rate",
    "profit_factor",
]


class WalkForwardOptions(BaseModel):
    """滚动窗口设置。仅在 ``walk_forward=true`` 时生效。"""

    train_days: int = Field(default=365, ge=30, le=3650, description="训练段长度（自然日）")
    test_days: int = Field(default=90, ge=10, le=730, description="测试段长度（自然日）")
    step_days: int | None = Field(
        default=None,
        ge=1,
        le=730,
        description="滚动步长；缺省等于测试段长度（测试段首尾相接）",
    )


class OptimizeStartRequest(BaseModel):
    """提交一次寻优。

    ``space`` 是"参数名 → 候选值"的声明式描述，支持三种写法：

    - 枚举：``{"fast": [5, 10, 15]}``
    - 等差：``{"slow": {"start": 20, "stop": 60, "step": 10}}``
    - 固定：``{"position_pct": 0.95}``
    """

    strategy_id: str = Field(min_length=1, max_length=64)
    symbols: list[str] = Field(min_length=1, max_length=30)
    start: date
    end: date
    space: dict[str, Any] = Field(min_length=1)
    objective: OBJECTIVE = "sharpe"
    initial_cash: float = Field(default=1_000_000, gt=0, le=1e10)
    params: dict[str, Any] = Field(default_factory=dict, description="不参与寻优的固定参数")
    cost: CostInput | None = None
    risk: list[RiskRuleInput] | None = None
    benchmark: str | None = None
    min_trades: int = Field(
        default=5,
        ge=0,
        le=10_000,
        description="成交次数门槛：低于该值的参数组合不参与排名，防止选出「几乎不交易」的伪最优",
    )
    method: Literal["grid", "random"] = "grid"
    sample_size: int | None = Field(default=None, ge=1, le=2000, description="随机采样组数")
    seed: int | None = Field(default=None, description="随机种子，便于复现")
    walk_forward: bool = False
    walk_forward_options: WalkForwardOptions = Field(default_factory=WalkForwardOptions)

    @field_validator("symbols")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return dedupe_symbols(values)

    @field_validator("end")
    @classmethod
    def _check_range(cls, value: date, info) -> date:
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("结束日期必须晚于开始日期")
        return value


class OptimizeJobSummary(BaseModel):
    """列表用的轻量状态。"""

    job_id: str
    mode: str
    status: str
    progress: float = 0.0
    message: str = ""
    created_at: str
    finished_at: str | None = None
    error: str | None = None


class OptimizeJobOut(OptimizeJobSummary):
    """单次寻优的完整结果。

    ``trials`` 默认只返回按目标值排序的前 N 条 —— 一次寻优可能有上千组，
    全量返回既拖慢前端也没人看第 800 名。
    """

    summary: dict[str, Any] = Field(default_factory=dict)
    best: dict[str, Any] | None = None
    trials: list[dict[str, Any]] = Field(default_factory=list)
    folds: list[dict[str, Any]] = Field(default_factory=list)
    total_trials: int = 0
