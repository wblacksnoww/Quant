"""spec v2：蓝图式节点图（React Flow 编辑器编译目标）。

与 v1（数组条件）同属 :mod:`quant.strategy.custom` 的白名单体系：
节点类型全白名单、无 ``eval``、无 import 面；引擎执行前经统一入口
:func:`quant.strategy.custom.parse_spec` 校验。

图语义是**数据流**：每个节点输出一条时间序列（或标量常数），连线即引用；
``outputs.entry`` / ``outputs.exit`` 指向的布尔节点其最后一根 bar 的值
决定买入/清仓。NaN（数据不足）在布尔节点一律按 False 处理。

结构::

    {
      "version": 2,
      "description": "...", "category": "trend",
      "position_pct": 0.9, "stop_loss": 0.08, "take_profit": 0.0,
      "nodes": [
        {"id": "s1", "type": "close", "pos": {"x": 48, "y": 120}},   # pos 仅编辑器布局用，后端忽略
        {"id": "f1", "type": "sma", "params": {"period": 20}},
        {"id": "x1", "type": "cross_above"}
      ],
      "edges": [{"from": "s1", "to": "f1", "to_port": "in"},
                {"from": "f1", "to": "x1", "to_port": "right"}],
      "outputs": {"entry": "x1", "exit": "x2"}
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import indicators as ta
from .custom_fields import ALL_FIELDS

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

GRAPH_VERSION = 2
MAX_NODES = 48
MAX_EDGES = 96
MAX_GRAPH_SPEC_BYTES = 16384

_ID_RE = re.compile(r"^[a-z0-9_]{1,24}$")

# 节点类型 ------------------------------------------------------------------- #

# 数据源（无输入，输出序列）
_SOURCES = ALL_FIELDS
# 指标（单输入 price，输出序列；参数白名单与 v1 一致）
_MACD_TUP = ("macd_dif", "macd_dea", "macd_hist")

# 算术（双输入，输出序列）
_ARITH = ("add", "sub", "mul", "div")
# 比较（双输入，输出布尔）
_COMPARE = ("gt", "gte", "lt", "lte", "cross_above", "cross_below")
# 逻辑（输出布尔）
_LOGIC_1 = ("not",)
_LOGIC_2 = ("and", "or")

_BOOL_NODES = frozenset(_COMPARE + _LOGIC_1 + _LOGIC_2)
_ALL_TYPES = (
    frozenset(_SOURCES)
    | frozenset(_INDICATORS)
    | frozenset(_ARITH + _COMPARE + _LOGIC_1 + _LOGIC_2)
    | {"const"}
)

# 每个 "kind" 在画布上是一种颜色：
#   const  —— 标量常数（可连任何数值输入）
#   series —— 时间序列
#   bool   —— 布尔序列（只能连逻辑输入与 outputs）
_KIND_COLORS = {"const": "#94a3b8", "series": "#22d3ee", "bool": "#f59e0b"}


def _fail(message: str) -> None:
    raise ValueError(message)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class GraphNode:
    id: str
    type: str
    attrs: tuple[tuple[str, float], ...] = ()  # 指标参数（const 用 (("value", v),)）
    inputs: dict[str, str] = field(default_factory=dict)  # port -> from_node_id
    pos: dict[str, float] | None = None  # 编辑器布局坐标，仅透传

    @property
    def const_value(self) -> float | None:
        if self.type != "const":
            return None
        return dict(self.attrs).get("value")


@dataclass(frozen=True, slots=True)
class GraphSpec:
    nodes: tuple[GraphNode, ...]
    entry_id: str
    exit_id: str
    position_pct: float = 0.9
    stop_loss: float = 0.08
    take_profit: float = 0.0
    category: str = "trend"
    description: str = ""
    topological: tuple[str, ...] = ()  # 拓扑序（仅 outputs 可达部分）
    lookback: int = 0

    def node(self, node_id: str) -> GraphNode:
        return self._index[node_id]

    @property
    def _index(self) -> dict[str, GraphNode]:
        return {n.id: n for n in self.nodes}

    def to_dict(self) -> dict[str, Any]:
        nodes_out: list[dict[str, Any]] = []
        for n in self.nodes:
            item: dict[str, Any] = {"id": n.id, "type": n.type}
            if n.attrs:
                item["params"] = dict(n.attrs)
            if n.pos:
                item["pos"] = n.pos
            nodes_out.append(item)
        edges_out = [
            {"from": src, "to": n.id, "to_port": port} for n in self.nodes for port, src in n.inputs.items()
        ]
        return {
            "version": GRAPH_VERSION,
            "description": self.description,
            "category": self.category,
            "position_pct": self.position_pct,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "nodes": nodes_out,
            "edges": edges_out,
            "outputs": {"entry": self.entry_id, "exit": self.exit_id},
        }


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


def _node_output_kind(node_type: str) -> str:
    if node_type in _BOOL_NODES:
        return "bool"
    if node_type == "const":
        return "const"
    return "series"


def _expected_inputs(node_type: str) -> dict[str, str]:
    """期望的输入端口：端口名 -> 允许的来源 kind。数据源与常数为空（只输出）。"""
    if node_type in _SOURCES or node_type == "const":
        return {}
    if node_type in _INDICATORS:
        return {"in": "series"}  # 指标必须吃序列（常数接指标无意义，直接拒绝更清晰）
    if node_type in _ARITH or node_type in _COMPARE:
        return (
            {"left": "series", "right": "series"}
            if node_type in ("cross_above", "cross_below")
            else {"left": "series|const", "right": "series|const"}
        )
    if node_type in _LOGIC_1:
        return {"in": "bool"}
    if node_type in _LOGIC_2:
        return {"left": "bool", "right": "bool"}
    _fail(f"未知节点类型 {node_type!r}")
    return {}


def _kind_ok(actual: str, allowed: str) -> bool:
    return actual in allowed.split("|")


def _parse_attrs(node_type: str, raw: Any, *, path: str) -> tuple[tuple[str, float], ...]:
    """节点属性：const 取 value；指标参数与 v1 相同边界；其余节点无属性。"""
    parsed: list[tuple[str, float]] = []
    if node_type == "const":
        value = (raw or {}).get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(f"{path}.value 必须是数字")
        if not -1e15 < float(value) < 1e15:
            _fail(f"{path}.value 数值超出范围")
        return (("value", float(value)),)

    rules = _INDICATORS.get(node_type)
    if rules is None:
        return ()

    raw_params = (raw or {}).get("params", {})
    if not isinstance(raw_params, dict):
        _fail(f"{path}.params 必须是对象")
    unknown = set(raw_params) - set(rules)
    if unknown:
        _fail(f"指标 {node_type} 不支持参数 {sorted(unknown)}")
    for pname, (ptype, default) in rules.items():
        value = raw_params.get(pname, default)
        if value is None:
            _fail(f"指标 {node_type} 缺少必填参数 {pname}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(f"指标 {node_type}.{pname} 必须是数字")
        value = float(value)
        if ptype == "int":
            if value != int(value):
                _fail(f"指标 {node_type}.{pname} 必须是整数")
            lo = 2 if pname in ("fast", "signal") else _INT_BOUNDS[0]
            if not lo <= value <= _INT_BOUNDS[1]:
                _fail(f"指标 {node_type}.{pname} 需在 {lo}~{_INT_BOUNDS[1]} 之间")
        else:
            if not _WIDTH_BOUNDS[0] <= value <= _WIDTH_BOUNDS[1]:
                _fail(f"指标 {node_type}.{pname} 需在 {_WIDTH_BOUNDS[0]}~{_WIDTH_BOUNDS[1]} 之间")
        parsed.append((pname, int(value) if ptype == "int" else value))
    return tuple(parsed)


def parse_graph_spec(raw: Any) -> GraphSpec:
    """校验 v2 图 spec，返回 GraphSpec（拓扑序 + lookback 已算好）。"""
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_GRAPH_SPEC_BYTES:
            _fail(f"spec 超过 {MAX_GRAPH_SPEC_BYTES} 字节上限")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            _fail(f"spec 不是合法 JSON：{exc}")
    if not isinstance(raw, dict):
        _fail("spec 必须是 JSON 对象")

    unknown = set(raw) - {
        "version",
        "description",
        "category",
        "position_pct",
        "stop_loss",
        "take_profit",
        "nodes",
        "edges",
        "outputs",
    }
    if unknown:
        _fail(f"spec 含未知字段 {sorted(unknown)}")
    if raw.get("version", GRAPH_VERSION) != GRAPH_VERSION:
        _fail("spec 版本不支持（当前 v1 / v2）")

    nodes_raw = raw.get("nodes")
    edges_raw = raw.get("edges")
    outputs_raw = raw.get("outputs")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        _fail("nodes 必须是非空数组")
    if not isinstance(edges_raw, list):
        _fail("edges 必须是数组")
    if len(nodes_raw) > MAX_NODES:
        _fail(f"节点最多 {MAX_NODES} 个")
    if len(edges_raw) > MAX_EDGES:
        _fail(f"连线最多 {MAX_EDGES} 条")

    # --- 节点 ------------------------------------------------------------- #
    by_id: dict[str, GraphNode] = {}
    for i, item in enumerate(nodes_raw):
        path = f"nodes[{i}]"
        if not isinstance(item, dict):
            _fail(f"{path} 必须是对象")
        nid, ntype = item.get("id"), item.get("type")
        if not isinstance(nid, str) or not _ID_RE.match(nid):
            _fail(f"{path}.id 必须是 ≤24 位的小写字母/数字/下划线")
        if nid in by_id:
            _fail(f"节点 id 重复：{nid}")
        if ntype not in _ALL_TYPES:
            _fail(f"{path}.type 未知节点类型 {ntype!r}")
        # 节点字段白名单：id/type/pos + const.value + 指标 params
        allowed_keys = {"id", "type", "pos"}
        if ntype == "const":
            allowed_keys |= {"value"}
        elif ntype in _INDICATORS:
            allowed_keys |= {"params"}
        extra_keys = set(item) - allowed_keys
        if extra_keys:
            if "params" in extra_keys and ntype not in _INDICATORS and ntype != "const":
                _fail(f"节点 {ntype} 不支持参数")
            _fail(f"{path} 含未支持的字段 {sorted(extra_keys)}")
        pos_raw = item.get("pos")
        pos: dict[str, float] | None = None
        if pos_raw is not None:
            if (
                not isinstance(pos_raw, dict)
                or set(pos_raw) - {"x", "y"}
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pos_raw.values())
            ):
                _fail(f"{path}.pos 必须是只含数字 x/y 的对象")
            pos = {k: float(v) for k, v in pos_raw.items()}
        attrs = _parse_attrs(str(ntype), item, path=path)
        by_id[nid] = GraphNode(id=nid, type=str(ntype), attrs=attrs, pos=pos)

    # --- 连线（类型检查 + 完备性） ---------------------------------------- #
    for i, item in enumerate(edges_raw):
        path = f"edges[{i}]"
        if not isinstance(item, dict):
            _fail(f"{path} 必须是对象")
        unknown = set(item) - {"from", "to", "to_port"}
        if unknown:
            _fail(f"{path} 含未知字段 {sorted(unknown)}")
        src, dst, port_raw = item.get("from"), item.get("to"), item.get("to_port")
        if not isinstance(src, str) or not isinstance(dst, str) or src not in by_id or dst not in by_id:
            _fail(f"{path} 引用了不存在的节点")
        if src == dst:
            _fail(f"{path} 不允许自环")
        node = by_id[dst]
        allowed = _expected_inputs(node.type)
        port = str(port_raw) if port_raw is not None else (next(iter(allowed)) if len(allowed) == 1 else "")
        if port not in allowed:
            _fail(f"{path}.to_port 无效（{node.type} 期望 {sorted(allowed)}）")
        src_kind = _node_output_kind(by_id[src].type)
        if not _kind_ok(src_kind, allowed[port]):
            cn = {"const": "常数", "series": "数值序列", "series|const": "数值", "bool": "布尔"}
            _fail(f"{path}：{src} 输出 {cn[src_kind]}，但 {dst}.{port} 需要 {cn[allowed[port]]}线")
        if port in node.inputs:
            _fail(f"节点 {node.id} 的 {port} 输入连接了多次")
        node.inputs[port] = src

    for node in by_id.values():
        needed = _expected_inputs(node.type)
        missing = sorted(set(needed) - set(node.inputs))
        if missing:
            _fail(f"节点 {node.id}（{node.type}）缺少输入 {missing}")

    # --- 输出 ------------------------------------------------------------- #
    if not isinstance(outputs_raw, dict) or set(outputs_raw) != {"entry", "exit"}:
        _fail("outputs 必须包含 entry 与 exit")
    entry_id, exit_id = outputs_raw.get("entry"), outputs_raw.get("exit")
    for side, oid in (("entry", entry_id), ("exit", exit_id)):
        if not isinstance(oid, str) or oid not in by_id:
            _fail(f"outputs.{side} 指向不存在的节点 {oid!r}")
        if _node_output_kind(by_id[oid].type) != "bool":
            _fail(f"outputs.{side} 必须是布尔节点（比较/交叉/逻辑）")

    # --- 环检测（Kahn） ---------------------------------------------------- #
    indegree = dict.fromkeys(by_id, 0)
    outs: dict[str, list[str]] = {nid: [] for nid in by_id}
    for node in by_id.values():
        for src in node.inputs.values():
            outs[src].append(node.id)
            indegree[node.id] += 1
    queue = [nid for nid, deg in indegree.items() if deg == 0]
    topo: list[str] = []
    while queue:
        nid = queue.pop()
        topo.append(nid)
        for nxt in outs[nid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(topo) != len(by_id):
        _fail("图存在环")

    # --- 可达剪枝 + lookback ---------------------------------------------- #
    reachable: set[str] = set()
    stack = [entry_id, exit_id]
    while stack:
        nid = stack.pop()
        if nid in reachable:
            continue
        reachable.add(nid)
        stack.extend(by_id[nid].inputs.values())

    topo_used = tuple(nid for nid in topo if nid in reachable)
    need: dict[str, int] = {}
    for nid in topo_used:
        node = by_id[nid]
        params = dict(node.attrs)
        if node.type in _INDICATORS:
            own = int(params.get("period", 0))
            if node.type in _MACD_TUP:
                own = int(params.get("slow", 26)) + int(params.get("signal", 9))
        else:
            own = 0
        extra = 1 if node.type in ("cross_above", "cross_below") else 0
        for src in node.inputs.values():
            own = max(own, need.get(src, 0) + extra)
        need[nid] = own

    lookback = max(need.get(str(entry_id), 0), need.get(str(exit_id), 0))

    from .custom import _bounded

    category = raw.get("category", "trend")
    if category not in ("trend", "mean_reversion", "momentum"):
        _fail("category 只支持 trend / mean_reversion / momentum")
    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > 255:
        _fail("description 必须是 ≤255 字符的字符串")

    return GraphSpec(
        nodes=tuple(by_id.values()),  # 保留全部节点（含悬挂），布局信息不丢
        entry_id=str(entry_id),
        exit_id=str(exit_id),
        position_pct=_bounded(raw.get("position_pct", 0.9), "position_pct", 0.01, 1.0),
        stop_loss=_bounded(raw.get("stop_loss", 0.08), "stop_loss", 0.0, 0.95),
        take_profit=_bounded(raw.get("take_profit", 0.0), "take_profit", 0.0, 5.0),
        category=category,
        description=description,
        topological=topo_used,
        lookback=lookback,
    )


# --------------------------------------------------------------------------- #
# 求值
# --------------------------------------------------------------------------- #


def _series_for(frame: pd.DataFrame, node: GraphNode, values: dict[str, Any], index) -> Any:
    """按拓扑序求值单个节点。返回 pd.Series 或标量。"""
    if node.type == "const":
        return pd.Series(node.const_value, index=index)
    if node.type in _SOURCES:
        return frame[node.type]

    def arg(port: str):
        return values[node.inputs[port]]

    if node.type in _INDICATORS:
        params = dict(node.attrs)
        price: pd.Series = arg("in")
        name = node.type
        if name == "sma":
            return ta.sma(price, int(params["period"]))
        if name == "ema":
            return ta.ema(price, int(params["period"]))
        if name == "rsi":
            return ta.rsi(price, int(params["period"]))
        if name == "std":
            return ta.std(price, int(params["period"]))
        if name == "rolling_high":
            return ta.rolling_high(price, int(params["period"]))
        if name == "rolling_low":
            return ta.rolling_low(price, int(params["period"]))
        if name == "momentum":
            return ta.momentum(price, int(params["period"]))
        if name == "atr":
            return ta.atr(frame["high"], frame["low"], frame["close"], int(params["period"]))
        if name in ("boll_lower", "boll_mid", "boll_upper"):
            lower, mid, upper = ta.bollinger(price, int(params["period"]), float(params.get("width", 2.0)))
            return {"boll_lower": lower, "boll_mid": mid, "boll_upper": upper}[name]
        if name in _MACD_TUP:
            dif, dea, hist = ta.macd(
                price, int(params.get("fast", 12)), int(params.get("slow", 26)), int(params.get("signal", 9))
            )
            return {"macd_dif": dif, "macd_dea": dea, "macd_hist": hist}[name]

    if node.type in _ARITH:
        left, right = arg("left"), arg("right")
        if node.type == "add":
            return left + right
        if node.type == "sub":
            return left - right
        if node.type == "mul":
            return left * right
        if isinstance(right, pd.Series):
            return left / right.mask(right.eq(0.0))  # 除零 → NaN → 布尔端判 False
        if right == 0:
            return left * float("nan")
        return left / right

    if node.type in _COMPARE:
        left, right = arg("left"), arg("right")
        if node.type in ("cross_above", "cross_below"):
            cross = ta.crossover(left, right)
            out = cross.eq(1.0) if node.type == "cross_above" else cross.eq(-1.0)
        elif node.type == "gt":
            out = left > right
        elif node.type == "gte":
            out = left >= right
        elif node.type == "lt":
            out = left < right
        else:
            out = left <= right
        return out.fillna(False).astype(bool)

    if node.type in _LOGIC_1:
        return ~arg("in").fillna(False).astype(bool)
    if node.type in _LOGIC_2:
        left, right = arg("left"), arg("right")
        return (left & right) if node.type == "and" else (left | right)

    _fail(f"未知节点类型 {node.type!r}")  # pragma: no cover - 白名单已挡


def graph_signals(frame: pd.DataFrame, spec: GraphSpec) -> tuple[bool, bool]:
    """在 frame（history 窗口）上求值，返回 (entry_last, exit_last)。"""
    values: dict[str, Any] = {}
    for nid in spec.topological:
        node = spec.node(nid)
        values[nid] = _series_for(frame, node, values, frame.index)
    entry = values[spec.entry_id].iloc[-1]
    exit_ = values[spec.exit_id].iloc[-1]
    return bool(entry), bool(exit_)


def graph_lookback(spec: GraphSpec) -> int:
    """最少历史行数：lookback（交叉自身已含 +1）再加 1 根与 v1 条件路径对齐。"""
    return spec.lookback + 1


__all__ = [
    "GRAPH_VERSION",
    "GraphSpec",
    "GraphNode",
    "graph_lookback",
    "graph_signals",
    "parse_graph_spec",
]
