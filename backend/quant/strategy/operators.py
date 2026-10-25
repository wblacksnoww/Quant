"""Python 快速验证算子（研究通道）。

## 为什么需要它

C++ 指标是**编译期固定**的（`cpp/strategy.cpp` 的 `indicator_kind` 是 if 链）。
想试一个新想法（比如"波动率调整动量"），走 C++ 要改代码、重编译、重启，
一个来回几分钟，而这正是研究阶段最频繁的动作。

这里的做法：**算子用 Python 写、全序列预计算、作为 extras 列喂给 C++**。
C++ 主循环不需要任何改动 —— 它从 `Columns::at()` 按列名取值，
取不到返回 NaN，这个机制本来就有（新闻字段就是这么走的）。

## 语义边界（必须清楚，否则会误判结果）

* 算子是**全序列向量化**的：给定整段 close 数组，返回整段数组。
  所以算子内部**不许看未来** —— 第 i 个输出只能用 `src[0..i]`。
  这条无法自动强制，靠约定 + `assert_causal()` 在注册时做形状检查。
* NaN 一律视为"条件不成立"（C++ 侧 `NaN > x` 为 false），
  所以算子开头 warmup 段要返回 NaN 而不是 0 —— 否则会被误判成有效信号。
* 这些算子**只在 Python 通道生效**。要进生产（C++），需要在
  `cpp/indicators.cpp` 里用同样公式重写一遍，并用等价性测试锁定。

## 用法

.. code-block:: python

    from quant.strategy.operators import register_operator, op

    @register_operator("vol_adjusted_momentum", period=("int", 20))
    def _vam(df, period=20):
        ret = df["close"].pct_change()
        vol = ret.rolling(period, min_periods=period).std()
        return ret.rolling(period, min_periods=period).mean() / vol.replace(0, np.nan)

写进 spec 后，在 Python 通道里作为一个普通字段名使用::

    {"kind": "field", "field": "op:vol_adjusted_momentum(period=20)"}

C++ 通道遇到未注册的 `op:*` 字段会拿到 NaN 列，从而条件恒不成立 ——
这一点由引擎显式告警（见 `engine/backtest.py` 的算子检查）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# op 字段名格式：op:<name>(k=v,k=v)
_OP_RE = re.compile(r"^op:([A-Za-z_][A-Za-z0-9_]*)\(([^)]*)\)$")


@dataclass
class OperatorSpec:
    """一个 Python 算子的定义。"""

    name: str
    func: Callable[..., pd.Series]
    params: dict[str, tuple[str, float | None]] = field(default_factory=dict)
    description: str = ""

    def coerce(self, raw: dict[str, Any]) -> dict[str, Any]:
        """按声明的参数表补齐默认值并做类型转换。"""
        out: dict[str, Any] = {}
        for pname, (ptype, default) in self.params.items():
            value = raw.get(pname, default)
            if value is None:
                raise ValueError(f"算子 {self.name} 缺少必填参数 {pname}")
            out[pname] = int(value) if ptype == "int" else float(value)
        unknown = set(raw) - set(self.params)
        if unknown:
            raise ValueError(f"算子 {self.name} 不支持参数 {sorted(unknown)}")
        return out


_REGISTRY: dict[str, OperatorSpec] = {}


def register_operator(
    name: str,
    **params: tuple[str, float | None],
) -> Callable[[Callable[..., pd.Series]], Callable[..., pd.Series]]:
    """注册一个 Python 算子。

    :param name: 算子名（用于 `op:name(...)` 字段）
    :param params: 参数声明，`名字=("int"|"float", 默认值或 None)`
    """

    def deco(func: Callable[..., pd.Series]) -> Callable[..., pd.Series]:
        if name in _REGISTRY:
            raise ValueError(f"算子 {name} 已注册")
        _REGISTRY[name] = OperatorSpec(
            name=name, func=func, params=dict(params), description=(func.__doc__ or "").strip()
        )
        return func

    return deco


def registered() -> dict[str, OperatorSpec]:
    return dict(_REGISTRY)


def is_operator_field(field_name: str) -> bool:
    return isinstance(field_name, str) and field_name.startswith("op:")


def parse_operator_field(field_name: str) -> tuple[str, dict[str, float]]:
    """把 `op:name(a=1,b=2)` 解析成 (name, params)。格式错直接抛 ValueError。"""
    match = _OP_RE.match(field_name)
    if match is None:
        raise ValueError(
            f"算子字段格式应为 op:name(k=v)，实际 {field_name!r}"
        )
    name, raw_args = match.group(1), match.group(2).strip()
    params: dict[str, float] = {}
    if raw_args:
        for chunk in raw_args.split(","):
            if "=" not in chunk:
                raise ValueError(f"算子参数应为 k=v，实际 {chunk!r}")
            key, _, value = chunk.partition("=")
            try:
                params[key.strip()] = float(value)
            except ValueError as exc:
                raise ValueError(f"算子参数 {key.strip()} 必须是数字") from exc
    return name, params


def resolve_operator(field_name: str) -> str:
    """校验一个算子字段是否可用；返回空字符串表示正常，否则返回问题描述。

    与 :func:`compute_operator` 分开，是为了让调用方能在**跑回测之前**
    一次性把所有算子都体检一遍，把问题汇总成一条用户可见的告警 ——
    而不是算的时候各自打 log（用户看不到）。
    """
    try:
        name, raw_params = parse_operator_field(field_name)
    except ValueError as exc:
        return str(exc)

    spec = _REGISTRY.get(name)
    if spec is None:
        return f"算子 {name} 未注册（可用：{sorted(_REGISTRY) or '无'}）"
    try:
        spec.coerce(raw_params)
    except ValueError as exc:
        return str(exc)
    return ""


def compute_operator(frame: pd.DataFrame, field_name: str) -> np.ndarray:
    """在整段 K 线上算一个算子，返回与 frame 等长的 float64 数组。

    这是"喂给 C++ 的那一列"的唯一生产点。未注册的算子返回全 NaN ——
    刻意不抛异常：回测应该跑完并让用户看到"策略没成交"，
    同时在 logs 里给出原因，而不是整个任务 failed。
    """
    name, raw_params = parse_operator_field(field_name)
    spec = _REGISTRY.get(name)
    if spec is None:
        log.warning(
            "算子 %s 未注册（可用：%s），该字段将全为 NaN，"
            "相关条件恒不成立。",
            name,
            sorted(_REGISTRY) or "无",
        )
        return np.full(len(frame), np.nan)

    kwargs = spec.coerce(raw_params)
    try:
        series = spec.func(frame, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 用户算子出错不应炸掉整个回测
        log.warning("算子 %s 执行失败（%s），该字段将全为 NaN", name, exc, exc_info=True)
        return np.full(len(frame), np.nan)

    if not isinstance(series, pd.Series):
        series = pd.Series(np.asarray(series, dtype="float64"), index=frame.index)
    if len(series) != len(frame):
        log.warning(
            "算子 %s 返回长度 %d 与 K 线长度 %d 不一致，该字段将全为 NaN",
            name,
            len(series),
            len(frame),
        )
        return np.full(len(frame), np.nan)
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype="float64")


def operator_fields_in_spec(spec: dict[str, Any]) -> list[str]:
    """扫出 spec 里用到的全部算子字段名（v1 条件 + v2 蓝图节点）。"""
    found: set[str] = set()

    def scan_operand(item: object) -> None:
        if not isinstance(item, dict):
            return
        field_name = item.get("field")
        if isinstance(field_name, str) and is_operator_field(field_name):
            found.add(field_name)

    for cond in spec.get("entry", []) + spec.get("exit", []):
        if isinstance(cond, dict):
            scan_operand(cond.get("left"))
            scan_operand(cond.get("right"))

    for node in spec.get("nodes", []):
        if not isinstance(node, dict):
            continue
        # 蓝图里算子既可能作为 field 型节点的 field，也可能作为节点 type
        node_type = node.get("type")
        if isinstance(node_type, str) and is_operator_field(node_type):
            found.add(node_type)
        for key in ("field", "left", "right"):
            scan_operand(node.get(key))

    return sorted(found)


# --------------------------------------------------------------------------- #
# 内置示例算子：作为"怎么写"的范本，也给测试一个稳定的目标
# --------------------------------------------------------------------------- #


@register_operator("zscore", period=("int", 20))
def _zscore(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    """收盘价的滚动 z-score：(x - 均值) / 标准差。

    典型用法：`field = "op:zscore(period=20)"` 配 `< -2` 做均值回归。
    warmup 段返回 NaN（不是 0），否则会被当成"z=0 的合法信号"。
    """
    close = pd.to_numeric(frame["close"], errors="coerce")
    mean = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return (close - mean) / std.replace(0.0, np.nan)


@register_operator("vol_ratio", fast=("int", 5), slow=("int", 20))
def _vol_ratio(frame: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    """短期波动率 / 长期波动率，>1 表示波动放大。"""
    close = pd.to_numeric(frame["close"], errors="coerce")
    ret = close.pct_change()
    v_fast = ret.rolling(fast, min_periods=fast).std(ddof=0)
    v_slow = ret.rolling(slow, min_periods=slow).std(ddof=0)
    return v_fast / v_slow.replace(0.0, np.nan)


@register_operator("gap", )
def _gap(frame: pd.DataFrame) -> pd.Series:
    """隔夜跳空幅度：(open - prev_close) / prev_close。"""
    open_ = pd.to_numeric(frame["open"], errors="coerce")
    prev = pd.to_numeric(frame["close"], errors="coerce").shift(1)
    return (open_ - prev) / prev.replace(0.0, np.nan)


def assert_causal(
    frame: pd.DataFrame,
    field_name: str,
    *,
    probe_rows: tuple[int, ...] = (50, 100, 200),
) -> tuple[bool, str]:
    """粗检算子是否看了未来：截断输入，前缀输出应保持不变。

    做法：分别在 `frame[:k]` 和 `frame[:k+1]` 上算，前者应等于后者前 k 行。
    用 rolling/ewm 实现的因果算子必然满足；用了 `.shift(-1)`、`iloc[::-1]`、
    全样本标准化（`(x - x.mean()) / x.std()`）的会露馅。

    只做**抽查**（默认 3 个截断点），不能保证覆盖所有未来函数，
    但能挡住最常见那几类。
    """
    ref = compute_operator(frame, field_name)
    if not np.isfinite(ref).any():
        return False, "算子返回全 NaN，无法检验因果性"
    for k in probe_rows:
        if k >= len(frame) or k < 2:
            continue
        truncated = compute_operator(frame.iloc[:k].copy(), field_name)
        a, b = truncated, ref[:k]
        both_nan = np.isnan(a) & np.isnan(b)
        close = np.isclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=False)
        if not np.all(both_nan | close):
            bad = np.flatnonzero(~(both_nan | close))
            return False, (
                f"截断到 {k} 行后第 {bad[0]} 行值从 {b[bad[0]]!r} 变为 {a[bad[0]]!r}，"
                f"说明算子用了未来数据"
            )
    return True, ""


__all__ = [
    "OperatorSpec",
    "assert_causal",
    "compute_operator",
    "is_operator_field",
    "operator_fields_in_spec",
    "parse_operator_field",
    "register_operator",
    "registered",
    "resolve_operator",
]
