"""
P0-A 验证：bus 不再静默丢消息。

测试策略：
  1. 满载时拒绝新消息（不丢旧的）
  2. 满载时通过 outbound 通知用户
  3. 正常流转不受影响
"""
import asyncio
import pytest
from datetime import datetime

from bus.events import InboundMessage, OutboundMessage
from bus.queue import MessageBus


def make_inbound(content: str = "hello", channel: str = "local_ws", chat_id: str = "test") -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id="test_user",
        chat_id=chat_id,
        content=content,
        timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_reject_when_full_preserves_old_messages():
    """
    满载时拒绝新消息，保留已排队的旧消息。
    """
    bus = MessageBus()
    # 手动塞满 inbound（maxsize=100）
    for i in range(100):
        bus.inbound.put_nowait(make_inbound(content=f"msg_{i}"))

    assert bus.inbound.full()

    # 尝试再发一条
    await bus.publish_inbound(make_inbound(content="should_be_rejected"))

    # 队列应该仍然是满的（100 条），新消息被拒绝
    assert bus.inbound.qsize() == 100
    # 第一条应该还是 msg_0，没有被丢弃
    first = bus.inbound.get_nowait()
    assert first.content == "msg_0"
    # 拒绝计数器应该 +1
    assert bus._rejected_count == 1
    # dropped 计数器应该永远是 0
    assert bus._dropped_count == 0


@pytest.mark.asyncio
async def test_reject_sends_outbound_notification():
    """
    满载拒绝时，通过 outbound 通知用户。
    """
    bus = MessageBus()
    # 塞满 inbound
    for i in range(100):
        bus.inbound.put_nowait(make_inbound(content=f"msg_{i}"))

    await bus.publish_inbound(make_inbound(content="should_be_rejected"))

    # outbound 应该有一条通知
    assert bus.outbound.qsize() == 1
    notification = bus.outbound.get_nowait()
    assert "繁忙" in notification.content or "稍后" in notification.content


@pytest.mark.asyncio
async def test_normal_flow_unchanged():
    """
    正常流转：单条 inbound 正常入队。
    """
    bus = MessageBus()
    msg = make_inbound(content="hello")
    await bus.publish_inbound(msg)

    assert bus.inbound.qsize() == 1
    consumed = await bus.consume_inbound()
    assert consumed.content == "hello"


@pytest.mark.asyncio
async def test_bulk_publish_no_exception():
    """
    大量发布不抛异常，所有 outbound 通知都收到。
    """
    bus = MessageBus()
    # 塞满
    for i in range(100):
        bus.inbound.put_nowait(make_inbound(content=f"msg_{i}"))

    # 再发 50 条，全部应该被拒绝，不抛异常
    for i in range(50):
        await bus.publish_inbound(make_inbound(content=f"extra_{i}"))

    assert bus._rejected_count == 50
    assert bus._dropped_count == 0
    # outbound 应该有 50 条通知（或接近，取决于 outbound 是否满）
    assert bus.outbound.qsize() > 0
