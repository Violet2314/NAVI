"""
OutboundRouter - 唯一出口分发器。
订阅 bus.outbound，根据 channel 字段路由到具体 channel 实现。
"""
import asyncio
import logging
from typing import Awaitable, Callable, Dict

from bus.events import OutboundMessage
from bus.queue import get_bus

logger = logging.getLogger("navi.bus.router")

DeliverFn = Callable[[OutboundMessage], Awaitable[None]]


class OutboundRouter:
    def __init__(self):
        self._handlers: Dict[str, DeliverFn] = {}
        self._task: asyncio.Task | None = None

    def register(self, channel: str, deliver: DeliverFn) -> None:
        if channel in self._handlers:
            logger.warning("Channel %s 重复注册，覆盖", channel)
        self._handlers[channel] = deliver

    async def start(self) -> None:
        if self._task is not None:
            return
        bus = get_bus()
        self._task = asyncio.create_task(self._dispatch_loop(bus), name="outbound-router")

    async def _dispatch_loop(self, bus) -> None:
        while True:
            msg: OutboundMessage = await bus.outbound.get()
            handler = self._handlers.get(msg.channel)
            if not handler:
                logger.error("无 channel 处理器: %s（消息丢失：%s）", msg.channel, msg.content[:50])
                continue
            try:
                await handler(msg)
            except Exception as e:
                logger.exception("Channel %s 投递失败: %s", msg.channel, e)


_router: OutboundRouter | None = None


def get_router() -> OutboundRouter:
    global _router
    if _router is None:
        _router = OutboundRouter()
    return _router
