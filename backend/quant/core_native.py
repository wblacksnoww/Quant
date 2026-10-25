"""C++ 加速核心探测与回测分发。

`quant._core` 是 pybind11 编译的原生扩展（源码在 backend/cpp/）。
Docker/CI 构建保证其存在；拼装策略与受支持的内置策略**必须**有它（缺失直接报错），
仅 news_dual_ma 等少数策略仍走 Python 引擎。

已移植到 C++ 的策略：``custom_rule``（拼装 v1/v2）与 ``dual_ma`` /
``momentum_rotation`` / ``rsi_reversion`` / ``buy_hold``。

拼装策略**没有 Python 求值路径**：``CustomRuleStrategy`` 缺扩展直接报错。内置策略保留
Python 实现，用于「带 benchmark 的回测」与「等价性测试基线」，不是降级回退。
C++ 路径抛异常时也不再静默重跑 Python——错误直接冒到运行结果里。

拼装策略含新闻因子字段时，Python 先把 PIT 因子物化为数据列再交给 C++。
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:  # pragma: no cover - 取决于构建环境
    from quant import _core as _native  # type: ignore[attr-defined]

    HAS_NATIVE = True
    NATIVE_ERROR = ""
except Exception as exc:  # ImportError 或构建产物损坏
    _native = None
    HAS_NATIVE = False
    NATIVE_ERROR = f"{type(exc).__name__}: {exc}"

# 原生路径支持的策略
NATIVE_STRATEGIES = {
    "custom_rule",
    "dual_ma",
    "momentum_rotation",
    "rsi_reversion",
    "buy_hold",
}


def native_ready(strategy_id: str, *, benchmark: str | None = None) -> bool:
    """该次回测是否走 C++ 引擎（策略已移植且无 benchmark 对照）。"""
    return HAS_NATIVE and strategy_id in NATIVE_STRATEGIES and benchmark is None
