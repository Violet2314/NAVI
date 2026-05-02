"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger
from llm_constants import AGENT_CONTEXT_WINDOW_TOKENS

from agent.context import ContextBuilder
from agent.memory import MemoryConsolidator
from agent.subagent import SubagentManager
from agent.tools.cron import CronTool
from agent.skills import BUILTIN_SKILLS_DIR
from agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from agent.tools.message import MessageTool
from agent.tools.registry import ToolRegistry
from agent.tools.shell import ExecTool
from agent.tools.spawn import SpawnTool
from agent.tools.web import WebFetchTool, WebSearchTool
from agent.tools.skill_manage import SkillManageTool
from agent.insights_engine import (
    record_tool_call,
    upsert_agent_session,
    get_insights_engine,
    maybe_run_deep_analysis,
    record_skill_use,
    detect_skill_from_read_path,
)
from agent.subdirectory_hints import get_hint_tracker
from bus.events import InboundMessage, OutboundMessage
from bus.queue import MessageBus
from providers.base import LLMProvider
from session.manager import Session, SessionManager

if TYPE_CHECKING:
    from config.schema import ChannelsConfig, ExecToolConfig, WebSearchConfig
    from cron.service import CronService


# 反思阶段硬上限兜底：无论动态条件是否触发，到达这一轮一定反思一次。
# 动态触发条件在 Scratchpad.should_reflect() 里（连续同工具/失败/打转）。
_REFLECT_HARD_CAP: int = 30

_REFLECT_PROMPT = (
    "（系统提示：你已经调用了 {iteration} 次工具。\n"
    "反思触发原因：{reason}\n\n"
    "请简短自评当前进展：\n"
    "1. 当前目标：\n"
    "2. 已完成的步骤：\n"
    "3. 下一步计划：\n"
    "如果已经有足够信息直接回复用户，就立即回复，不要再调用工具；"
    "如果之前的方向走偏了，请承认并调整策略。）"
)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 16_000

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = AGENT_CONTEXT_WINDOW_TOKENS,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
    ):
        from config.schema import ExecToolConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)

        # ── 初始化 SubdirectoryHints 和 SkillDeduplicator 单例 ──────────────
        get_hint_tracker(workspace)
        try:
            from agent.skill_dedup import get_skill_deduplicator
            get_skill_deduplicator(workspace)
        except Exception:
            pass
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        # per-session 锁：不同会话（本地/微信/钉钉/cron）可以并发处理
        # 同一会话内消息仍然串行，防止上下文乱序
        self._session_locks: dict[str, asyncio.Lock] = {}
        self.memory_consolidator = MemoryConsolidator(
            workspace=workspace,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
        )
        # ── Phase 2: InsightsEngine 初始化 ────────────────────────────────
        try:
            from config import get_config
            _db_path = str(Path(get_config().data_dir) / "app.db")
            self._db_path = _db_path
            get_insights_engine(_db_path)  # 初始化单例
        except Exception:
            self._db_path = None
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        self.tools.register(SkillManageTool(workspace=self.workspace))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        session_type: str = "chat",
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop.

        Args:
            session_type: "chat" | "task" | "proactive" | "system"
                Controls which tools are exposed to the LLM.
        """
        from agent.scratchpad import Scratchpad, ScratchEntry, summarize_args

        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        # ── Scratchpad：结构化便签本，追踪"试过什么 / 学到什么" ──────────
        scratch = Scratchpad(goal="")

        # 反思去重：同一原因在短窗口内不重复触发，避免连环反思
        _last_reflect_iter: int = -10
        _last_reflect_reason: str = ""
        _REFLECT_COOLDOWN: int = 5  # 两次反思之间至少隔 5 轮

        while iteration < self.max_iterations:
            iteration += 1

            # ── 反思阶段：动态触发 + 硬上限兜底 ──────────────────────────────
            # 动态条件（Scratchpad.should_reflect）：
            #   连续同名工具 / 连续失败 / observation 打转
            # 硬上限兜底（iteration == _REFLECT_HARD_CAP）：
            #   即使没有动态信号，也强制反思一次
            _reflect_reason: str | None = None
            if iteration - _last_reflect_iter >= _REFLECT_COOLDOWN:
                _reflect_reason = scratch.should_reflect()
                if (
                    _reflect_reason is None
                    and iteration == _REFLECT_HARD_CAP
                ):
                    _reflect_reason = f"已达反思硬上限 {_REFLECT_HARD_CAP} 轮"

            if _reflect_reason:
                _last_reflect_iter = iteration
                _last_reflect_reason = _reflect_reason
                _reflect_msg = {
                    "role": "user",
                    "content": _REFLECT_PROMPT.format(
                        iteration=iteration, reason=_reflect_reason
                    ),
                }
                try:
                    _reflect_resp = await self.provider.chat_with_retry(
                        messages=messages + [_reflect_msg],
                        tools=None,  # 反思阶段不带工具，强制文字输出
                        model=self.model,
                    )
                    _reflect_text = self._strip_think(_reflect_resp.content) or ""
                    if _reflect_text:
                        messages = messages + [
                            _reflect_msg,
                            {"role": "assistant", "content": _reflect_text},
                        ]
                        logger.info(
                            "ReAct reflection at iter {} (reason={}): {}",
                            iteration,
                            _reflect_reason,
                            _reflect_text[:120],
                        )
                        # 如果反思阶段 LLM 直接给出了最终答案（不再调用工具），
                        # finish_reason 不是 tool_calls 且内容有效，则直接采纳
                        if _reflect_resp.finish_reason != "tool_calls" and _reflect_text:
                            final_content = _reflect_text
                            break
                except Exception:
                    logger.warning("ReAct reflection failed at iter {} (non-fatal)", iteration)

            if final_content is not None:
                break

            tool_defs = self.tools.get_definitions_for_session(session_type)

            # ── 注入 scratchpad 到消息列表（LLM 调用前）───────────────────
            # 注意：用 user 角色而非 system，因为 MiniMax/DeepSeek 等厂商
            # 不允许在对话中间插入 system 消息（仅允许在最开头）。
            scratch_text = scratch.to_prompt()
            augmented_messages = (
                messages + [{"role": "user", "content": scratch_text}]
                if scratch_text else messages
            )

            response = await self.provider.chat_with_retry(
                messages=augmented_messages,
                tools=tool_defs,
                model=self.model,
            )

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    tool_hint = self._tool_hint(response.tool_calls)
                    tool_hint = self._strip_think(tool_hint)
                    await on_progress(tool_hint, tool_hint=True)

                tool_call_dicts = [
                    tc.to_openai_tool_call()
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    _t_start = time.monotonic()
                    _tool_ok = True
                    try:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    except Exception as _exc:
                        _tool_ok = False
                        result = f"Error: {_exc}"
                        raise _exc
                    finally:
                        # ── Phase 2: 记录工具调用日志 ────────────────────
                        if getattr(self, "_db_path", None) and getattr(self, "_current_session_key", None):
                            _dur = int((time.monotonic() - _t_start) * 1000)
                            record_tool_call(
                                self._db_path,
                                self._current_session_key,
                                tool_call.name,
                                success=_tool_ok,
                                duration_ms=_dur,
                            )
                            # ── P1-3: 检测 skill 加载（read_file SKILL.md）─────
                            try:
                                if tool_call.name == "read_file":
                                    _path_arg = (
                                        tool_call.arguments.get("path")
                                        if isinstance(tool_call.arguments, dict)
                                        else None
                                    )
                                    _skill_name = detect_skill_from_read_path(_path_arg or "")
                                    if _skill_name:
                                        record_skill_use(
                                            self._db_path,
                                            _skill_name,
                                            success=_tool_ok,
                                            session_id=self._current_session_key,
                                        )
                            except Exception:
                                pass
                            # ── 每50次工具调用触发 LLM 深度分析 ──────────
                            try:
                                from llm_client import chat as _llm_chat
                                self._schedule_background(
                                    maybe_run_deep_analysis(self._db_path, _llm_chat)
                                )
                            except Exception:
                                pass
                    # ── SubdirectoryHints：工具调用完成后检测目录规则 ────────
                    try:
                        _hint_tracker = get_hint_tracker(self.workspace)
                        if _hint_tracker:
                            _dirs = _hint_tracker.extract_paths_from_tool_call(
                                tool_call.name, tool_call.arguments
                            )
                            for _d in _dirs:
                                _hint = _hint_tracker.check_directory(_d)
                                if _hint:
                                    _block = _hint_tracker.format_hint_block(_hint, _d)
                                    result = f"{result}\n\n{_block}" if result else _block
                    except Exception:
                        pass

                    # ── 记录到 scratchpad ────────────────────────────────
                    scratch.add(ScratchEntry(
                        iteration=iteration,
                        action=f"{tool_call.name}({summarize_args(tool_call.arguments)})",
                        observation=str(result)[:120],
                        success=_tool_ok,
                        tool_name=tool_call.name,
                    ))

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations (%d) reached, falling back to summary",
                           self.max_iterations)
            fallback_messages = messages + [{
                "role": "user",
                "content": (
                    "（系统提示：已达到工具调用上限。请基于以上所有上下文，"
                    "用一段话回复用户：\n"
                    "- 如果信息已足够回答原问题，直接回答；\n"
                    "- 如果还差关键信息，如实告诉用户「已查到 X、Y，但还需要 Z」；\n"
                    "- 不要再调用任何工具；\n"
                    "- 用用户期待的语气和称呼，参考 SOUL 配置。）"
                ),
            }]
            try:
                fallback = await self.provider.chat_with_retry(
                    messages=fallback_messages,
                    tools=None,                       # 关键：关掉工具
                    model=self.model,
                )
                final_content = self._strip_think(fallback.content) or (
                    "（多次尝试后没能完成这个任务，能不能再说一遍需求？）"
                )
            except Exception as e:
                logger.exception("Fallback summary failed: %s", e)
                final_content = "（系统繁忙，请再发一次需求。）"

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            cmd = msg.content.strip().lower()
            if cmd == "/stop":
                await self._handle_stop(msg)
            elif cmd == "/restart":
                await self._handle_restart(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _handle_restart(self, msg: InboundMessage) -> None:
        """Restart the process in-place via os.execv."""
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        ))

        async def _do_restart():
            await asyncio.sleep(1)
            # Use -m navi for cross-platform compatibility
            os.execv(sys.executable, [sys.executable, "-m", "navi"] + sys.argv[1:])

        asyncio.create_task(_do_restart())

    async def handle_cron_job(self, job: "CronJob") -> str | None:
        """
        Handle a cron job callback - called by CronService when a job is due.
        Sends the job's message to the configured channel/chat as if it came from the agent.
        """
        from cron.types import CronJob

        payload = job.payload
        if not payload.deliver or not payload.channel or not payload.to:
            logger.info("Cron job '{}' executed (no delivery)", job.name)
            return None

        content = payload.message or f"提醒: {job.name}"
        chat_id = payload.to

        # ── 1. 写入 DB ────────────────────────────────────────────────────
        try:
            from api.chat_routes import save_message
            _loop = asyncio.get_event_loop()
            await _loop.run_in_executor(None, save_message, chat_id, "assistant", content)
            logger.debug("Cron job '{}' saved to DB session={}", job.name, chat_id)
        except Exception as e:
            logger.warning("Cron job '{}' DB persist failed: {}", job.name, e)

        # ── 2. 从消息内容提取情绪标签（兼容 `[EMOTION: xxx]` 带空格变体）──
        from utils.helpers import parse_emotion_tag
        emotion, content = parse_emotion_tag(content)

        # ── 3. broadcaster 广播 type="reply"（Live2D 和 ChatPage 都能处理）──
        #    broadcaster 广播全量内容，不走流式协议
        #    Live2D 只认 type="reply"，ChatPage 也统一用 reply 接收
        try:
            from bus.broadcaster import get_broadcaster
            await get_broadcaster().broadcast({
                "type": "reply",
                "content": content,
                "emotion": emotion,
                "chat_id": chat_id,
                "cron": True,       # 标记来源，供前端按 chat_id 过滤
            })
        except Exception as e:
            logger.warning("Cron job '{}' WS broadcast failed: {}", job.name, e)

        # ── 3.5 直接投递到 IM 渠道（WeChat / 其他）──
        #    不走 bus，直接调 channel 单例，跟 proactive dispatcher 保持一致
        if payload.deliver and payload.channel and payload.channel != "local":
            try:
                if payload.channel == "wechat":
                    from channels.wechat import WeChatChannel
                    wechat = WeChatChannel.get_instance()
                    if wechat and wechat.is_online:
                        await wechat.send_to_last_user(content)
                        logger.info("Cron job '{}' delivered to wechat", job.name)
                    else:
                        logger.warning("Cron job '{}': WeChat offline, skipped", job.name)
                else:
                    # 其他 channel 走 bus 路由
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=payload.channel,
                        chat_id=chat_id,
                        content=content,
                    ))
                    logger.info("Cron job '{}' queued to channel={}", job.name, payload.channel)
            except Exception as e:
                logger.warning("Cron job '{}' channel deliver failed: {}", job.name, e)

        # ── 4. TTS（后台并行，不阻塞）────────────────────────────────────
        async def _cron_tts():
            try:
                from tts.manager import get_tts_manager
                import re as _re
                tts_mgr = get_tts_manager()
                if not tts_mgr.enabled or not content.strip():
                    return
                tts_text = _re.sub(r'[*_`#>\[\]!|]', '', content).strip()
                tts_audio_b64 = await tts_mgr.synthesize_to_base64(tts_text)
                from bus.broadcaster import get_broadcaster as _gb
                await _gb().broadcast({
                    "type": "tts_audio",
                    "audio": tts_audio_b64,
                    "format": "mp3",
                    "chat_id": chat_id,
                })
                logger.info("Cron job '{}' TTS sent", job.name)
            except Exception as e:
                logger.warning("Cron job '{}' TTS failed (non-fatal): {}", job.name, e)

        asyncio.create_task(_cron_tts())

        logger.info("Cron job '{}' delivered to chat_id={}", job.name, chat_id)
        return content

    def _get_session_lock(self, session_key: str) -> asyncio.Lock:
        """获取（或创建）指定 session 的独立锁。"""
        if session_key not in self._session_locks:
            self._session_locks[session_key] = asyncio.Lock()
        return self._session_locks[session_key]

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under a per-session lock (allows concurrent sessions)."""
        lock = self._get_session_lock(msg.session_key)
        async with lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            # Subagent results should be assistant role, other system messages use user role
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
                current_role=current_role,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages, session_type="system")
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # ── 全渠道：通知 ProactiveEngine 用户发来了消息（反馈感知）─────────
        # 提升到 AgentLoop 层，确保微信/钉钉等渠道的用户消息也能触发反馈检测
        try:
            from proactive.engine import ProactiveEngine
            _proactive = getattr(self, "_proactive_engine_ref", None)
            if _proactive is not None and isinstance(_proactive, ProactiveEngine):
                _proactive.on_user_message(msg.content)
        except Exception:
            pass  # 反馈感知失败不影响主流程

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            snapshot = session.messages[session.last_consolidated:]
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            if snapshot:
                self._schedule_background(self.memory_consolidator.archive_messages(snapshot))

            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            lines = [
                "🐈 Navi commands:",
                "/new — Start a new conversation",
                "/stop — Stop the current task",
                "/restart — Restart the bot",
                "/help — Show available commands",
            ]
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines),
            )
        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        # ── Phase 2: 设置当前 session key（供工具调用日志使用）──────────────
        self._current_session_key = key

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)

        # ── Phase 2: prefetch_all — 每轮消息前从 FactMemory 检索相关事实 ──
        prefetch_context = ""
        try:
            from memory.fact_memory import get_fact_memory
            fm = get_fact_memory()
            # 用用户消息作为查询键，检索最相关的事实
            relevant_facts = fm.search_facts(msg.content, limit=5)
            if relevant_facts:
                lines = [f"  - {f['content']}" for f in relevant_facts]
                prefetch_context = (
                    "<memory-context>\n"
                    "[本轮检索到的相关用户事实]\n"
                    + "\n".join(lines)
                    + "\n</memory-context>"
                )
        except Exception:
            pass  # prefetch 失败不影响主流程

        # 将 prefetch 上下文附加到当前消息前
        current_message = (
            prefetch_context + "\n\n" + msg.content
            if prefetch_context
            else msg.content
        )

        initial_messages = self.context.build_messages(
            history=history,
            current_message=current_message,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # ── 构造返回值（先准备好，后台任务再做持久化，避免阻塞回复推送）──
        is_message_tool_sent = (
            (mt := self.tools.get("message"))
            and isinstance(mt, MessageTool)
            and mt._sent_in_turn
        )

        # ── 所有持久化/后台工作放到 background，不阻塞 return ──────────
        async def _post_process():
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            # ── 生命周期钩子：会话结束后更新用户画像 + 触发 fact 入队 ──
            self._schedule_background(self.memory_consolidator.on_session_end(session, self.workspace))
            # ── Phase 2: 写入 agent_sessions 统计（upsert，幂等）──────
            if self._db_path:
                tool_count = sum(
                    1 for m in all_msgs[1 + len(history):]
                    if m.get("role") == "tool"
                )
                upsert_agent_session(
                    self._db_path,
                    key,
                    message_count=len(session.messages),
                    tool_call_count=tool_count,
                    model=self.model,
                    ended=True,
                )

        self._schedule_background(_post_process())

        if is_message_tool_sent:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            path = (c.get("_meta") or {}).get("path", "")
                            placeholder = f"[image: {path}]" if path else "[image]"
                            filtered.append({"type": "text", "text": placeholder})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""
