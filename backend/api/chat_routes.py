"""
聊天会话管理 REST API
- GET    /api/chat/sessions          列出所有会话
- POST   /api/chat/sessions          新建会话
- PATCH  /api/chat/sessions/{id}     更新会话标题 / system_prompt
- DELETE /api/chat/sessions/{id}     删除会话
- GET    /api/chat/sessions/{id}/messages  获取消息历史
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_config

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 主动对话专用会话 ID（不可删除）
PROACTIVE_SESSION_ID = "__proactive__"
PROACTIVE_SESSION_TITLE = "主动对话"

# 微信对话专用会话 ID（不可删除）
WECHAT_SESSION_ID = "__wechat__"
WECHAT_SESSION_TITLE = "微信对话"


def _get_db() -> sqlite3.Connection:
    cfg = get_config()
    db_path = Path(cfg.data_dir) / "app.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── 请求/响应模型 ─────────────────────────────────────────────

class CreateSessionReq(BaseModel):
    title: str = "新对话"
    system_prompt: Optional[str] = None


class UpdateSessionReq(BaseModel):
    title: Optional[str] = None
    system_prompt: Optional[str] = None


# ── 路由 ──────────────────────────────────────────────────────

@router.get("/sessions")
def list_sessions():
    """列出所有会话，主动对话置顶，其余按最近更新排序"""
    conn = _get_db()
    try:
        # 确保固定会话存在
        _ensure_proactive_session(conn)
        _ensure_wechat_session(conn)
        
        rows = conn.execute(
            "SELECT id, title, system_prompt, created_at, updated_at, message_count, last_preview "
            "FROM chat_sessions ORDER BY updated_at DESC"
        ).fetchall()
        
        # 把固定会话移到最前面（微信 → 主动对话 → 其余）
        result = []
        proactive_session = None
        wechat_session = None
        for r in rows:
            d = dict(r)
            if d["id"] == PROACTIVE_SESSION_ID:
                d["is_proactive"] = True
                proactive_session = d
            elif d["id"] == WECHAT_SESSION_ID:
                d["is_wechat"] = True
                wechat_session = d
            else:
                result.append(d)
        
        # 固定会话置顶：主动对话 → 微信对话 → 普通对话
        if wechat_session:
            result.insert(0, wechat_session)
        if proactive_session:
            result.insert(0, proactive_session)
        
        return result
    finally:
        conn.close()


def _ensure_proactive_session(conn: sqlite3.Connection):
    """确保主动对话会话存在"""
    row = conn.execute(
        "SELECT id FROM chat_sessions WHERE id=?", (PROACTIVE_SESSION_ID,)
    ).fetchone()
    if not row:
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (PROACTIVE_SESSION_ID, PROACTIVE_SESSION_TITLE, now, now)
        )
        conn.commit()


def _ensure_wechat_session(conn: sqlite3.Connection):
    """确保微信对话会话存在"""
    row = conn.execute(
        "SELECT id FROM chat_sessions WHERE id=?", (WECHAT_SESSION_ID,)
    ).fetchone()
    if not row:
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (WECHAT_SESSION_ID, WECHAT_SESSION_TITLE, now, now)
        )
        conn.commit()


@router.post("/sessions", status_code=201)
def create_session(req: CreateSessionReq):
    """新建会话"""
    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (id, title, system_prompt, created_at, updated_at) VALUES (?,?,?,?,?)",
            (session_id, req.title, req.system_prompt, now, now)
        )
        conn.commit()
        return {"id": session_id, "title": req.title, "system_prompt": req.system_prompt,
                "created_at": now, "updated_at": now, "message_count": 0, "last_preview": None}
    finally:
        conn.close()


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, req: UpdateSessionReq):
    """更新会话标题或 system_prompt"""
    # 主动对话会话不允许修改标题
    if session_id == PROACTIVE_SESSION_ID and req.title is not None:
        raise HTTPException(status_code=403, detail="主动对话会话不可重命名")
    
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")

        updates, params = [], []
        if req.title is not None:
            updates.append("title=?"); params.append(req.title)
        if req.system_prompt is not None:
            updates.append("system_prompt=?"); params.append(req.system_prompt)
        if not updates:
            raise HTTPException(status_code=400, detail="没有可更新的字段")

        updates.append("updated_at=?"); params.append(datetime.now().isoformat())
        params.append(session_id)
        conn.execute(f"UPDATE chat_sessions SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话及其所有消息"""
    # 固定会话不允许删除
    if session_id == PROACTIVE_SESSION_ID:
        raise HTTPException(status_code=403, detail="主动对话会话不可删除")
    if session_id == WECHAT_SESSION_ID:
        raise HTTPException(status_code=403, detail="微信对话会话不可删除")
    
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str, limit: int = 100, offset: int = 0):
    """获取会话消息历史，images 字段返回 /media/{filename} 相对路径列表"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="会话不存在")
        rows = conn.execute(
            "SELECT id, role, content, tool_name, images, created_at FROM chat_messages "
            "WHERE session_id=? ORDER BY id ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            raw_images = d.pop("images", None)
            if raw_images:
                try:
                    filenames = json.loads(raw_images)
                    # 返回 /media/filename 相对路径，前端自行拼接后端 base URL
                    d["images"] = [f"/media/{fn}" for fn in filenames]
                except Exception:
                    d["images"] = []
            else:
                d["images"] = []
            result.append(d)
        return result
    finally:
        conn.close()


# ── 内部工具函数（供 local_ws 调用）─────────────────────────

def save_message(
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
    images: list[str] | None = None,
):
    """把一条消息写入 DB，并更新会话元数据（同步，在 asyncio 线程里调用用 run_in_executor）
    
    images: 持久化后的图片文件名列表（如 ['20260426_175200_a1b2c3.jpg']），序列化为 JSON 存储
    """
    conn = _get_db()
    try:
        now = datetime.now().isoformat()
        images_json = json.dumps(images, ensure_ascii=False) if images else None
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, tool_name, images, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, role, content, tool_name, images_json, now)
        )
        preview = content[:50].replace("\n", " ")
        conn.execute(
            "UPDATE chat_sessions SET updated_at=?, last_preview=?, message_count=message_count+1 WHERE id=?",
            (now, preview, session_id)
        )
        conn.commit()
    finally:
        conn.close()


def ensure_session(session_id: str, title: str = "新对话") -> str:
    """确保 session_id 对应的会话存在，不存在则创建。返回 session_id。"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                (session_id, title, now, now)
            )
            conn.commit()
        return session_id
    finally:
        conn.close()
