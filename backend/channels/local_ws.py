"""
本地 WebSocket 频道 — Navi 的 WebSocket 对话接口。
前端通过 ws://localhost:8000/ws/chat?chat_id=<session_id> 连接，直接和 AgentLoop 对话。
支持：
- 多会话（chat_id 即 session_id，由前端生成 UUID）
- 历史持久化（每条消息写入 SQLite chat_messages 表）
- 伪流式输出（回复按字/词分批推送，视觉上逐字显示）
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from bus.events import InboundMessage, OutboundMessage
from bus.broadcaster import get_broadcaster

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger("navi.ws_channel")

# 伪流式：每批推送的字符数和间隔（毫秒）
_STREAM_CHUNK = 4        # 每批推送字符数
_STREAM_DELAY = 0.015    # 批间延迟（秒）


def _strip_markdown(text: str) -> str:
    """清理 Markdown 格式，让 TTS 不会读出 ** _ ` 等符号。"""
    # 移除代码块 ```...```
    text = re.sub(r'```[\s\S]*?```', '', text)
    # 移除行内代码 `...`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 移除粗体 **...**
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    # 移除斜体 _..._ 或 *...*
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    # 移除标题 # ## ###
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 移除链接 [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 移除图片 ![alt](url)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    # 移除引用 >
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # 移除水平线 --- / ***
    text = re.sub(r'^[-*]{3,}$', '', text, flags=re.MULTILINE)
    # 移除无序列表符号 - * + 
    text = re.sub(r'^[\-\*\+]\s+', '', text, flags=re.MULTILINE)
    # 移除有序列表数字 1. 2. 
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def _pseudo_stream(websocket: "WebSocket", content: str):
    """把完整回复内容分批推送，模拟流式输出体验。"""
    for i in range(0, len(content), _STREAM_CHUNK):
        chunk = content[i: i + _STREAM_CHUNK]
        await websocket.send_text(json.dumps({
            "type": "stream",
            "delta": chunk,
        }))
        await asyncio.sleep(_STREAM_DELAY)
    # 推送完成信号
    await websocket.send_text(json.dumps({"type": "stream_end"}))


class LocalWSChannel:
    """
    本地 WebSocket 频道。
    每个 WebSocket 连接对应一个独立会话，支持多连接并发。
    """

    def __init__(self, bus, agent_loop):
        self.bus = bus
        self.agent_loop = agent_loop
        self._connections: dict[str, "WebSocket"] = {}   # chat_id → ws

    async def handle(self, websocket: "WebSocket", chat_id: str = "local"):
        """处理单个 WebSocket 连接的完整生命周期。"""
        from fastapi.websockets import WebSocketDisconnect
        from api.chat_routes import save_message, ensure_session

        await websocket.accept()
        self._connections[chat_id] = websocket
        # 注册到全局广播器，让后端事件能推送到这个连接
        broadcaster = get_broadcaster()
        await broadcaster.register(websocket)
        logger.info(f"[WS] 连接建立 chat_id={chat_id}")

        # 确保会话存在（前端直接用 UUID 连接时自动创建）
        try:
            ensure_session(chat_id, title="新对话")
        except Exception as e:
            logger.debug(f"[WS] ensure_session 失败（跳过）: {e}")

        try:
            while True:
                raw = await websocket.receive_text()
                data = json.loads(raw)
                content = data.get("content", "").strip()
                # 前端发来的图片列表（base64 data URL，如 "data:image/png;base64,..."）
                frontend_images: list[str] = data.get("images") or []
                if not content and not frontend_images:
                    continue

                # ── 持久化用户消息 ──────────────────────────────
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, save_message, chat_id, "user", content)
                except Exception as e:
                    logger.debug(f"[WS] 保存用户消息失败: {e}")

                # 发送"正在思考"状态
                await websocket.send_text(json.dumps({
                    "type": "thinking",
                    "content": "Navi 正在思考...",
                }))

                # 进度回调：工具调用时实时推送
                async def on_progress(text: str, *, tool_hint: bool = False):
                    await websocket.send_text(json.dumps({
                        "type": "tool_hint" if tool_hint else "progress",
                        "content": text,
                    }))

                # ── 注入记忆上下文（Phase 1+2 混合检索，L3 Facts 已在 system prompt 中注入）──
                memory_ctx = ""
                try:
                    # L2 + episodic: 混合检索（Phase 1+2，按相关性）
                    from memory.episodic_memory import get_episodic_memory, should_retrieve
                    if should_retrieve(content):
                        _em = get_episodic_memory()
                        _ctx = _em.to_context_text(content, n=3)
                        if _ctx:
                            memory_ctx += (
                                "【相关历史记忆，仅供参考，"
                                "请勿将其视为用户当前的发言内容】\n"
                                + _ctx + "\n\n"
                            )
                    else:
                        logger.info(f"[WS] 意图过滤：跳过记忆检索 (query={content[:20]})")
                except Exception as _e:
                    logger.info(f"[WS] 记忆加载失败: {_e}")

                # ── 前端图片：data URL → media list（仅保留 base64 部分供 LLM vision 使用）──
                media_list: list[str] = []
                for img_data_url in frontend_images:
                    # data URL 格式：data:image/png;base64,<base64data>
                    if img_data_url.startswith("data:"):
                        media_list.append(img_data_url)
                    else:
                        media_list.append(img_data_url)

                # 构造入站消息 → AgentLoop 处理
                msg = InboundMessage(
                    channel="local_ws",
                    chat_id=chat_id,
                    sender_id="user",
                    content=(memory_ctx + "【用户消息】\n" + content) if memory_ctx else content,
                    media=media_list,
                )
                # ── 拦截 message 工具的 _send_callback，收集 media 和文字 ──
                _media_to_push: list[str] = []  # 收集 message 工具附带的 media 路径
                _tool_sent_content: list[str] = []  # 收集 message 工具发送的文字内容

                _msg_tool = self.agent_loop.tools.get("message")
                _original_cb = getattr(_msg_tool, "_send_callback", None) if _msg_tool else None

                async def _intercepted_send(out_msg: OutboundMessage) -> None:
                    """拦截 message 工具的发送，收集 media 和文字内容。"""
                    if out_msg.media:
                        # media 可能是 list[str] 或被 LLM 传成了 str
                        if isinstance(out_msg.media, list):
                            _media_to_push.extend(out_msg.media)
                        elif isinstance(out_msg.media, str):
                            # LLM 有时把 JSON 数组传成字符串
                            try:
                                parsed = json.loads(out_msg.media)
                                if isinstance(parsed, list):
                                    _media_to_push.extend(parsed)
                            except Exception:
                                _media_to_push.append(out_msg.media)
                    if out_msg.content:
                        _tool_sent_content.append(out_msg.content)
                    # 不调用原始 callback，因为 outbound 队列没人消费

                if _msg_tool and hasattr(_msg_tool, "_send_callback"):
                    _msg_tool._send_callback = _intercepted_send

                try:
                    response = await self.agent_loop._process_message(
                        msg, on_progress=on_progress
                    )
                finally:
                    # 恢复原始 callback
                    if _msg_tool and _original_cb is not None:
                        _msg_tool._send_callback = _original_cb

                # ── 收集所有 media 路径（来自 response 或拦截到的 message 工具）──
                all_media_paths: list[str] = list(_media_to_push)
                if response and getattr(response, "media", None):
                    all_media_paths.extend(response.media)

                # ── response=None 说明 message 工具已经发了文字，
                #    但图片还没推给前端，这里补推图片即可 ──────────
                if response is None:
                    # message 工具的文字 + 图片都需要推给前端
                    # 先持久化图片到 media 目录
                    _persisted_filenames: list[str] = []
                    if all_media_paths:
                        from utils.media import persist_images
                        _persisted_filenames = await loop.run_in_executor(
                            None, persist_images, all_media_paths
                        )

                    # 流式推送文字内容
                    tool_content = "\n".join(_tool_sent_content).strip()
                    if tool_content:
                        # 解析情绪标签
                        _em_match = re.search(r'\[EMOTION:(\w+)\]', tool_content, re.IGNORECASE)
                        _em_tag = _em_match.group(1).lower() if _em_match else None
                        _clean = re.sub(r'\s*\[EMOTION:\w+\]', '', tool_content, flags=re.IGNORECASE).strip()
                        if _em_tag:
                            logger.info(f"[WS] 解析到情绪标签(message tool): {_em_tag}")
                        # 持久化消息（存文件名而非绝对路径）
                        try:
                            await loop.run_in_executor(
                                None, save_message, chat_id, "assistant", _clean,
                                None, _persisted_filenames or None,
                            )
                        except Exception:
                            pass
                        # 流式推送
                        await websocket.send_text(json.dumps({
                            "type": "reply_start",
                            "emotion": _em_tag,
                            "chat_id": chat_id,
                        }))
                        await _pseudo_stream(websocket, _clean)
                        # 广播给其他连接
                        await broadcaster.broadcast({
                            "type": "reply", "content": _clean,
                            "emotion": _em_tag, "chat_id": chat_id,
                        }, exclude=websocket)
                        # TTS
                        _tts_clean = _clean
                        async def _do_tts_tool():
                            try:
                                from tts.manager import get_tts_manager
                                tts_mgr = get_tts_manager()
                                if tts_mgr.enabled and _tts_clean.strip():
                                    tts_text = _strip_markdown(_tts_clean)
                                    tts_audio_b64 = await tts_mgr.synthesize_to_base64(tts_text)
                                    await websocket.send_text(json.dumps({
                                        "type": "tts_audio", "audio": tts_audio_b64,
                                        "format": "mp3", "chat_id": chat_id,
                                    }))
                            except Exception:
                                pass
                        asyncio.create_task(_do_tts_tool())

                    # 推送图片（/media/filename 相对路径，前端拼 API base）
                    if _persisted_filenames:
                        image_urls = [f"/media/{fn}" for fn in _persisted_filenames]
                        await websocket.send_text(json.dumps({
                            "type": "reply_images",
                            "images": image_urls,
                            "chat_id": chat_id,
                        }))
                        logger.info(f"[WS] 推送 {len(image_urls)} 张图片给前端: {image_urls}")
                    continue  # message 工具已处理完，跳过后面的流式推送

                # ── 解析 LLM 结构化情绪标签 [EMOTION:xxx] ──────────────────
                raw_content = response.content if response else ""
                emotion_match = re.search(r'\[EMOTION:(\w+)\]', raw_content, re.IGNORECASE)
                emotion_tag = emotion_match.group(1).lower() if emotion_match else None
                # 去掉标签，保持正文干净
                clean_content = re.sub(r'\s*\[EMOTION:\w+\]', '', raw_content, flags=re.IGNORECASE).strip()
                if emotion_tag:
                    logger.info(f"[WS] 解析到情绪标签: {emotion_tag}")

                # ── 持久化 assistant 消息 ────────────────────────
                try:
                    await loop.run_in_executor(None, save_message, chat_id, "assistant", clean_content)
                except Exception as e:
                    logger.debug(f"[WS] 保存 assistant 消息失败: {e}")

                # ── 伪流式推送回复 ───────────────────────────────
                # 先发元信息（情绪等），再流式推送内容
                await websocket.send_text(json.dumps({
                    "type": "reply_start",
                    "emotion": emotion_tag,
                    "chat_id": chat_id,
                }))
                await _pseudo_stream(websocket, clean_content)

                # ── 推送 Navi 回复中的附图（持久化 + /media/filename）──
                if all_media_paths:
                    from utils.media import persist_images as _persist2
                    _fns2 = await loop.run_in_executor(None, _persist2, all_media_paths)
                    if _fns2:
                        image_urls2 = [f"/media/{fn}" for fn in _fns2]
                        await websocket.send_text(json.dumps({
                            "type": "reply_images",
                            "images": image_urls2,
                            "chat_id": chat_id,
                        }))
                        logger.info(f"[WS] 推送 {len(image_urls2)} 张图片给前端: {image_urls2}")

                # ── 广播给其他连接（Live2D companion 等）────────
                # 先广播文字内容，不等 TTS
                reply_payload = {
                    "type": "reply",
                    "content": clean_content,
                    "emotion": emotion_tag,
                    "chat_id": chat_id,
                }
                await broadcaster.broadcast(reply_payload, exclude=websocket)

                logger.info(f"Response to {chat_id}: {clean_content[:60]}…")

                # ── TTS 语音合成（后台并行，不阻塞）──────────────
                async def _do_tts():
                    try:
                        from tts.manager import get_tts_manager
                        tts_mgr = get_tts_manager()
                        if tts_mgr.enabled and clean_content.strip():
                            tts_text = _strip_markdown(clean_content)
                            tts_audio_b64 = await tts_mgr.synthesize_to_base64(tts_text)
                            await websocket.send_text(json.dumps({
                                "type": "tts_audio",
                                "audio": tts_audio_b64,
                                "format": "mp3",
                                "chat_id": chat_id,
                            }))
                            logger.info(f"[WS] TTS audio sent ({len(tts_audio_b64)//1024}KB)")
                    except Exception as tts_err:
                        logger.warning(f"[WS] TTS 合成失败（不影响对话）: {tts_err}")

                asyncio.create_task(_do_tts())  # 后台并行执行

        except Exception as e:
            logger.info(f"[WS] 连接断开 chat_id={chat_id}: {e}")
        finally:
            self._connections.pop(chat_id, None)
            await get_broadcaster().unregister(websocket)
            # Phase 3: WS 断开时将会话加入记忆提取队列（异步，< 1ms）
            try:
                from memory.fact_memory import enqueue_memory_job
                from config import get_config
                _cfg = get_config()
                _db = str(Path(_cfg.data_dir) / "app.db")
                enqueue_memory_job(chat_id, _db)
            except Exception as _e:
                logger.debug(f"[WS] 记忆排队跳过: {_e}")
