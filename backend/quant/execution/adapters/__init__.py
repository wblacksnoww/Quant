"""实盘适配器包。"""

from .base import AdapterBalance, AdapterError, AdapterFill, AdapterStatus, BrokerAdapter
from .ccxt_adapter import CcxtAdapter
from .miniqmt_adapter import MiniQmtAdapter

ADAPTERS: dict[str, type[BrokerAdapter]] = {
    CcxtAdapter.name: CcxtAdapter,
    MiniQmtAdapter.name: MiniQmtAdapter,
}


def create_adapter(name: str, *, sandbox: bool = False, **config) -> BrokerAdapter:
    key = (name or "").strip().lower()
    cls = ADAPTERS.get(key)
    if cls is None:
        raise ValueError(f"未知实盘适配器 {name!r}，可用：{sorted(ADAPTERS)}")
    return cls(sandbox=sandbox, **config)


__all__ = [
    "ADAPTERS",
    "AdapterBalance",
    "AdapterError",
    "AdapterFill",
    "AdapterStatus",
    "BrokerAdapter",
    "CcxtAdapter",
    "MiniQmtAdapter",
    "create_adapter",
]
