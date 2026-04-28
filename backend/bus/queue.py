"""Async message queue for decoupled channel-agent communication."""

import asyncio
import logging

from bus.events import InboundMessage, OutboundMessage

logger = logging.getLogger("navi.bus")

# 队列容量上限，防止 LLM 慢响应时内存无限堆积
_INBOUND_MAX = 100
_OUTBOUND_MAX = 200


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    两个队列均设有容量上限（inbound=100, outbound=200）。
    队列满时拒绝新消息并通知用户，绝不丢弃已排队的旧消息。
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=_INBOUND_MAX)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=_OUTBOUND_MAX)
        self._dropped_count = 0          # 改后应永远是 0
        self._rejected_count = 0         # 用 P95 监控这个

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """
        入站消息发布。
        满载策略：拒绝新消息，保留已排队的旧消息，并通过 outbound 通知用户。
        禁止丢旧消息——用户消息消失是产品级背叛。
        """
        if self.inbound.full():
            self._rejected_count += 1
            # 试图通过 outbound 通知用户「我在忙」
            try:
                self.outbound.put_nowait(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="（消息队列繁忙，刚才那条没收到，请稍后再发一次）",
                ))
            except asyncio.QueueFull:
                pass  # outbound 也满，那真的没辙
            logger.warning(
                "InboundQueue 满 (maxsize=%d)，拒绝新消息 channel=%s chat=%s",
                self.inbound.maxsize, msg.channel, msg.chat_id,
            )
            return  # ← 关键：拒收新的，不动旧的
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels.

        如果队列已满，丢弃最旧的响应后再入队。
        """
        if self.outbound.full():
            try:
                dropped = self.outbound.get_nowait()
                logger.warning(
                    "OutboundQueue 已满（maxsize=%d），丢弃最旧响应: channel=%s",
                    _OUTBOUND_MAX,
                    getattr(dropped, "channel", "?"),
                )
            except asyncio.QueueEmpty:
                pass
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
