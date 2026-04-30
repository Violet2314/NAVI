"""
dispatcher.py — 分发器：推送到各通道 + 写入会话历史

v4: 和普通对话完全一致的消息格式。
- 用 save_message 写 DB（和普通聊天同一个函数）
- 广播格式用 reply（和普通聊天一样，Live2D 能识别）
- TTS 走同样的流程
"""
import asyncio
import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from proactive.triggers import Trigger
from proactive.constants import PROACTIVE_SESSION_KEY, PROACTIVE_DB_SESSION_ID

if TYPE_CHECKING:
    from channels.local_ws import LocalWSChannel
    from session.manager import SessionManager

logger = logging.getLogger("navi.proactive.dispatcher")


def _strip_markdown(text: str) -> str:
    """去除 markdown 格式，提取纯文本用于 TTS"""
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'[*_~#>\-\[\]()!|]', '', text)
    text = re.sub(r'\n{2,}', '。', text)
    return text.strip()


class Dispatcher:
    """
    主动消息分发器。

    v4 设计：和普通对话完全一致。
    1. 用 save_message 写入 DB（和普通聊天同一个函数，同时更新 chat_sessions）
    2. 广播格式用 type="reply"（Live2D 能识别、ChatPage 也能处理）
    3. TTS 走同样的流程
    """

    def __init__(
        self,
        ws_channel: Optional["LocalWSChannel"],
        session_manager: Optional["SessionManager"],
        db_path: str = "",
    ):
        self.ws_channel = ws_channel
        self.session_manager = session_manager
        self.db_path = db_path

    async def dispatch(
        self,
        message: str,
        trigger: Trigger,
        strategy: str,
        emotion: Optional[str] = None,
    ) -> str:
        """
        分发主动消息到所有通道。
        返回 event_id（用于反馈跟踪）。
        """
        event_id = f"proactive_{uuid.uuid4().hex[:12]}"

        # ── 1. 写入 DB（和普通聊天完全一样的 save_message）──
        self._persist_to_db(message)

        # ── 2. WebSocket 广播（格式和普通聊天的 reply 完全一致）──
        await self._broadcast_ws(message, trigger, strategy, emotion, event_id)

        # ── 3. 写入 Session 会话历史（让 AgentLoop 知道上文）──
        self._write_session(message, trigger, strategy, event_id)

        # ── 4. TTS 语音合成（后台并行，不阻塞）──
        asyncio.create_task(self._do_tts(message, event_id))

        # ── 5. 微信推送（如果在线）──
        asyncio.create_task(self._push_wechat(message, trigger, event_id))

        logger.info(
            f"Dispatched: event_id={event_id}, trigger={trigger.type}, "
            f"strategy={strategy}, text={message[:40]}..."
        )
        return event_id

    def _persist_to_db(self, message: str):
        """用 save_message 写入 DB，和普通聊天完全一致"""
        try:
            from api.chat_routes import save_message
            save_message(PROACTIVE_DB_SESSION_ID, "assistant", message)
            logger.debug(f"DB saved to session={PROACTIVE_DB_SESSION_ID}")
        except Exception as e:
            logger.warning(f"DB persist failed: {e}")

    async def _broadcast_ws(
        self,
        message: str,
        trigger: Trigger,
        strategy: str,
        emotion: Optional[str],
        event_id: str,
    ):
        """
        通过 WebSocket 广播。
        格式用 type="reply"，和普通聊天一致，这样 Live2D 和 ChatPage 都能处理。
        """
        try:
            from bus.broadcaster import get_broadcaster
            broadcaster = get_broadcaster()

            # 和普通聊天的 reply 格式一致
            payload = {
                "type": "reply",
                "content": message,
                "emotion": emotion,
                "chat_id": PROACTIVE_DB_SESSION_ID,
                # 额外字段标识这是主动对话（不影响现有逻辑）
                "proactive": True,
                "event_id": event_id,
                "trigger_type": trigger.type,
                "strategy": strategy,
            }
            await broadcaster.broadcast(payload)
            logger.debug(f"WS broadcast sent: {event_id}")

        except Exception as e:
            logger.warning(f"WS broadcast failed (non-fatal): {e}")

    async def _do_tts(self, message: str, event_id: str):
        """TTS 合成并广播音频"""
        try:
            from tts.manager import get_tts_manager
            tts_mgr = get_tts_manager()
            if not tts_mgr.enabled or not message.strip():
                return

            tts_text = _strip_markdown(message)
            if not tts_text:
                return

            tts_audio_b64 = await tts_mgr.synthesize_to_base64(tts_text)

            from bus.broadcaster import get_broadcaster
            broadcaster = get_broadcaster()
            await broadcaster.broadcast({
                "type": "tts_audio",
                "audio": tts_audio_b64,
                "format": "mp3",
                "chat_id": PROACTIVE_DB_SESSION_ID,
                "event_id": event_id,
            })
            logger.info(f"[TTS] Proactive TTS sent ({len(tts_audio_b64) // 1024}KB)")

        except Exception as e:
            logger.warning(f"[TTS] Proactive TTS failed (non-fatal): {e}")

    def _write_session(
        self,
        message: str,
        trigger: Trigger,
        strategy: str,
        event_id: str,
    ):
        """写入 Session 会话历史，让用户回复时 AgentLoop 能看到上文"""
        if not self.session_manager:
            return
        try:
            session = self.session_manager.get_or_create(PROACTIVE_SESSION_KEY)
            session.add_message(
                role="assistant",
                content=message,
                proactive=True,
                event_id=event_id,
                trigger_type=trigger.type,
                strategy=strategy,
            )
            self.session_manager.save(session)
            logger.debug(f"Session updated: {PROACTIVE_SESSION_KEY}")
        except Exception as e:
            logger.warning(f"Session write failed (non-fatal): {e}")

    async def _push_wechat(self, message: str, trigger: Trigger, event_id: str):
        """如果微信在线，将主动消息推送给最近活跃的微信用户"""
        try:
            from channels.wechat import WeChatChannel, WECHAT_DB_SESSION_ID
            wechat = WeChatChannel.get_instance()
            if not wechat:
                logger.debug("[WeChat] 主动推送跳过：get_instance() 返回 None")
                return
            if not wechat.is_online:
                logger.debug(
                    "[WeChat] 主动推送跳过：is_online=False "
                    "(running=%s, token=%s)",
                    wechat._running, bool(wechat._token),
                )
                return

            from api.chat_routes import save_message, ensure_session
            ensure_session(WECHAT_DB_SESSION_ID, title="微信对话")
            # DB 里保留标记（前端展示用），发给微信的是干净文本
            save_message(WECHAT_DB_SESSION_ID, "assistant", f"[主动对话] {message}")

            # 广播让前端刷新
            try:
                from bus.broadcaster import get_broadcaster
                broadcaster = get_broadcaster()
                await broadcaster.broadcast({
                    "type": "wechat_message",
                    "role": "assistant",
                    "content": f"[主动对话] {message}",
                    "chat_id": WECHAT_DB_SESSION_ID,
                })
                # 同步广播 reply 给 Live2D / TTS
                clean_msg = " ".join(
                    s.strip() for s in message.split("[SPLIT]") if s.strip()
                ) or message
                await broadcaster.broadcast({
                    "type": "reply",
                    "content": clean_msg,
                    "emotion": None,
                    "chat_id": WECHAT_DB_SESSION_ID,
                    "source": "wechat_proactive",
                })
                try:
                    from tts.manager import get_tts_manager
                    from channels.local_ws import _strip_markdown  # type: ignore
                    tts_mgr = get_tts_manager()
                    if tts_mgr.enabled and clean_msg.strip():
                        tts_text = _strip_markdown(clean_msg)
                        tts_audio_b64 = await tts_mgr.synthesize_to_base64(tts_text)
                        await broadcaster.broadcast({
                            "type": "tts_audio",
                            "audio": tts_audio_b64,
                            "format": "mp3",
                            "chat_id": WECHAT_DB_SESSION_ID,
                        })
                except Exception:
                    pass
            except Exception:
                pass

            # 发到微信：不带 [主动对话] 前缀，自动按 [SPLIT] 拆段
            try:
                await wechat.send_to_last_user(message)
            except Exception as send_err:
                logger.warning(
                    f"[WeChat] 主动对话发送失败（iLink 业务错误）event_id={event_id}: {send_err}"
                )
                return
            logger.info(f"[WeChat] 主动对话已推送: {event_id}")

        except Exception as e:
            logger.warning(f"[WeChat] 主动对话推送失败 (non-fatal): {e}")
