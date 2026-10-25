"""条件拼装策略（网页端免代码写策略）。

用户在前端用「指标 + 比较/交叉」拼出入场/离场规则，保存为 JSON spec；
后端只有一个 ``CustomRuleStrategy`` 解释器类，spec 作为参数传入。

安全边界：

* 指标、字段、比较符全部走**白名单**，不做 ``eval``，不暴露 import 面；
* spec 在保存（CRUD）与提交运行两条路径上都会经 :func:`parse_spec` 校验；
* spec 大小与条件数量有硬上限，防止构造超大 payload。

信号求值由 C++ ``quant._core.SpecEvaluator`` 完成（窗口重喂，与
``history(min_rows)`` 滑窗口径一致）；本文件只负责 spec 的解析、校验与订单纪律。

spec 结构（v1）::

    {
      "version": 1,
      "description": "双均线金叉买入",
      "category": "trend",
      "entry":   [ {"type": "cross_above", "left": {...}, "right": {...}}, ... ],
      "exit":    [ {"type": "lt", "left": {...}, "right": {...}}, ... ],
      "position_pct": 0.9,
      "stop_loss": 0.08,        # 0 = 不启用
      "take_profit": 0.0        # 0 = 不启用
    }

* ``entry`` 里的条件**全部满足**且空仓 → 次日开盘按 ``position_pct`` 买入；
* ``exit`` 里的条件**任一满足**且持仓 → 清仓；
* 操作数 ``{"kind": "field", "field": "close"}`` / ``{"kind": "indicator", "name": "sma", "params": {"period": 10}}``
  / ``{"kind": "const", "value": 30}``。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..core.types import Bar
from . import indicators as ta
from .base import ParamSpec, Strategy
from .custom_fields import ALL_FIELDS, NEWS_FIELDS
from .custom_graph import (
    GRAPH_VERSION,
    MAX_GRAPH_SPEC_BYTES,
    GraphSpec,
    graph_lookback,
    parse_graph_spec,
)

# 指标白名单唯一来源：v1 条件与 v2 蓝图共用（参数边界随之同源）
from .indicators import (
    INDICATOR_INT_BOUNDS as _INT_BOUNDS,
)
from .indicators import (
    INDICATOR_SPECS as _INDICATORS,
)
from .indicators import (
    INDICATOR_WIDTH_BOUNDS as _WIDTH_BOUNDS,
)
from .registry import register_strategy

try:  # C++ 拼装策略求值器（必需：构建产物由 Docker/CI 保证）
    from .. import core_native as _core_native
except Exception:  # pragma: no cover
    _core_native = None

STRATEGY_ID = "custom_rule"

SPEC_VERSION = 1
MAX_CONDITIONS = 6
MAX_SPEC_BYTES = 8192

_FIELDS = ALL_FIELDS
_COMPARE_OPS = ("gt", "gte", "lt", "lte")
_CROSS_OPS = ("cross_above", "cross_below")

# 指标白名单：name -> (函数描述, 参数规则)
# 参数规则: "int"/"float" 表示必填数值参数；带默认值的写 (type, default)
_FIELDS = ALL_FIELDS


# --------------------------------------------------------------------------- #
# spec 解析与校验
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Operand:
    kind: str  # field | indicator | const
    field: str = ""
    name: str = ""
    params: tuple[tuple[str, float], ...] = ()
    value: float = 0.0


@dataclass(frozen=True, slots=True)
class Condition:
    op: str
    left: Operand
    right: Operand


@dataclass(frozen=True, slots=True)
class CustomSpec:
    entry: tuple[Condition, ...]
    exit: tuple[Condition, ...]
    position_pct: float = 0.9
    stop_loss: float = 0.08
    take_profit: float = 0.0
    category: str = "trend"
    description: str = ""
    lookback: int = 0  # 计算出的最大指标周期

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SPEC_VERSION,
            "description": self.description,
            "category": self.category,
            "entry": [_cond_to_dict(c) for c in self.entry],
            "exit": [_cond_to_dict(c) for c in self.exit],
            "position_pct": self.position_pct,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
        }


def _cond_to_dict(cond: Condition) -> dict[str, Any]:
    return {
        "type": cond.op,
        "left": _operand_to_dict(cond.left),
        "right": _operand_to_dict(cond.right),
    }


def _operand_to_dict(op: Operand) -> dict[str, Any]:
    if op.kind == "const":
        return {"kind": "const", "value": op.value}
    if op.kind == "field":
        return {"kind": "field", "field": op.field}
    return {"kind": "indicator", "name": op.name, "params": dict(op.params)}


def _fail(message: str) -> None:
    raise ValueError(message)


def _parse_operand(raw: Any, *, path: str) -> Operand:
    if not isinstance(raw, dict):
        _fail(f"{path} 必须是对象")
    unknown = set(raw) - {"kind", "field", "name", "params", "value"}
    if unknown:
        _fail(f"{path} 含未知字段 {sorted(unknown)}")

    kind = raw.get("kind")
    if kind == "const":
        value = raw.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            _fail(f"{path}.value 必须是数字")
        if not -1e15 < float(value) < 1e15:
            _fail(f"{path}.value 数值超出范围")
        return Operand(kind="const", value=float(value))

    if kind == "field":
        field = raw.get("field")
        # Python 算子字段（op:name(k=v)）是研究通道的动态字段，
        # 不在静态白名单里；格式与注册状态由 operators 模块自己校验。
        from .operators import is_operator_field, parse_operator_field

        if is_operator_field(str(field)):
            try:
                parse_operator_field(str(field))
            except ValueError as exc:
                _fail(f"{path}.field {exc}")
            return Operand(kind="field", field=str(field))

        if field not in _FIELDS:
            _fail(f"{path}.field 只支持 {_FIELDS}，或 op:name(k=v) 形式的算子字段")
        return Operand(kind="field", field=str(field))

    if kind == "indicator":
        name = raw.get("name")
        if name not in _INDICATORS:
            _fail(f"{path}.name 只支持 {sorted(_INDICATORS)}")
        params_raw = raw.get("params", {})
        if not isinstance(params_raw, dict):
            _fail(f"{path}.params 必须是对象")
        unknown = set(params_raw) - set(_INDICATORS[str(name)])
        if unknown:
            _fail(f"指标 {name} 不支持参数 {sorted(unknown)}")

        parsed: list[tuple[str, float]] = []
        for pname, (ptype, default) in _INDICATORS[str(name)].items():
            value = params_raw.get(pname, default)
            if value is None:
                _fail(f"指标 {name} 缺少必填参数 {pname}")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _fail(f"指标 {name}.{pname} 必须是数字")
            value = float(value)
            if ptype == "int":
                if value != int(value):
                    _fail(f"指标 {name}.{pname} 必须是整数")
                lo, hi = _INT_BOUNDS
                if pname in ("fast", "signal"):
                    lo = 2
                if not lo <= value <= hi:
                    _fail(f"指标 {name}.{pname} 需在 {lo}~{hi} 之间")
            else:
                lo, hi = _WIDTH_BOUNDS
                if not lo <= value <= hi:
                    _fail(f"指标 {name}.{pname} 需在 {lo}~{hi} 之间")
            parsed.append((pname, int(value) if ptype == "int" else value))
        return Operand(kind="indicator", name=str(name), params=tuple(parsed))

    _fail(f"{path}.kind 只支持 field / indicator / const")


def _parse_condition(raw: Any, *, path: str) -> Condition:
    if not isinstance(raw, dict):
        _fail(f"{path} 必须是对象")
    unknown = set(raw) - {"type", "left", "right"}
    if unknown:
        _fail(f"{path} 含未知字段 {sorted(unknown)}")
    op = raw.get("type")
    if op not in _COMPARE_OPS + _CROSS_OPS:
        _fail(f"{path}.type 只支持 {_COMPARE_OPS + _CROSS_OPS}")
    for side in ("left", "right"):
        if side not in raw:
            _fail(f"{path} 缺少 {side}")
    left = _parse_operand(raw["left"], path=f"{path}.left")
    right = _parse_operand(raw["right"], path=f"{path}.right")
    return Condition(op=str(op), left=left, right=right)


def _operand_lookback(op: Operand) -> int:
    if op.kind != "indicator":
        return 0
    params = dict(op.params)
    if op.name in ("macd_dif", "macd_dea", "macd_hist"):
        return int(params.get("slow", 26)) + int(params.get("signal", 9))
    return int(params.get("period", 0))


def _bounded(value: Any, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{name} 必须是数字")
    value = float(value)
    if not lo <= value <= hi:
        _fail(f"{name} 需在 {lo}~{hi} 之间")
    return value


def parse_spec(raw: Any) -> CustomSpec | GraphSpec:
    """解析并校验 spec。任何问题抛 ``ValueError``（中文消息，直接给前端看）。

    v1 → 条件数组（:class:`CustomSpec`）；v2 → 蓝图节点图（:class:`GraphSpec`）。
    """
    if isinstance(raw, str):
        size = len(raw.encode("utf-8"))
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            _fail(f"spec 不是合法 JSON：{exc}")
        if not isinstance(raw, dict):
            _fail("spec 必须是 JSON 对象")
        if raw.get("version", SPEC_VERSION) == GRAPH_VERSION:
            if size > MAX_GRAPH_SPEC_BYTES:
                _fail(f"spec 超过 {MAX_GRAPH_SPEC_BYTES} 字节上限")
        elif size > MAX_SPEC_BYTES:
            _fail(f"spec 超过 {MAX_SPEC_BYTES} 字节上限")
    if not isinstance(raw, dict):
        _fail("spec 必须是 JSON 对象")

    version = raw.get("version", SPEC_VERSION)
    if version == GRAPH_VERSION:
        return parse_graph_spec(raw)
    if version != SPEC_VERSION:
        _fail("spec 版本不支持（当前 v1 / v2）")

    unknown = set(raw) - {
        "version",
        "description",
        "category",
        "entry",
        "exit",
        "position_pct",
        "stop_loss",
        "take_profit",
    }
    if unknown:
        _fail(f"spec 含未知字段 {sorted(unknown)}")
    # v1 分支（v2 已在上方分派给 parse_graph_spec）
    entry_raw = raw.get("entry")
    exit_raw = raw.get("exit")
    if not isinstance(entry_raw, list) or not entry_raw:
        _fail("entry 必须是非空条件数组")
    if not isinstance(exit_raw, list) or not exit_raw:
        _fail("exit 必须是非空条件数组")
    if len(entry_raw) > MAX_CONDITIONS or len(exit_raw) > MAX_CONDITIONS:
        _fail(f"每侧条件最多 {MAX_CONDITIONS} 条")

    entry = tuple(_parse_condition(item, path=f"entry[{i}]") for i, item in enumerate(entry_raw))
    exit_ = tuple(_parse_condition(item, path=f"exit[{i}]") for i, item in enumerate(exit_raw))

    lookback = 0
    for cond in entry + exit_:
        lookback = max(lookback, _operand_lookback(cond.left), _operand_lookback(cond.right))

    category = raw.get("category", "trend")
    if category not in ("trend", "mean_reversion", "momentum"):
        _fail("category 只支持 trend / mean_reversion / momentum")
    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > 255:
        _fail("description 必须是 ≤255 字符的字符串")

    return CustomSpec(
        entry=entry,
        exit=exit_,
        position_pct=_bounded(raw.get("position_pct", 0.9), "position_pct", 0.01, 1.0),
        stop_loss=_bounded(raw.get("stop_loss", 0.08), "stop_loss", 0.0, 0.95),
        take_profit=_bounded(raw.get("take_profit", 0.0), "take_profit", 0.0, 5.0),
        category=category,
        description=description,
        lookback=lookback,
    )


def validate_spec(raw: Any) -> dict[str, Any]:
    """校验并返回规范化后的 spec dict（CRUD 存库与提交运行共用）。"""
    return parse_spec(raw).to_dict()


def spec_lookback(spec: CustomSpec) -> int:
    """策略需要的最少历史行数（含交叉信号的前一根）。"""
    return spec.lookback + 2


# --------------------------------------------------------------------------- #
# 求值
# --------------------------------------------------------------------------- #


def _series_for_operand(frame: pd.DataFrame, op: Operand):
    """返回 pd.Series 或标量（const）。比较与交叉运算对两者都成立。"""
    if op.kind == "const":
        return op.value
    if op.kind == "field":
        return frame[op.field]
    close = frame["close"]
    params = dict(op.params)
    name = op.name
    if name == "sma":
        return ta.sma(close, int(params["period"]))
    if name == "ema":
        return ta.ema(close, int(params["period"]))
    if name == "rsi":
        return ta.rsi(close, int(params["period"]))
    if name == "std":
        return ta.std(close, int(params["period"]))
    if name == "rolling_high":
        return ta.rolling_high(close, int(params["period"]))
    if name == "rolling_low":
        return ta.rolling_low(close, int(params["period"]))
    if name == "momentum":
        return ta.momentum(close, int(params["period"]))
    if name == "atr":
        return ta.atr(frame["high"], frame["low"], close, int(params["period"]))
    if name in ("boll_lower", "boll_mid", "boll_upper"):
        lower, mid, upper = ta.bollinger(close, int(params["period"]), float(params.get("width", 2.0)))
        return {"boll_lower": lower, "boll_mid": mid, "boll_upper": upper}[name]
    if name in ("macd_dif", "macd_dea", "macd_hist"):
        dif, dea, hist = ta.macd(
            close,
            int(params.get("fast", 12)),
            int(params.get("slow", 26)),
            int(params.get("signal", 9)),
        )
        return {"macd_dif": dif, "macd_dea": dea, "macd_hist": hist}[name]
    _fail(f"未知指标 {name!r}")  # pragma: no cover - 白名单已挡


# --------------------------------------------------------------------------- #
# 解释器策略
# --------------------------------------------------------------------------- #


@register_strategy
class CustomRuleStrategy(Strategy):
    """把 JSON spec 翻译成交易的通用策略。"""

    strategy_id = STRATEGY_ID
    display_name = "条件拼装策略"
    category = "custom"
    description = (
        "在网页上用指标条件拼装买卖规则，无需写代码。"
        "在「策略库 → 我的拼装策略」里创建，回测/纸盘提交时选择即可。"
    )

    param_specs = (ParamSpec("spec", "json", {}, description="条件拼装 spec（由网页生成，一般不手动填）"),)

    def __init__(self, ctx, params=None):
        super().__init__(ctx, params)
        try:
            self.spec = parse_spec(self.params.get("spec"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"拼装策略 spec 无效：{exc}") from exc
        self._is_graph = isinstance(self.spec, GraphSpec)
        self._news_fields = self._news_fields_used()
        self._uses_news = bool(self._news_fields)
        # 信号计算只走 C++ SpecEvaluator（与回测/网关同一份解释器）。
        # 构建产物由 Docker/CI 保证存在；这里缺失即视为环境损坏，直接报错。
        if _core_native is None or not _core_native.HAS_NATIVE:
            raise RuntimeError(
                f"拼装策略需要 C++ 核心（quant._core）：{_core_native.NATIVE_ERROR if _core_native else '未构建'}"
            )
        raw = params.get("spec") if isinstance(params, Mapping) else None
        self._native = _core_native._native.SpecEvaluator(raw)
        self._native_cache: dict[str, Any] = {}  # symbol → numpy 列缓存（避免每 bar 重建）
        if self._is_graph:
            self._min_rows = graph_lookback(self.spec)
            self.log(
                f"蓝图载入：{len(self.spec.topological)} 个节点（lookback {self.spec.lookback}），"
                f"仓位 {self.spec.position_pct:.0%}，止损 {self.spec.stop_loss:.0%}，"
                f"止盈 {self.spec.take_profit:.0%}"
            )
        else:
            self._min_rows = spec_lookback(self.spec)
            self.log(
                f"spec 载入：入场 {len(self.spec.entry)} 条 / 离场 {len(self.spec.exit)} 条，"
                f"仓位 {self.spec.position_pct:.0%}，止损 {self.spec.stop_loss:.0%}，"
                f"止盈 {self.spec.take_profit:.0%}"
            )

    def _news_fields_used(self) -> set[str]:
        if self._is_graph:
            return {node.type for node in self.spec.nodes if node.type in NEWS_FIELDS}
        return {
            operand.field
            for cond in self.spec.entry + self.spec.exit
            for operand in (cond.left, cond.right)
            if operand.field in NEWS_FIELDS
        }

    def _attach_news(self, frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """把新闻因子按每根 bar 的时点展开成列（PIT），供字段条件直接使用。"""
        out = frame.copy()
        as_ofs = [pd.Timestamp(ts).to_pydatetime() for ts in out["date"]]
        # 实时模式的“今日 bar”日期是 00:00，必须用当前时刻判断新闻可见性；
        # 回测时 ctx.now 就是当前 bar 的时间，结果一致。
        if as_ofs:
            as_ofs[-1] = self.ctx.now
        for field in self._news_fields:
            metric, window = NEWS_FIELDS[field]
            values = []
            for as_of in as_ofs:
                factor = self.ctx.news_factor_at(symbol, as_of=as_of, window=window)
                values.append(getattr(factor, metric))
            out[field] = values
        return out

    def _native_signals(self, symbol: str, frame: pd.DataFrame) -> tuple[bool, bool]:
        """C++ 求值：frame → numpy 列（带行数缓存），spec 支持新闻字段列。

        frame 已是 history(min_rows) 截断后的窗口——cursor 必须用窗口内
        行号（len-1），不能用全局游标（会越界读到窗口外的数据）。
        """
        import numpy as np

        cursor = len(frame) - 1
        cached = self._native_cache.get(symbol)
        if cached is None or cached[0] is not frame or cached[1] != len(frame):
            arrays = {}
            for col in ("open", "high", "low", "close", "volume", "amount"):
                if col in frame.columns:
                    arrays[col] = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype="float64")
                else:
                    arrays[col] = np.zeros(len(frame))
            # 新闻因子列（_attach_news 已展开进 frame）作为 extras 传给 C++
            extras = {}
            for field in self._news_fields:
                if field in frame.columns:
                    extras[field] = pd.to_numeric(frame[field], errors="coerce").to_numpy(dtype="float64")
            cached = (frame, len(frame), arrays, extras)
            self._native_cache[symbol] = cached
        _, _, arrays, extras = cached
        # 窗口重喂：与 Python history(symbol, min_rows) 滑窗口径一致（ewm 类指标需要）
        return self._native.evaluate_window(
            cursor,
            self._min_rows,
            len(frame),
            arrays["open"],
            arrays["high"],
            arrays["low"],
            arrays["close"],
            arrays["volume"],
            arrays["amount"],
            extras,
        )

    def on_bars(self, bars: Mapping[str, Bar]) -> None:
        for bar in bars.values():
            self._on_symbol(bar)

    def _on_symbol(self, bar: Bar) -> None:
        frame = self.history(bar.symbol, self._min_rows)
        if self._uses_news:
            frame = self._attach_news(frame, bar.symbol)
        if len(frame) < self._min_rows:
            return  # 指标数据不足，跳过
        if self.has_open_order(bar.symbol):
            return

        pos = self.position(bar.symbol)

        # --- 先管持仓：止损 / 止盈 ------------------------------------- #
        if not pos.is_flat and pos.available > 0 and pos.avg_cost > 0:
            pnl = bar.close / pos.avg_cost - 1.0
            if self.spec.stop_loss > 0 and pnl <= -self.spec.stop_loss:
                self.log(f"{bar.symbol} 触发止损 {pnl:.2%}")
                self.close(bar.symbol, tag="stop_loss")
                return
            if self.spec.take_profit > 0 and pnl >= self.spec.take_profit:
                self.log(f"{bar.symbol} 触发止盈 {pnl:.2%}")
                self.close(bar.symbol, tag="take_profit")
                return

        # --- 信号：C++ SpecEvaluator（与回测/网关同一份解释器） ---------- #
        entry_signal, exit_signal = self._native_signals(bar.symbol, frame)

        if pos.is_flat:
            if entry_signal:
                self.buy(bar.symbol, percent=self.spec.position_pct, tag="entry")
        elif pos.available > 0:
            if exit_signal:
                self.close(bar.symbol, tag="exit")


__all__ = [
    "STRATEGY_ID",
    "CustomRuleStrategy",
    "CustomSpec",
    "Condition",
    "Operand",
    "MAX_CONDITIONS",
    "MAX_SPEC_BYTES",
    "parse_spec",
    "spec_lookback",
    "validate_spec",
]
