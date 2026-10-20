"""把引擎事件总线桥接到 WebSocket 事件中心（框架无关）。"""

from __future__ import annotations

from typing import Any


def bus_bridge(
    bus: Any,
    hub: Any,
    source: str = "",
    run_id: str = "",
    user_id: int | None = None,
) -> list:
    """把 :class:`~quant.core.events.EventBus` 的所有事件转发到 WebSocket。

    ``user_id`` 非空时事件只发给该用户的连接。返回取消订阅的闭包列表。
    """
    from ..core.events import EventType

    unwire = []
    for event_type in EventType:

        def handler(event, _t=event_type):
            payload = dict(event.payload)
            if run_id:
                payload["run_id"] = run_id
            hub.publish(f"run.{event.type.value}", payload, event.source or source, user_id=user_id)

        unwire.append(bus.subscribe(event_type, handler))
    return unwire


__all__ = ["bus_bridge"]
