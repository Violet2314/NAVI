"""
CollectorDaemon - 采集守护进程
串联 WindowActivityCapture + ScreenshotCapture + ActivityTracker
独立运行，不依赖 FastAPI
"""
import logging
import signal
import sqlite3
import sys
import time
from pathlib import Path

# 确保 backend 目录在 path 里
sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.activity_tracker import ActivityTracker
from collector.screenshot_capture import ScreenshotCapture
from collector.window_capture import WindowActivityCapture
from config import get_config
from db.init import init_db

logger = logging.getLogger("navi.collector")


def load_app_rules(db_path: str) -> dict:
    """从数据库加载 app 规则到内存字典（process_name → rule）"""
    rules = {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT process_name, category, sentiment, is_blacklist, is_user_defined FROM app_rules"
        ).fetchall()
        conn.close()
        for row in rows:
            rules[row[0].lower()] = {
                "category": row[1],
                "sentiment": row[2],
                "is_blacklist": bool(row[3]),
                "is_user_defined": bool(row[4]) if len(row) > 4 else False,
            }
        logger.info(f"已加载 {len(rules)} 条 app 规则")
    except Exception as e:
        logger.warning(f"加载 app 规则失败: {e}")
    return rules


def main():
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")

    # 初始化数据库
    init_db()
    logger.info("✅ Navi 采集守护进程启动")

    # 加载 app 规则
    app_rules = load_app_rules(db_path)

    # 初始化各组件
    tracker = ActivityTracker(
        db_path=db_path,
        device_id=config.device_id,
        app_rules=app_rules,
        idle_threshold_sec=float(config.collector.get("idle_threshold_sec", 300)),
    )

    window_capture = WindowActivityCapture(
        capture_interval=float(config.collector.get("window_interval_sec", 60))
    )

    screenshot_capture = ScreenshotCapture(
        screenshot_dir=config.screenshot_dir,
        db_path=db_path,
        device_id=config.device_id,
        capture_interval=float(config.collector.get("screenshot_interval_sec", 30)),
        format=config.collector.get("screenshot_format", "jpg"),
        quality=int(config.collector.get("screenshot_quality", 75)),
        histogram_threshold=float(config.collector.get("screenshot_similarity", 0.05)),
    )

    # 窗口采集 → ActivityTracker
    def on_window_captured(results):
        for window_info in results:
            tracker.on_window_captured(window_info)
            # 窗口切换时触发截图
            # screenshot_capture.force_capture()  # Day 1 先不开，避免噪音

    window_capture.set_callback(on_window_captured)

    # 截图采集 → 直接写数据库（screenshot_capture 内部处理）
    def on_screenshot_captured(results):
        for r in results:
            logger.info(f"截图: {r.get('file_path', '')} ({r.get('file_size_kb', 0)}KB)")

    screenshot_capture.set_callback(on_screenshot_captured)

    # 启动所有组件
    window_capture.start()
    screenshot_capture.start()
    logger.info("✅ 所有采集组件已启动")
    logger.info(f"   窗口采集间隔: {config.collector.get('window_interval_sec', 60)}s")
    logger.info(f"   截图采集间隔: {config.collector.get('screenshot_interval_sec', 300)}s")
    logger.info(f"   数据库: {db_path}")
    logger.info(f"   截图目录: {config.screenshot_dir}")

    # ── 摄像头采集（条件启动，独立运行模式）──────────────────────────
    camera_capture = None
    camera_enabled = config.collector.get("camera_enabled", False)
    if camera_enabled:
        try:
            from collector.camera_capture import CameraCapture
            from collector.emotion_trigger import EmotionTrigger

            model_dir = str(Path(config.data_dir) / "models")
            Path(model_dir).mkdir(parents=True, exist_ok=True)
            camera_capture = CameraCapture(
                capture_interval=float(config.collector.get("camera_interval_sec", 5)),
                emotion_every_n=int(config.collector.get("emotion_every_n", 6)),
                camera_index=int(config.collector.get("camera_index", 0)),
                model_dir=model_dir,
                confidence_threshold=float(config.collector.get("camera_confidence_threshold", 0.7)),
            )
            emotion_trigger = EmotionTrigger(db_path=db_path)

            def on_camera_captured(results):
                for r in results:
                    tracker.on_presence_update(r.get("user_present", False))
                    if r.get("emotion"):
                        tracker.on_emotion_update(r["emotion"])
                        emotion_trigger.on_emotion(r)
                        emotion_trigger.save_record(r)

            camera_capture.set_callback(on_camera_captured)
            camera_capture.start()
            logger.info("📹 摄像头采集已启动")
        except Exception as e:
            logger.warning(f"摄像头采集启动失败（不影响核心采集）: {e}")
            camera_capture = None

    # 优雅停止
    def on_signal(signum, frame):
        logger.info("收到停止信号，正在退出...")
        window_capture.stop()
        screenshot_capture.stop(graceful=True)
        if camera_capture:
            camera_capture.stop()
        tracker.flush()   # 等待 activity write_queue 清空
        tracker.close()   # 关闭 activity write_queue 线程
        logger.info("✅ 采集守护进程已停止")
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # 启动时立即执行一次截图清理
    from utils.cleanup import cleanup_screenshots
    keep_days = int(config.collector.get("screenshot_keep_days", 3))
    logger.info(f"启动时清理截图（保留最近 {keep_days} 天）")
    cleanup_screenshots(config.screenshot_dir, db_path, keep_days=keep_days)

    # 主循环：每分钟打印一次统计，每 30 分钟触发一次 LLM 分类
    last_w, last_s, last_c = 0, 0, 0
    last_llm_classify_time = 0          # 上次 LLM 分类的时间戳
    LLM_CLASSIFY_INTERVAL = 30 * 60    # 30 分钟

    try:
        while True:
            time.sleep(60)
            w_stats = window_capture.get_stats()
            s_stats = screenshot_capture.get_stats()
            w_total = w_stats['capture_count']
            s_total = s_stats['capture_count']
            msg = (
                f"统计 | 窗口采集: {w_total}次(+{w_total - last_w}) "
                f"| 截图: {s_total}次(+{s_total - last_s}) "
                f"| 错误: {w_stats['error_count'] + s_stats['error_count']}次"
            )
            if camera_capture:
                c_stats = camera_capture.get_stats()
                c_total = c_stats['capture_count']
                present = "在" if c_stats.get('user_present') else "离"
                emotion = c_stats.get('current_emotion') or '-'
                conf = c_stats.get('emotion_confidence', 0)
                msg += (
                    f" | 📹摄像头: {c_total}次(+{c_total - last_c})"
                    f" 用户{present} 情绪:{emotion}({conf:.0%})"
                )
                last_c = c_total
            logger.info(msg)
            last_w, last_s = w_total, s_total

            # ── 定期 LLM 分类：每 30 分钟对 pending 记录批量分类 ──────────────
            now_ts = time.time()
            if now_ts - last_llm_classify_time >= LLM_CLASSIFY_INTERVAL:
                last_llm_classify_time = now_ts
                try:
                    from collector.llm_classifier import classify_pending_activities
                    updated = classify_pending_activities(db_path)
                    if updated > 0:
                        logger.info(f"🤖 LLM 定期分类完成：{updated} 条记录已更新")
                        # 重新加载 app_rules，让新缓存规则立即生效
                        new_rules = load_app_rules(db_path)
                        tracker.update_rules(new_rules)
                        logger.info(f"📋 app_rules 已热重载：{len(new_rules)} 条规则")
                except Exception as e:
                    logger.warning(f"⚠️ LLM 定期分类失败（不影响采集）: {e}")

    except KeyboardInterrupt:
        on_signal(None, None)


def start_collector():
    """
    供 FastAPI main.py 调用：启动采集组件并返回引用
    返回 (window_capture, screenshot_capture)
    """
    import atexit
    config = get_config()
    db_path = str(Path(config.data_dir) / "app.db")
    init_db()

    app_rules = load_app_rules(db_path)
    window_interval = float(config.collector.get("window_interval_sec", 60))
    tracker = ActivityTracker(
        db_path=db_path,
        device_id=config.device_id,
        app_rules=app_rules,
        capture_interval_sec=window_interval,
        idle_threshold_sec=float(config.collector.get("idle_threshold_sec", 300)),
    )

    window_capture = WindowActivityCapture(capture_interval=window_interval)
    screenshot_capture = ScreenshotCapture(
        screenshot_dir=config.screenshot_dir,
        db_path=db_path,
        device_id=config.device_id,
        capture_interval=float(config.collector.get("screenshot_interval_sec", 30)),
        format=config.collector.get("screenshot_format", "jpg"),
        quality=int(config.collector.get("screenshot_quality", 75)),
        histogram_threshold=float(config.collector.get("screenshot_similarity", 0.05)),
    )

    def on_window_captured(results):
        for window_info in results:
            tracker.on_window_captured(window_info)

    window_capture.set_callback(on_window_captured)
    window_capture.start()
    screenshot_capture.start()

    # ── 摄像头采集（条件启动）──────────────────────────────────────────────
    camera_capture = None
    camera_enabled = config.collector.get("camera_enabled", False)
    if camera_enabled:
        try:
            from collector.camera_capture import CameraCapture
            from collector.emotion_trigger import EmotionTrigger

            model_dir = str(Path(config.data_dir) / "models")
            Path(model_dir).mkdir(parents=True, exist_ok=True)
            camera_capture = CameraCapture(
                capture_interval=float(config.collector.get("camera_interval_sec", 5)),
                emotion_every_n=int(config.collector.get("emotion_every_n", 6)),
                camera_index=int(config.collector.get("camera_index", 0)),
                model_dir=model_dir,
                confidence_threshold=float(config.collector.get("camera_confidence_threshold", 0.7)),
            )
            emotion_trigger = EmotionTrigger(db_path=db_path)

            def on_camera_captured(results):
                for r in results:
                    # 更新 tracker 在离状态
                    tracker.on_presence_update(r.get("user_present", False))
                    # 情绪识别结果处理
                    if r.get("emotion"):
                        tracker.on_emotion_update(r["emotion"])
                        emotion_trigger.on_emotion(r)
                        emotion_trigger.save_record(r)

            camera_capture.set_callback(on_camera_captured)
            camera_capture.start()
            logger.info("📹 摄像头采集已启动")
        except Exception as e:
            logger.warning(f"摄像头采集启动失败（不影响核心采集）: {e}")
            camera_capture = None

    def cleanup():
        window_capture.stop()
        screenshot_capture.stop(graceful=True)
        if camera_capture:
            camera_capture.stop()
        tracker.flush()

    atexit.register(cleanup)
    return window_capture, screenshot_capture, tracker, camera_capture


if __name__ == "__main__":
    main()
