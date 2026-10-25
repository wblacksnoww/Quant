"""参数寻优与 walk-forward。

对外只暴露四个东西：参数空间展开、折切分、执行器、取消信号。

     >>> from quant.optimize import Optimizer, OptimizeRequest, expand_space
     >>> combos = expand_space({"fast": [5, 10], "slow": {"start": 20, "stop": 40, "step": 10}})
     >>> Optimizer(ctx.data).search(OptimizeRequest(...))

设计原则：寻优只是"跑很多次回测"，不引入第二套撮合/指标实现 ——
每次 trial 走的都是 ``BacktestEngine``，与用户在回测页拿到的结果逐位一致。
"""

from __future__ import annotations

from .runner import (
    OBJECTIVES,
    FoldResult,
    OptimizeCancelled,
    Optimizer,
    OptimizeRequest,
    OptimizeResult,
    Trial,
)
from .space import MAX_COMBINATIONS, SpaceError, count_combinations, expand_space, sample_space
from .walkforward import Fold, WalkForwardError, make_folds

__all__ = [
    "MAX_COMBINATIONS",
    "OBJECTIVES",
    "Fold",
    "FoldResult",
    "OptimizeCancelled",
    "OptimizeRequest",
    "OptimizeResult",
    "Optimizer",
    "SpaceError",
    "Trial",
    "WalkForwardError",
    "count_combinations",
    "expand_space",
    "make_folds",
    "sample_space",
]
