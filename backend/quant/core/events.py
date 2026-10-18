"""事件总线。

回测是"推历史"、纸盘是"推实时"，两者对策略/风控/记录器的通知语义应当一致，
所以统一走这里。总线是**同步**的：单线程回测零开销，asyncio 里同步回调也安全；
需要跨线程/跨进程的地方（例如 WebSocket 广播）由订阅方自己塞进队列。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from .types import AccountSnapshot, Bar, Fill, Order, utcnow

log = logging.getLogger(__name__)


class EventType(str, Enum):
    BAR = "bar"
    ORDER = "order"
    FILL = "fill"
    POSITION = "position"
    ACCOUNT = "account"
    EQUITY = "equity"
    RISK = "risk"
    SIGNAL = "signal"
    LOG = "log"
    STATUS = "status"


@dataclass(slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "ts": self.ts.isoformat(),
            "source": self.source,
            "payload": self.payload,
        }


Handler = Callable[[Event], None]


class EventBus:
    """极简发布/订阅。

    * 订阅返回一个取消订阅的闭包，方便引擎在 ``finally`` 里清理。
    * 单个订阅者抛异常不会打断其它订阅者，也不会打断引擎主循环。
    """

    def __init__(self, name: str = "bus") -> None:
        self.name = name
        self._subs: dict[EventType, list[Handler]] = defaultdict(list)
        self._global: list[Handler] = []
        self._published = 0

    # -- 订阅 -------------------------------------------------------------- #
    def subscribe(self, event_type: EventType, handler: Handler) -> Callable[[], None]:
        self._subs[event_type].append(handler)
        return lambda: self._remove(self._subs[event_type], handler)

    def subscribe_all(self, handler: Handler) -> Callable[[], None]:
        self._global.append(handler)
        return lambda: self._remove(self._global, handler)

    @staticmethod
    def _remove(bucket: list[Handler], handler: Handler) -> None:
        try:
            bucket.remove(handler)
        except ValueError:
            pass

    def clear(self) -> None:
        self._subs.clear()
        self._global.clear()

    @property
    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subs.values()) + len(self._global)

    @property
    def published_count(self) -> int:
        return self._published

    # -- 发布 -------------------------------------------------------------- #
    def publish(self, event: Event) -> None:
        self._published += 1
        for handler in (*self._subs.get(event.type, ()), *self._global):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - 订阅者故障不应影响主流程
                log.exception(
                    "事件订阅者异常 bus=%s type=%s handler=%s",
                    self.name,
                    event.type.value,
                    getattr(handler, "__qualname__", handler),
                )

    # -- 便捷发布 ---------------------------------------------------------- #
    def emit(self, type_: EventType, payload: dict[str, Any], source: str = "") -> None:
        self.publish(Event(type=type_, payload=payload, source=source))

    def emit_bar(self, bar: Bar, source: str = "") -> None:
        self.emit(EventType.BAR, bar.to_dict(), source)

    def emit_order(self, order: Order, source: str = "") -> None:
        self.emit(EventType.ORDER, order.to_dict(), source)

    def emit_fill(self, fill: Fill, source: str = "") -> None:
        self.emit(EventType.FILL, fill.to_dict(), source)

    def emit_account(self, snap: AccountSnapshot, source: str = "") -> None:
        self.emit(EventType.EQUITY, snap.to_dict(), source)

    def emit_log(self, message: str, level: str = "info", source: str = "") -> None:
        self.emit(EventType.LOG, {"message": message, "level": level}, source)

    def emit_status(self, status: str, detail: str = "", source: str = "") -> None:
        self.emit(EventType.STATUS, {"status": status, "detail": detail}, source)


class EventRecorder:
    """把事件攒进列表，用于回测报告与"事件回放"式排查。

    ``limit`` 防止长跑纸盘把内存吃满（超出后丢弃最旧的事件）。
    """

    def __init__(self, bus: EventBus, limit: int = 10_000, types: tuple[EventType, ...] = ()):
        self.limit = limit
        self._types = set(types)
        self.events: list[Event] = []
        self._unsubscribe = bus.subscribe_all(self._on_event)

    def _on_event(self, event: Event) -> None:
        if self._types and event.type not in self._types:
            return
        self.events.append(event)
        if len(self.events) > self.limit:
            del self.events[: len(self.events) - self.limit]

    def close(self) -> None:
        self._unsubscribe()

    def __enter__(self) -> EventRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["Event", "EventBus", "EventRecorder", "EventType"]
