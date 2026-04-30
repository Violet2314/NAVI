"""
数据库初始化 - 创建所有 SQLite 表
首次启动自动执行，幂等操作
"""
import sqlite3
from pathlib import Path
from config import get_config

def init_db():
    config = get_config()
    db_path = Path(config.data_dir) / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
    -- 窗口活动记录（核心采集数据，支持父子层级）
    CREATE TABLE IF NOT EXISTS window_activities (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at    DATETIME NOT NULL,
        ended_at      DATETIME,
        duration_sec  INTEGER DEFAULT 0,
        process_name  TEXT NOT NULL,
        window_title  TEXT NOT NULL,
        app_category  TEXT DEFAULT 'other',
        classification_source TEXT DEFAULT 'rule',  -- rule/llm/user/pending
        sentiment     TEXT DEFAULT 'neutral',
        device_id     TEXT NOT NULL,
        is_synced     INTEGER DEFAULT 0,
        parent_id     INTEGER DEFAULT NULL,  -- NULL=大段(主活动), 非NULL=子活动
        FOREIGN KEY (parent_id) REFERENCES window_activities(id) ON DELETE CASCADE
    );

    -- app 分类规则表
    CREATE TABLE IF NOT EXISTS app_rules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        process_name    TEXT UNIQUE NOT NULL,
        app_name        TEXT,
        category        TEXT NOT NULL DEFAULT 'other',
        sentiment       TEXT NOT NULL DEFAULT 'neutral',
        is_blacklist    INTEGER DEFAULT 0,
        is_user_defined INTEGER DEFAULT 0,
        auto_tagged_at  DATETIME,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- 截图记录
    CREATE TABLE IF NOT EXISTS screenshots (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        captured_at         DATETIME NOT NULL,
        file_path           TEXT NOT NULL,
        file_size_kb        INTEGER,
        window_activity_id  INTEGER,
        is_duplicate        INTEGER DEFAULT 0,
        sent_to_llm         INTEGER DEFAULT 0,
        llm_description     TEXT,
        device_id           TEXT NOT NULL,
        FOREIGN KEY (window_activity_id) REFERENCES window_activities(id)
    );

    -- 日报元数据（内容在 .md 文件里）
    CREATE TABLE IF NOT EXISTS daily_reports (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date     DATE UNIQUE NOT NULL,
        generated_at    DATETIME NOT NULL,
        summary         TEXT,
        md_file_path    TEXT,
        focus_score     REAL,
        total_work_sec  INTEGER,
        total_waste_sec INTEGER,
        positive_items  TEXT,
        negative_items  TEXT
    );

    -- 日报追问（P0-2：日报追问模式）
    -- 一天最多一条 pending；用户回答后 status=answered，超时则 status=timeout
    CREATE TABLE IF NOT EXISTS daily_report_inquiries (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date   TEXT NOT NULL UNIQUE,
        question      TEXT NOT NULL,
        activity_id   INTEGER,
        time_range    TEXT,
        app_summary   TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',  -- pending / answered / timeout
        answer        TEXT,
        asked_at      TEXT DEFAULT (datetime('now', 'localtime')),
        answered_at   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_inquiries_date   ON daily_report_inquiries(report_date);
    CREATE INDEX IF NOT EXISTS idx_inquiries_status ON daily_report_inquiries(status);

    -- 学习时段计划
    CREATE TABLE IF NOT EXISTS study_schedules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        day_of_week INTEGER,
        start_time  TEXT NOT NULL,
        end_time    TEXT NOT NULL,
        is_active   INTEGER DEFAULT 1,
        label       TEXT
    );

    -- 设备信息
    CREATE TABLE IF NOT EXISTS devices (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        device_type TEXT NOT NULL,
        last_sync   DATETIME,
        is_trusted  INTEGER DEFAULT 0
    );

    -- 同步日志
    CREATE TABLE IF NOT EXISTS sync_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        synced_at     DATETIME NOT NULL,
        source_device TEXT NOT NULL,
        record_count  INTEGER,
        status        TEXT
    );

    -- 聊天会话元数据表（多会话管理）
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id           TEXT PRIMARY KEY,          -- UUID
        title        TEXT NOT NULL DEFAULT '新对话',
        system_prompt TEXT,                     -- 会话级 system prompt（NULL 时用全局）
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        message_count INTEGER DEFAULT 0,
        last_preview TEXT                       -- 最后一条消息的前50字
    );

    -- 聊天消息表
    CREATE TABLE IF NOT EXISTS chat_messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT NOT NULL,
        role         TEXT NOT NULL,             -- 'user' | 'assistant' | 'tool'
        content      TEXT NOT NULL,
        tool_name    TEXT,                      -- tool 消息时的工具名
        images       TEXT,                      -- JSON 数组，存图片文件绝对路径
        created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);

    -- Phase 3: 用户事实记忆（长期记忆 L3）
    CREATE TABLE IF NOT EXISTS user_facts (
        fact_id        TEXT PRIMARY KEY,
        content        TEXT NOT NULL,
        content_hash   TEXT UNIQUE,
        category       TEXT NOT NULL DEFAULT 'status',
        source_session TEXT,
        confidence     REAL DEFAULT 0.8,
        created_at     DATETIME NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_facts_category ON user_facts(category);
    CREATE INDEX IF NOT EXISTS idx_facts_hash ON user_facts(content_hash);

    -- Phase 3: 异步记忆提取任务队列
    CREATE TABLE IF NOT EXISTS memory_jobs (
        job_id      TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL UNIQUE,
        status      TEXT DEFAULT 'pending',
        created_at  DATETIME NOT NULL,
        processed_at DATETIME,
        error       TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memory_jobs_status ON memory_jobs(status);

    -- 索引
    -- 情绪时间序列记录（摄像头采集）
    CREATE TABLE IF NOT EXISTS emotion_records (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        detected_at         DATETIME NOT NULL,
        user_present        INTEGER NOT NULL DEFAULT 1,  -- 0=不在, 1=在
        emotion             TEXT,                         -- happy/neutral/sad/angry/fear/surprise/disgust
        confidence          REAL,                         -- 0.0~1.0
        face_count          INTEGER DEFAULT 0,
        window_activity_id  INTEGER,                      -- 关联当时的活动段
        FOREIGN KEY (window_activity_id) REFERENCES window_activities(id)
    );

    CREATE INDEX IF NOT EXISTS idx_activities_started_at ON window_activities(started_at);
    CREATE INDEX IF NOT EXISTS idx_activities_process    ON window_activities(process_name);
    CREATE INDEX IF NOT EXISTS idx_activities_device     ON window_activities(device_id);
    CREATE INDEX IF NOT EXISTS idx_screenshots_captured  ON screenshots(captured_at);
    CREATE INDEX IF NOT EXISTS idx_reports_date          ON daily_reports(report_date);
    CREATE INDEX IF NOT EXISTS idx_emotion_detected_at   ON emotion_records(detected_at);
    CREATE INDEX IF NOT EXISTS idx_emotion_emotion       ON emotion_records(emotion);

    -- ── Phase 2: Agent 会话统计表（支持 InsightsEngine）────────────────────
    CREATE TABLE IF NOT EXISTS agent_sessions (
        id              TEXT PRIMARY KEY,          -- session.key (channel:chat_id)
        started_at      REAL NOT NULL,             -- unix timestamp
        ended_at        REAL,
        message_count   INTEGER DEFAULT 0,
        tool_call_count INTEGER DEFAULT 0,
        input_tokens    INTEGER DEFAULT 0,
        output_tokens   INTEGER DEFAULT 0,
        model           TEXT,
        source          TEXT DEFAULT 'navi'
    );
    CREATE INDEX IF NOT EXISTS idx_agent_sessions_started ON agent_sessions(started_at);
    CREATE INDEX IF NOT EXISTS idx_agent_sessions_source  ON agent_sessions(source);

    -- ── Phase 2: 工具调用日志（支持 InsightsEngine 分析工具使用模式）────────
    CREATE TABLE IF NOT EXISTS tool_call_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT NOT NULL,
        tool_name    TEXT NOT NULL,
        called_at    REAL NOT NULL,               -- unix timestamp
        success      INTEGER DEFAULT 1,           -- 1=成功, 0=失败
        duration_ms  INTEGER,                     -- 耗时毫秒
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
    );
    CREATE INDEX IF NOT EXISTS idx_tool_log_session   ON tool_call_log(session_id);
    CREATE INDEX IF NOT EXISTS idx_tool_log_tool      ON tool_call_log(tool_name);
    CREATE INDEX IF NOT EXISTS idx_tool_log_called_at ON tool_call_log(called_at);

    -- ── P1-3: Skill 使用日志（InsightsEngine → Skill 反馈闭环）──────────────
    -- 与 tool_call_log 解耦的原因：tool_call_log 不存 arguments，无法从
    -- read_file('xxx/SKILL.md') 反推到 skill 名。这里直接以 skill_name 为粒度。
    CREATE TABLE IF NOT EXISTS skill_usage_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_name   TEXT NOT NULL,
        used_at      REAL NOT NULL,             -- unix timestamp
        success      INTEGER DEFAULT 1,         -- 1=load 成功, 0=失败
        session_id   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_skill_log_name    ON skill_usage_log(skill_name);
    CREATE INDEX IF NOT EXISTS idx_skill_log_used_at ON skill_usage_log(used_at);

    -- ── FTS5 全文检索虚拟表（跨会话语义搜索）────────────────────────────────
    -- 内容表模式：content 映射到 chat_messages，无需双写
    CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts
        USING fts5(
            content,
            content='chat_messages',
            content_rowid='id',
            tokenize='unicode61'
        );

    -- FTS 触发器：保持虚拟表与 chat_messages 同步
    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_insert
        AFTER INSERT ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
        END;

    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_delete
        BEFORE DELETE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;

    CREATE TRIGGER IF NOT EXISTS chat_messages_fts_update
        AFTER UPDATE ON chat_messages BEGIN
            INSERT INTO chat_messages_fts(chat_messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
    """)

    conn.commit()

    # ── Migration: 给已有 chat_messages 表补 images 字段 ──
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
        if "images" not in cols:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN images TEXT")
            conn.commit()
            print("✅ Migration: chat_messages 新增 images 字段")
    except Exception as e:
        print(f"⚠️  Migration chat_messages 检查跳过: {e}")

    # ── Migration: 给已有 window_activities 表补缺失字段 ──
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(window_activities)").fetchall()]
        if "classification_source" not in cols:
            conn.execute(
                "ALTER TABLE window_activities ADD COLUMN classification_source TEXT DEFAULT 'rule'"
            )
            conn.commit()
            print("✅ Migration: window_activities 新增 classification_source 字段")
        if "parent_id" not in cols:
            conn.execute(
                "ALTER TABLE window_activities ADD COLUMN parent_id INTEGER DEFAULT NULL"
            )
            conn.commit()
            print("✅ Migration: window_activities 新增 parent_id 字段（父子活动层级）")
    except Exception as e:
        print(f"⚠️  Migration 检查跳过: {e}")

    # ── Migration: 重建 FTS5 索引（已有历史消息补全）────────────────────────
    try:
        # 检查 FTS 表是否已存在（虚拟表在 sqlite_master 里 type='table'）
        fts_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages_fts'"
        ).fetchone()
        if fts_exists:
            # 检查 FTS 索引是否为空（说明是第一次建表，需要从存量数据重建）
            fts_count = conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()[0]
            msg_count = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
            if fts_count == 0 and msg_count > 0:
                conn.execute(
                    "INSERT INTO chat_messages_fts(rowid, content) "
                    "SELECT id, content FROM chat_messages WHERE content IS NOT NULL"
                )
                conn.commit()
                print(f"✅ Migration: FTS5 索引重建完成，共索引 {msg_count} 条历史消息")
    except Exception as e:
        print(f"⚠️  FTS5 Migration 跳过: {e}")

    conn.close()
    print(f"✅ 数据库初始化完成：{db_path}")
