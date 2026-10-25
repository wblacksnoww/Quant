"""C++ 加速核心探测与回测分发。

`quant._core` 是 pybind11 编译的原生扩展（源码在 backend/cpp/）。
Docker/CI 构建保证其存在；拼装策略与全部内置策略**必须**有它（缺失直接报错），
没有 Python 降级路径。

已移植到 C++ 的策略：``custom_rule``（拼装 v1/v2）与 ``dual_ma`` /
``momentum_rotation`` / ``rsi_reversion`` / ``buy_hold`` / ``news_dual_ma``。

``news_dual_ma`` 不再自己算指标 —— 它把参数翻译成 spec v1，走与拼装策略
同一条 C++ 求值路径；新闻因子通过 ``extras`` 列传入（``Columns::at()``
原生支持任意字段名），所以 C++ 侧不需要为它单开策略类型。

Python 只保留「数据准备 + 指标后处理」：把 PIT 新闻因子物化成列、
把成交明细重建为性能报告。两者都不是回测热路径。
"""

from __future__ import annotations

import logging
import platform
import sys

log = logging.getLogger(__name__)

# 扩展应当匹配的解释器版本（编译时决定），用于诊断版本漂移
_EXPECTED_ABI = f"cpython-{sys.version_info.major}{sys.version_info.minor}"


def _probe() -> tuple[object | None, bool, str]:
    """探测原生扩展，返回 (模块, 是否可用, 失败原因)。"""
    try:
        from quant import _core  # type: ignore[attr-defined]

        return _core, True, ""
    except Exception as exc:  # ImportError / ABI 不匹配 / 产物损坏
        return None, False, f"{type(exc).__name__}: {exc}"


_native, HAS_NATIVE, NATIVE_ERROR = _probe()

if not HAS_NATIVE:
    # 显式告警：以前这里是静默回退，用户可能在不知情下跑慢 10~70 倍的 Python 路径，
    # 却以为自己在测"加速后"的性能。现在把原因打到日志，并暴露到 /api/system/info。
    log.warning(
        "C++ 加速核心不可用，回测将走 Python 路径（慢 10~70 倍）：%s\n"
        "  当前解释器 ABI: %s (%s)\n"
        "  排查：cd backend && python setup.py build_ext --inplace\n"
        "  （需要 g++ 与 Python 开发头文件，例如 apt install python3-dev）",
        NATIVE_ERROR,
        _EXPECTED_ABI,
        platform.python_version(),
    )

# 原生路径支持的策略
NATIVE_STRATEGIES = {
    "custom_rule",
    "dual_ma",
    "momentum_rotation",
    "rsi_reversion",
    "buy_hold",
    # 参数翻译成 spec v1 后走 C++ 求值；新闻因子经 extras 传入
    "news_dual_ma",
}


def spec_for_strategy(strategy_id: str, params: dict[str, object]) -> dict[str, object] | None:
    """把内置策略的参数翻译成 spec v1（用于走 C++ 求值）。

    ``custom_rule`` 直接用用户给的 spec；``news_dual_ma`` 由参数生成。
    其余内置策略在 C++ 侧有原生实现（``StrategyKind``），不需要 spec。
    返回 ``None`` 表示"该策略不需要 spec"。
    """
    if strategy_id == "custom_rule":
        raw = params.get("spec")
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            import json

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    if strategy_id == "news_dual_ma":
        from .strategy.builtin.news_dual_ma import build_spec

        def pick(key: str, default: object) -> object:
            value = params.get(key, default)
            return default if value is None else value

        return build_spec(
            fast=int(pick("fast", 10)),  # type: ignore[arg-type]
            slow=int(pick("slow", 30)),  # type: ignore[arg-type]
            position_pct=float(pick("position_pct", 0.95)),  # type: ignore[arg-type]
            stop_loss=float(pick("stop_loss", 0.08)),  # type: ignore[arg-type]
            take_profit=float(pick("take_profit", 0.0)),  # type: ignore[arg-type]
            news_window=str(pick("news_window", "1h")),
            min_confidence=float(pick("min_confidence", 0.0)),  # type: ignore[arg-type]
            min_sentiment=float(pick("min_sentiment", -1.0)),  # type: ignore[arg-type]
            max_policy_risk=float(pick("max_policy_risk", 1.0)),  # type: ignore[arg-type]
            require_news=bool(pick("require_news", False)),
        )

    return None


def native_ready(strategy_id: str, *, benchmark: str | None = None) -> bool:
    """该次回测是否走 C++ 引擎（策略已移植）。

    ``benchmark`` 参数保留在签名里但**不再影响判定**：基准只是另一只标的的价格
    序列，在回测结束后由 Python 对齐到权益曲线并算 Beta/Alpha，C++ 主循环无需
    感知它。早期版本因为 "benchmark 只有 Python 路径才算" 而在这里返回 False，
    导致带基准的回测整条降级 —— 那是实现缺失，不是设计约束。
    """
    return HAS_NATIVE and strategy_id in NATIVE_STRATEGIES


def native_status() -> dict[str, object]:
    """原生扩展状态（供 /api/system/info 与前端"运行环境"提示）。"""
    return {
        "available": HAS_NATIVE,
        "error": NATIVE_ERROR,
        "interpreter_abi": _EXPECTED_ABI,
        "python_version": platform.python_version(),
        "strategies": sorted(NATIVE_STRATEGIES),
        "hint": (
            ""
            if HAS_NATIVE
            else "回测走 Python 路径，结果一致但慢 10~70 倍；"
            "重新编译扩展见 backend/setup.py"
        ),
    }
