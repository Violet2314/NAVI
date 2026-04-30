# 快速测试 AgentLoop 端到端
# 用法: cd D:/learn/Navi/backend && uv run python scripts/tests/test_agent_loop.py
import asyncio
import sys
import os
import traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")


async def main():
    from agent.loop import AgentLoop
    from agent.navi_provider import NaviLLMProvider
    from bus.queue import MessageBus
    from bus.events import InboundMessage

    workspace = Path(__file__).parent.parent.parent

    provider = NaviLLMProvider()
    bus = MessageBus()

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        max_iterations=5,
    )

    print("=" * 50)
    print("  测试 1: 简单问答")
    print("=" * 50)
    msg = InboundMessage(
        channel="cli",
        chat_id="test",
        sender_id="user",
        content="你好，请用一句话介绍你自己。",
    )
    try:
        response = await asyncio.wait_for(loop._process_message(msg), timeout=60)
        print("  回复:", (response.content or "")[:300] if response else "(无回复)")
    except Exception:
        traceback.print_exc()

    print()
    print("=" * 50)
    print("  测试 2: 工具调用（列目录）")
    print("=" * 50)
    msg2 = InboundMessage(
        channel="cli",
        chat_id="test",
        sender_id="user",
        content="用 list_dir 工具列出当前工作目录下的文件",
    )
    try:
        response2 = await asyncio.wait_for(loop._process_message(msg2), timeout=60)
        print("  回复:", (response2.content or "")[:300] if response2 else "(无回复)")
    except Exception:
        traceback.print_exc()

    print()
    print("OK - AgentLoop 测试完成")


if __name__ == "__main__":
    asyncio.run(main())