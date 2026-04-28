"""
Navi Backend - 主入口
启动 FastAPI 服务 + 后台采集进程
"""
import uvicorn
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path
from fastapi import WebSocket

# ── 统一日志初始化（必须在所有模块 import 之前）──────────────────────────
# 策略：以 loguru 为唯一输出端，标准 logging 通过 InterceptHandler 转发给 loguru。
# 结果：所有模块（无论用 logging 还是 loguru）输出格式完全一致，无重复、无乱序。
try:
    from loguru import logger as _loguru_logger

    class _InterceptHandler(logging.Handler):
        """把标准 logging 的所有输出转发给 loguru，实现单一输出端。"""
        def emit(self, record: logging.LogRecord) -> None:
            # 把 logging 的 level 名称映射到 loguru level
            try:
                level = _loguru_logger.level(record.levelname).name
            except ValueError:
                level = str(record.levelno)

            # 找到真实调用帧（跳过 logging 内部帧），让 loguru 显示正确的调用位置
            frame, depth = logging.currentframe(), 2
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back  # type: ignore[assignment]
                depth += 1

            _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )

    # 清空标准 logging 的所有 handler，替换为 InterceptHandler
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.INFO, force=True)
    # 降低 uvicorn access log 噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    # loguru 自身：移除默认 stderr handler，只保留格式化的 stdout handler
    _loguru_logger.remove()
    _loguru_logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>: {message}",
        level="INFO",
        colorize=True,
    )

except ImportError:
    # loguru 未安装时退回到标准 logging（不影响运行）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

from db.init import init_db
from config import get_config
from api.routes import router, set_collector_stats, set_capture_refs, set_tracker_ref, set_blacklist_monitor_ref, set_camera_refs
from api.soul_routes import soul_router
from api.chat_routes import router as chat_router
from api.tts_routes import tts_router
from api.wechat_routes import router as wechat_router
from collector.daemon import start_collector
from blacklist.monitor import BlacklistMonitor

_window_capture = None
_screenshot_capture = None
_tracker = None
_camera_capture = None
_blacklist_monitor = None
_agent_loop = None
_ws_channel = None


def _cleanup_stale_snapshots(db_path: str):
    """
    启动时清理上次残留的快照记录（is_synced = -1）。
    v3 已移除定时快照机制，此函数仅清理历史残留。

    清理策略：将残留快照转为已完成记录（保留数据，不丢失），
    用快照自带的 ended_at 和 duration_sec 作为最终值，
    把 is_synced 从 -1 改为 0（正常记录）。
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        updated = conn.execute(
            "UPDATE window_activities SET is_synced = 0 WHERE is_synced = -1"
        ).rowcount
        conn.commit()
        conn.close()
        if updated:
            print(f"🧹 清理了 {updated} 条残留快照记录（转为已完成）")
    except Exception as e:
        print(f"⚠️  清理残留快照失败（不影响启动）: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _window_capture, _screenshot_capture, _tracker, _camera_capture, _blacklist_monitor, _agent_loop, _ws_channel

    # ── startup ──────────────────────────────────────────────────────────────
    # 注：日志体系已在模块顶部通过 _InterceptHandler 统一初始化，此处无需重复设置。
    init_db()

    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")

    # ── 清理上次残留的快照记录（后端被强杀时快照未清理）──────────────
    _cleanup_stale_snapshots(db_path)

    # 灵魂仓库：首次启动时将现有 SOUL.md 导入仓库
    from soul.vault import ensure_current_in_vault
    ensure_current_in_vault()
    _window_capture, _screenshot_capture, _tracker, _camera_capture = start_collector()
    set_capture_refs(_window_capture, _screenshot_capture)
    set_tracker_ref(_tracker)

    # 摄像头引用注入到 API 路由
    if _camera_capture:
        # 尝试获取 emotion_trigger 引用（从 daemon 的闭包中无法直接获取，
        # 所以创建一个新的 EmotionTrigger 用于 API 查询）
        try:
            from collector.emotion_trigger import EmotionTrigger
            _emotion_trigger_for_api = EmotionTrigger(
                db_path=str(Path(config.data_dir) / "app.db")
            )
            set_camera_refs(_camera_capture, _emotion_trigger_for_api)
        except Exception:
            set_camera_refs(_camera_capture, None)
    _blacklist_monitor = BlacklistMonitor(db_path=db_path)
    _blacklist_monitor.start()
    set_blacklist_monitor_ref(_blacklist_monitor)

    # 初始化 AgentLoop + Cron 服务
    _cron_service = None
    try:
        from agent.loop import AgentLoop
        from agent.navi_provider import NaviLLMProvider
        from bus.queue import MessageBus
        from channels.local_ws import LocalWSChannel
        from cron.service import CronService

        _bus = MessageBus()
        _provider = NaviLLMProvider()
        
        # 初始化 Cron 服务
        _cron_store_path = Path(config.data_dir) / "cron_jobs.json"
        _cron_service = CronService(store_path=_cron_store_path)
        
        _agent_loop = AgentLoop(
            bus=_bus,
            provider=_provider,  # type: ignore[arg-type]
            workspace=Path(__file__).parent,
            max_iterations=20,
            cron_service=_cron_service,
        )
        
        # 设置 cron 的回调，到期时触发 AgentLoop
        _cron_service.on_job = _agent_loop.handle_cron_job
        
        _ws_channel = LocalWSChannel(bus=_bus, agent_loop=_agent_loop)
        print("✅ AgentLoop 初始化成功")
        
        # 启动 Cron 服务
        await _cron_service.start()
        print("✅ Cron 定时器服务启动成功")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️  AgentLoop 初始化失败（不影响采集）: {e}")

    # 设置广播器的事件循环（让同步模块能跨线程广播）
    import asyncio
    from bus.broadcaster import get_broadcaster
    get_broadcaster().set_loop(asyncio.get_running_loop())

    # Phase 3: 启动记忆提取 Worker + 矛盾检测 Worker（后台异步任务）
    try:
        from memory.fact_memory import memory_worker, _contradiction_worker_loop
        _mem_worker_task = asyncio.create_task(
            memory_worker(db_path=str(Path(config.data_dir) / "app.db"), interval=30)
        )
        # 矛盾检测 worker：独立后台任务，消费 add_facts() 写入后的检测队列
        _contradiction_worker_task = asyncio.create_task(
            _contradiction_worker_loop()
        )
        print("✅ Memory Worker 启动成功（含后台矛盾检测 Worker）")
    except Exception as e:
        print(f"⚠️  Memory Worker 启动失败（不影响核心功能）: {e}")
        _mem_worker_task = None
        _contradiction_worker_task = None

    # Phase 3.5: 启动 MemoryScheduler（统一巡检）
    _memory_scheduler = None
    try:
        from agent.memory_scheduler import MemoryScheduler
        _memory_scheduler = MemoryScheduler(
            workspace=Path(__file__).parent,
            interval_sec=300,
        )
        await _memory_scheduler.start()
        print("✅ MemoryScheduler 启动成功（每 5 分钟巡检）")
    except Exception as e:
        print(f"⚠️  MemoryScheduler 启动失败（不影响核心功能）: {e}")

    # Phase 4: 启动主动对话引擎
    _proactive_engine = None
    try:
        from proactive.engine import ProactiveEngine
        from session.manager import SessionManager

        # 加载 Soul 内容
        _soul_content = ""
        _soul_path = Path(__file__).parent / "templates" / "SOUL.md"
        if _soul_path.exists():
            _soul_content = _soul_path.read_text(encoding="utf-8")

        _session_mgr = SessionManager(workspace=Path(__file__).parent)
        # 注意：_provider 是 lifespan 函数的局部变量，用 locals() 检查
        _resolved_provider = locals().get('_provider', None)
        if _resolved_provider is None:
            print("⚠️  ProactiveEngine: LLM provider 未找到（AgentLoop 可能未初始化），将使用 config 兜底")
        else:
            print("✅ ProactiveEngine: LLM provider 已注入")

        _proactive_engine = ProactiveEngine(
            db_path=db_path,
            ws_channel=_ws_channel,
            session_manager=_session_mgr,
            llm_provider=_resolved_provider,
            soul_content=_soul_content,
            patrol_interval=120,
        )
        await _proactive_engine.start()

        # 注册 Collector 回调（System 1 实时触发）
        if _tracker:
            _tracker._on_activity_change = _proactive_engine.on_activity_change
        if _blacklist_monitor:
            _blacklist_monitor._on_hit = _proactive_engine.on_blacklist_hit

        # 注册摄像头情绪回调到 ProactiveEngine
        if _camera_capture:
            try:
                from collector.emotion_trigger import EmotionTrigger as _ET
                _emotion_trigger_for_engine = _ET(
                    db_path=db_path
                )
                # 覆盖 daemon 中的回调，增加 proactive engine 通知
                _original_callback = _camera_capture._callback

                def _camera_callback_with_engine(results):
                    if _original_callback:
                        _original_callback(results)
                    for r in results:
                        if r.get("emotion"):
                            trigger_event = _emotion_trigger_for_engine.on_emotion(r)
                            if trigger_event:
                                _proactive_engine.on_emotion_shift(trigger_event)

                _camera_capture.set_callback(_camera_callback_with_engine)
                print("📹 摄像头情绪回调已连接到 ProactiveEngine")
            except Exception as e:
                print(f"⚠️  摄像头→ProactiveEngine 回调注册失败（不影响核心功能）: {e}")

        # 注入到 routes.py 供 API 使用
        from api.routes import set_proactive_engine_ref
        set_proactive_engine_ref(_proactive_engine)

        # 注入到 AgentLoop，实现全渠道反馈感知（微信/钉钉消息也能触发）
        if _agent_loop is not None:
            _agent_loop._proactive_engine_ref = _proactive_engine

        # 从 config.yaml 加载已保存的 proactive 参数
        from config import NAVI_HOME as _NAVI_HOME
        _raw_cfg = {}
        _cfg_path = _NAVI_HOME / "config.yaml"
        if _cfg_path.exists():
            import yaml as _yaml
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _raw_cfg = _yaml.safe_load(_f) or {}
        _pcfg = _raw_cfg.get("proactive", {})
        if _pcfg:
            from proactive import constants as _C
            _FIELDS = {
                "patrol_interval_sec": ("PATROL_INTERVAL_SEC", int),
                "long_work_threshold_min": ("LONG_WORK_THRESHOLD_MIN", int),
                "slacking_threshold_min": ("SLACKING_THRESHOLD_MIN", int),
                "idle_threshold_min": ("IDLE_THRESHOLD_MIN", int),
                "periodic_chat_threshold_hours": ("PERIODIC_CHAT_THRESHOLD_HOURS", int),
                "gate_min_interval_min": ("GATE_MIN_INTERVAL_MIN", int),
                "gate_max_daily_proactive": ("GATE_MAX_DAILY_PROACTIVE", int),
                "gate_quiet_hours_start": ("GATE_QUIET_HOURS_START", int),
                "gate_quiet_hours_end": ("GATE_QUIET_HOURS_END", int),
                "gate_base_threshold": ("GATE_BASE_THRESHOLD", float),
                "feedback_ignore_timeout_sec": ("FEEDBACK_IGNORE_TIMEOUT_SEC", int),
                "silent_mode_default_hours": ("SILENT_MODE_DEFAULT_HOURS", float),
            }
            for _k, (_cn, _cast) in _FIELDS.items():
                if _k in _pcfg:
                    setattr(_C, _cn, _cast(_pcfg[_k]))
            # 同步到 GateKeeper 实例
            _gate = _proactive_engine.gate
            if "gate_min_interval_min" in _pcfg:
                _gate.min_interval_minutes = int(_pcfg["gate_min_interval_min"])
            if "gate_max_daily_proactive" in _pcfg:
                _gate.max_daily_proactive = int(_pcfg["gate_max_daily_proactive"])
            if "gate_quiet_hours_start" in _pcfg or "gate_quiet_hours_end" in _pcfg:
                _gate.quiet_hours = (_C.GATE_QUIET_HOURS_START, _C.GATE_QUIET_HOURS_END)
            if "gate_base_threshold" in _pcfg:
                _gate.base_threshold = float(_pcfg["gate_base_threshold"])
            if "patrol_interval_sec" in _pcfg:
                _proactive_engine.patrol_interval = int(_pcfg["patrol_interval_sec"])
            print(f"   📝 已从 config.yaml 加载主动对话参数: {list(_pcfg.keys())}")

        print("✅ ProactiveEngine 启动成功（主动对话已激活）")
    except Exception as e:
        print(f"⚠️  ProactiveEngine 启动失败（不影响核心功能）: {e}")
        import traceback; traceback.print_exc()

    # Phase 5: 启动微信频道（如果有已保存凭证则自动连接）
    _wechat_channel = None
    try:
        from channels.wechat import WeChatChannel
        if _agent_loop:
            _wechat_channel = WeChatChannel(agent_loop=_agent_loop)
            await _wechat_channel.start()  # 有凭证则自动连接，没有则等 QR 登录
            print("✅ WeChat 频道初始化成功" + (" (在线)" if _wechat_channel.is_online else " (等待登录)"))
        else:
            print("⚠️  WeChat 频道跳过（AgentLoop 未初始化）")
    except Exception as e:
        print(f"⚠️  WeChat 频道初始化失败（不影响核心功能）: {e}")

    print("✅ Navi Backend 启动成功，采集已开始")

    yield  # ── 服务运行中 ──────────────────────────────────────────────────────

    # ── shutdown ─────────────────────────────────────────────────────────────
    if _wechat_channel:
        await _wechat_channel.stop()
    if _cron_service:
        _cron_service.stop()
    if _tracker:
        _tracker.flush()
    if _window_capture:
        _window_capture.stop()
    if _screenshot_capture:
        _screenshot_capture.stop(graceful=True)
    if _camera_capture:
        _camera_capture.stop()
    if _blacklist_monitor:
        _blacklist_monitor.stop()


app = FastAPI(title="Navi Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(soul_router)
app.include_router(chat_router)
app.include_router(tts_router)
app.include_router(wechat_router)

# ── 挂载 media 静态文件目录（图片持久化存储）──────────────────
from fastapi.staticfiles import StaticFiles
from utils.media import get_media_dir
_media_dir = get_media_dir()
app.mount("/media", StaticFiles(directory=str(_media_dir)), name="media")


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, chat_id: str = "local"):
    """本地对话 WebSocket 入口"""
    if _ws_channel is None:
        await websocket.close(code=1013, reason="AgentLoop 未初始化")
        return
    await _ws_channel.handle(websocket, chat_id=chat_id)


@app.get("/health")
async def health():
    # 顺便刷新采集器状态
    if _window_capture and _screenshot_capture:
        set_collector_stats({
            "window_capture": _window_capture.get_stats(),
            "screenshot_capture": _screenshot_capture.get_stats(),
        })
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)