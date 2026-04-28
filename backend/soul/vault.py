"""
Soul Vault — 灵魂仓库

多灵魂管理：存储、切换、编辑、激活。
灵魂文件存储在 ~/.navi/soul-vault/{soul_id}.md
激活状态记录在 ~/.navi/soul-vault/vault.json
"""
import json
import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from soul.schema import validate_soul

logger = logging.getLogger("navi.soul.vault")

NAVI_HOME = Path.home() / ".navi"
VAULT_DIR = NAVI_HOME / "soul-vault"
VAULT_INDEX = VAULT_DIR / "vault.json"
SOUL_OUTPUT_PATH = Path(__file__).parent.parent / "templates" / "SOUL.md"


def _ensure_vault():
    VAULT_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> dict:
    """加载仓库索引"""
    _ensure_vault()
    if VAULT_INDEX.exists():
        try:
            return json.loads(VAULT_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"active_id": None, "souls": []}


def _save_index(index: dict):
    VAULT_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_name(content: str) -> str:
    """从 soul.md 内容中提取角色名（第一行 # 标题）"""
    first_line = content.strip().split("\n")[0] if content.strip() else ""
    # 匹配 # xxx · Navi 的灵魂  或  # xxx — yyy
    match = re.match(r"^#\s*(.+?)(?:\s*[·—\-|]\s*.+)?$", first_line)
    if match:
        return match.group(1).strip()
    return "未命名灵魂"


def list_souls() -> dict:
    """列出仓库中所有灵魂"""
    index = _load_index()
    result = []
    for soul in index["souls"]:
        soul_path = VAULT_DIR / f"{soul['id']}.md"
        content = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
        v = validate_soul(content)
        result.append({
            "id": soul["id"],
            "name": soul["name"],
            "active": soul["id"] == index["active_id"],
            "created_at": soul.get("created_at", ""),
            "updated_at": soul.get("updated_at", ""),
            "completeness": v.completeness,
            "valid": v.valid,
            "preview": content[:150] + "..." if len(content) > 150 else content,
        })
    return {"active_id": index["active_id"], "souls": result}


def add_soul(content: str, name: str = "") -> dict:
    """添加一个灵魂到仓库"""
    _ensure_vault()
    index = _load_index()

    soul_id = f"soul_{uuid.uuid4().hex[:8]}"
    if not name:
        name = _extract_name(content)
    now = datetime.now().isoformat()

    # 保存文件
    soul_path = VAULT_DIR / f"{soul_id}.md"
    soul_path.write_text(content, encoding="utf-8")

    # 更新索引
    index["souls"].append({
        "id": soul_id,
        "name": name,
        "created_at": now,
        "updated_at": now,
    })
    _save_index(index)

    logger.info(f"📥 灵魂入库: [{soul_id}] {name}")
    return {"id": soul_id, "name": name}


def get_soul(soul_id: str) -> dict:
    """获取一个灵魂的完整内容"""
    index = _load_index()
    soul_meta = next((s for s in index["souls"] if s["id"] == soul_id), None)
    if not soul_meta:
        return {"error": "灵魂不存在"}

    soul_path = VAULT_DIR / f"{soul_id}.md"
    content = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
    v = validate_soul(content)

    return {
        "id": soul_id,
        "name": soul_meta["name"],
        "content": content,
        "active": soul_id == index["active_id"],
        "validation": {
            "valid": v.valid,
            "completeness": v.completeness,
            "missing_required": v.missing_required,
            "missing_recommended": v.missing_recommended,
            "warnings": v.warnings,
        },
    }


def update_soul(soul_id: str, content: str, name: str = "") -> dict:
    """更新（编辑）一个灵魂"""
    index = _load_index()
    soul_meta = next((s for s in index["souls"] if s["id"] == soul_id), None)
    if not soul_meta:
        return {"error": "灵魂不存在"}

    # 写入文件
    soul_path = VAULT_DIR / f"{soul_id}.md"
    soul_path.write_text(content, encoding="utf-8")

    # 更新元数据
    if name:
        soul_meta["name"] = name
    soul_meta["updated_at"] = datetime.now().isoformat()
    _save_index(index)

    # 如果当前是激活的灵魂，同步到 templates/SOUL.md
    if soul_id == index["active_id"]:
        _apply_soul(content)

    logger.info(f"✏️ 灵魂已更新: [{soul_id}] {soul_meta['name']}")
    v = validate_soul(content)
    return {"id": soul_id, "name": soul_meta["name"], "completeness": v.completeness}


def activate_soul(soul_id: str) -> dict:
    """激活一个灵魂（写入 templates/SOUL.md）"""
    index = _load_index()
    soul_meta = next((s for s in index["souls"] if s["id"] == soul_id), None)
    if not soul_meta:
        return {"error": "灵魂不存在"}

    soul_path = VAULT_DIR / f"{soul_id}.md"
    if not soul_path.exists():
        return {"error": "灵魂文件丢失"}

    content = soul_path.read_text(encoding="utf-8")
    _apply_soul(content)

    index["active_id"] = soul_id
    _save_index(index)

    logger.info(f"✅ 灵魂已激活: [{soul_id}] {soul_meta['name']}")
    return {"id": soul_id, "name": soul_meta["name"], "active": True}


def delete_soul(soul_id: str) -> dict:
    """删除一个灵魂"""
    index = _load_index()
    soul_meta = next((s for s in index["souls"] if s["id"] == soul_id), None)
    if not soul_meta:
        return {"error": "灵魂不存在"}

    # 不允许删除当前激活的灵魂
    if soul_id == index["active_id"]:
        return {"error": "不能删除当前激活的灵魂，请先切换到其他灵魂"}

    # 删除文件
    soul_path = VAULT_DIR / f"{soul_id}.md"
    if soul_path.exists():
        soul_path.unlink()

    # 从索引移除
    index["souls"] = [s for s in index["souls"] if s["id"] != soul_id]
    _save_index(index)

    logger.info(f"🗑️ 灵魂已删除: [{soul_id}] {soul_meta['name']}")
    return {"ok": True}


def _apply_soul(content: str):
    """将灵魂内容写入 backend/templates/SOUL.md（正式生效）"""
    SOUL_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOUL_OUTPUT_PATH.write_text(content, encoding="utf-8")
    logger.info(f"[Soul] ✅ SOUL.md 已写入: {SOUL_OUTPUT_PATH}")


def ensure_current_in_vault():
    """
    启动时调用：如果 templates/SOUL.md 存在但仓库为空，
    自动把它导入仓库并标记为激活。
    """
    index = _load_index()
    if index["souls"]:
        return  # 仓库已有内容，不处理

    if SOUL_OUTPUT_PATH.exists():
        content = SOUL_OUTPUT_PATH.read_text(encoding="utf-8")
        if content.strip():
            result = add_soul(content)
            index = _load_index()
            index["active_id"] = result["id"]
            _save_index(index)
            logger.info(f"📦 已将现有 SOUL.md 导入仓库并激活: {result['name']}")
