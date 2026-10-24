"""C++ 加速核心探测与回测分发。

`quant._core` 是 pybind11 编译的原生扩展（源码在 backend/cpp/）。构建不可用时
（无编译器 / QUANT_SKIP_NATIVE=1），所有调用方自动回退 Python 路径，功能无损。

受支持的策略与条件：

* ``custom_rule``（v1/v2 spec）与 ``dual_ma`` / ``momentum_rotation`` /
  ``rsi_reversion`` / ``buy_hold``；
* 未设置 benchmark（基准对照仍在 Python 侧，后续再移植）；
* 拼装策略含新闻因子字段时，Python 先把 PIT 因子物化为数据列再交给 C++。
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
    """该次回测能否走 C++ 快速路径。"""
    return HAS_NATIVE and strategy_id in NATIVE_STRATEGIES and benchmark is None
