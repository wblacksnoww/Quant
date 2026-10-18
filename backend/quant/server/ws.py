"""WebSocket 事件枢纽。

回测跑在后台线程、纸盘跑在另一个线程，而 WebSocket 在 asyncio 事件循环里。
两个世界的桥就是这里：``publish()`` 可以从任意线程调用，内部用
``loop.call_soon_threadsafe`` 切回循环线程再分发。

另外保留一个环形缓冲，新连接上来先补发最近若干事件，
避免用户打开页面时错过"刚刚成交的那一笔"。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


class EventHub:
    """多用户事件枢纽：**事件按用户定向投递**。

    ``subscribe(user_id=...)`` 只收到"发往该用户的事件 + 公共事件"，
    ``publish(..., user_id=...)`` 的私密事件不会广播给其他人——
    回测进度、纸盘成交这类数据属于发起者，不能给所有登录用户看到。
    公共事件（``user_id=None``，如系统级消息）仍然广播。
    """

    def __init__(self, buffer_size: int = 200) -> None:
        self._queues: dict[asyncio.Queue, int | None] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer: deque[tuple[int | None, dict[str, Any]]] = deque(maxlen=buffer_size)

    # ------------------------------------------------------------------ #
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @staticmethod
    def _visible(event_user: int | None, conn_user: int | None) -> bool:
        if event_user is None:
            return True  # 公共事件
        return conn_user == event_user

    def subscribe(self, *, replay: int = 0, user_id: int | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._queues[queue] = user_id
        if replay:
            items = list(self._buffer)[-replay:]
            for target, item in items:
                if not self._visible(target, user_id):
                    continue
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    break
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._queues.pop(queue, None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._queues)

    # ------------------------------------------------------------------ #
    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        source: str = "",
        *,
        user_id: int | None = None,
    ) -> None:
        """任意线程可调用。``user_id`` 非空时只投递给该用户的连接。"""
        event = {
            "type": event_type,
            "source": source,
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        with self._lock:
            self._buffer.append((user_id, event))
            loop = self._loop
            targets = [(q, u) for q, u in self._queues.items() if self._visible(user_id, u)]
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event, targets)
        except RuntimeError:  # pragma: no cover - 循环已关闭
            pass

    def _fanout(self, event: dict[str, Any], targets: list[tuple[asyncio.Queue, int | None]]) -> None:
        for queue, _user in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.debug("WS 订阅队列已满，丢弃事件")

    def publish_dict(self, data: dict[str, Any]) -> None:
        self.publish(
            str(data.get("type", "event")),
            dict(data.get("payload") or {}),
            str(data.get("source", "")),
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    async def stream(queue: asyncio.Queue, *, timeout: float = 25.0):
        """异步生成器：超时则发送心跳，保持连接不被中间层掐断。"""
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                yield {"type": "ping", "ts": datetime.now(timezone.utc).isoformat(), "payload": {}}
                continue
            yield event


def bus_bridge(
    bus,
    hub: EventHub,
    source: str = "",
    run_id: str = "",
    user_id: int | None = None,
) -> list:
    """把 :class:`~quant.core.events.EventBus` 的所有事件转发到 WebSocket。

    ``user_id`` 非空时事件只发给该用户的连接。
    返回取消订阅的闭包列表。
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


__all__ = ["EventHub", "bus_bridge"]
