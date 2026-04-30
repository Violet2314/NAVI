"""
EventBroadcaster — 全局事件广播器

让后端任何模块（日报、黑名单、AgentLoop...）都能推送事件到所有 WebSocket 客户端。
Live2D 伴侣窗口通过 WebSocket 接收事件后触发表情/动作。

事件类型：
- navi:report_done   — 日报生成完成
- navi:blacklist_kill — 黑名单击杀
- navi:emotion       — 直接触发情绪
- navi:message       — 通用消息（文字气泡）
"""
import asyncio
import json
import logging
from typing import Any, Dict, Set

logger = logging.getLogger("navi.broadcaster")


class EventBroadcaster:
    """
    全局单例，管理所有 WebSocket 连接并广播事件。

    用法：
        broadcaster = get_broadcaster()

        # WebSocket 连接时注册
        broadcaster.register(websocket)

        # 任何模块发送事件
        await broadcaster.broadcast({
            "type": "navi:blacklist_kill",
            "process": "wechat.exe",
            "emotion": "angry",
        })
    """

    def __init__(self):
        self._connections: Set = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """设置事件循环（main.py 启动时调用）"""
        self._loop = loop

    async def register(self, ws):
        """注册一个 WebSocket 连接"""
        async with self._lock:
            self._connections.add(ws)
        logger.info(f"[Broadcaster] 注册连接，当前 {len(self._connections)} 个")

    async def unregister(self, ws):
        """注销一个 WebSocket 连接"""
        async with self._lock:
            self._connections.discard(ws)
        logger.info(f"[Broadcaster] 注销连接，当前 {len(self._connections)} 个")

    async def broadcast(self, event: Dict[str, Any], *, exclude=None):
        """
        向所有连接的 WebSocket 广播一个事件，可排除指定连接。

        优化：lock 只用于读取连接集合的快照，发送在 lock 外并发执行，
        每个连接单独设 5 秒超时，避免单个慢连接阻塞全部广播。
        """
        message = json.dumps(event, ensure_ascii=False)

        # 只在 lock 内做快照，不在 lock 内做 await
        async with self._lock:
            targets = [
                ws for ws in self._connections
                if exclude is None or ws is not exclude
            ]

        if not targets:
            return

        dead: set = set()

        # tts_audio payload 体积较大（base64 编码音频），给更长的超时
        # 普通消息 2 秒，tts_audio 30 秒
        _timeout = 30.0 if event.get("type") == "tts_audio" else 2.0

        async def _send_one(ws):
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=_timeout)
            except Exception:
                dead.add(ws)

        await asyncio.gather(*[_send_one(ws) for ws in targets])

        # 清理断开的连接
        if dead:
            async with self._lock:
                self._connections -= dead
            logger.info(f"[Broadcaster] 清理 {len(dead)} 个断开的连接")

        logger.info(f"[Broadcaster] 广播事件 type={event.get('type','?')} 到 {len(targets)} 个连接 (排除={exclude is not None})")

    def broadcast_sync(self, event: Dict[str, Any]):
        """
        从同步代码（如 BlacklistMonitor 的线程）广播事件。
        内部使用 asyncio.run_coroutine_threadsafe 跨线程调度。
        """
        if not self._loop or self._loop.is_closed():
            logger.warning("[Broadcaster] 事件循环未就绪，跳过广播")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.broadcast(event), self._loop
            )
            # 不阻塞等待结果，fire-and-forget
        except Exception as e:
            logger.warning(f"[Broadcaster] 同步广播失败: {e}")


# ── 全局单例 ──────────────────────────────────────────────────────────────

_broadcaster: EventBroadcaster | None = None


def get_broadcaster() -> EventBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = EventBroadcaster()
    return _broadcaster
