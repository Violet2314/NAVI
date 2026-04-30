"""
USER.md 渲染器 (UserModelManager)

将 USER.md 从「LLM 辩证生成」降级为「FactMemory 自动投影」。
USER.md 不再是数据源，而是 FactMemory 的只读 Markdown 视图。

设计原则：
  - SSOT：用户事实的唯一权威是 FactMemory（backend/data/app.db.user_facts）
  - USER.md 仅作为 system prompt 可读视图，由本类按 category 分组渲染
  - 不再调用 LLM，零 token 成本，零幻觉风险
  - 失败时保留旧版本（FactMemory 仍是真相）
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("navi.memory.user_model")


# ── 渲染配置 ─────────────────────────────────────────────────────────────────

# category 顺序与中文标签（与 fact_memory.py 的 _EXTRACTION_PROMPT 保持一致）
_CATEGORY_ORDER: list[tuple[str, str]] = [
    ("biographical", "基本信息"),
    ("skill", "技能与技术栈"),
    ("preference", "偏好"),
    ("habit", "习惯"),
    ("relationship", "人际关系"),
    ("status", "近期状态"),
]

# 写入 USER.md 的最低置信度门槛（避免噪声进入 system prompt）
_MIN_CONFIDENCE = 0.6

# 单类目最多渲染条数（避免长尾事实压垮 prompt）
_MAX_PER_CATEGORY = 20

_HEADER_NOTICE = (
    "<!-- 此文件由 FactMemory 自动渲染，请勿手动编辑 -->\n"
    "<!-- Source of Truth: backend/data/app.db.user_facts -->\n"
    "<!-- 如需新增/修正事实，请通过对话让 Navi 自然记录，或直接修改 SQLite -->\n"
)


class UserModelManager:
    """
    USER.md 渲染器（从 FactMemory 投影，不再 LLM 生成）。

    Usage:
        manager = UserModelManager(workspace)
        await manager.update_after_session(messages)  # 签名兼容，messages 被忽略
        # 等价于：
        manager.render_from_facts()
    """

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._user_md_path = workspace / "templates" / "USER.md"
        self._backup_path = workspace / "templates" / "USER.md.bak"

    def _write_model(self, content: str) -> None:
        """写入新的 USER.md 内容，写入前备份旧版本。"""
        if self._user_md_path.exists():
            shutil.copy2(self._user_md_path, self._backup_path)
        self._user_md_path.parent.mkdir(parents=True, exist_ok=True)
        self._user_md_path.write_text(content, encoding="utf-8")

    def _render_profile_from_facts(self, facts: list[dict]) -> str:
        """
        将 FactMemory 中的事实按 category 分组渲染为 Markdown。

        过滤规则：
          - confidence < _MIN_CONFIDENCE 的丢弃
          - 每个 category 最多 _MAX_PER_CATEGORY 条
          - 全部为空时返回空字符串
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 按 category 分组（先过滤低置信度）
        groups: dict[str, list[dict]] = {}
        for f in facts:
            try:
                conf = float(f.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            if conf < _MIN_CONFIDENCE:
                continue
            cat = f.get("category") or "status"
            groups.setdefault(cat, []).append(f)

        # 检查是否有任何类目有内容
        if not any(groups.get(k) for k, _ in _CATEGORY_ORDER) and not groups:
            return ""

        lines: list[str] = [
            _HEADER_NOTICE.rstrip(),
            "",
            f"# 用户画像（自动渲染 · 最后更新：{timestamp}）",
            "",
        ]

        wrote_any_section = False

        # 已知 category 按预定义顺序输出
        for cat_key, cat_label in _CATEGORY_ORDER:
            items = groups.pop(cat_key, [])
            if not items:
                continue
            lines.append(f"## {cat_label}")
            for f in items[:_MAX_PER_CATEGORY]:
                content = (f.get("content") or "").strip()
                if content:
                    lines.append(f"- {content}")
            lines.append("")
            wrote_any_section = True

        # 未知 category 兜底（防止 LLM 提取出新分类被丢弃）
        for cat_key, items in groups.items():
            if not items:
                continue
            lines.append(f"## {cat_key}")
            for f in items[:_MAX_PER_CATEGORY]:
                content = (f.get("content") or "").strip()
                if content:
                    lines.append(f"- {content}")
            lines.append("")
            wrote_any_section = True

        if not wrote_any_section:
            return ""

        return "\n".join(lines).rstrip() + "\n"

    def render_from_facts(self) -> bool:
        """
        从 FactMemory 拉取所有事实，渲染并写入 USER.md。

        Returns:
            True = 已写入新内容，False = 跳过（无事实或失败）
        """
        try:
            from memory.fact_memory import get_fact_memory
            fact_mem = get_fact_memory()
            facts = fact_mem.get_all_facts(limit=200)
        except Exception as e:
            logger.warning(f"[UserModel] FactMemory 不可用，跳过渲染: {e}")
            return False

        if not facts:
            logger.debug("[UserModel] FactMemory 为空，跳过 USER.md 渲染")
            return False

        rendered = self._render_profile_from_facts(facts)
        if not rendered:
            logger.debug("[UserModel] 没有满足置信度门槛的事实，跳过写入")
            return False

        try:
            self._write_model(rendered)
            logger.info(
                f"[UserModel] USER.md 已从 FactMemory 渲染（{len(facts)} 条原始 facts，"
                f"{len(rendered)} 字符）"
            )
            return True
        except Exception as e:
            logger.warning(f"[UserModel] 写入 USER.md 失败: {e}")
            return False

    async def update_after_session(self, messages: list[dict]) -> bool:
        """
        会话结束钩子（签名向后兼容）。

        messages 参数已不再使用——用户事实由 FactMemory 通过
        on_pre_compress / memory_worker 自动从对话中抽取。
        本方法只负责把 FactMemory 的最新状态投影到 USER.md。
        """
        return self.render_from_facts()

    def restore_backup(self) -> bool:
        """[DEPRECATED] 恢复上一个备份版本。

        USER.md 现在是纯渲染产物，备份文件已删除（P0-D）。
        此方法保留仅为向后兼容，始终返回 False。
        """
        import warnings
        warnings.warn(
            "UserModelManager.restore_backup() is deprecated. "
            "USER.md is now a pure render product; backup has been removed (P0-D).",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning("[UserModel] restore_backup 已废弃（P0-D），备份文件不存在")
        return False


# ── 单例 ─────────────────────────────────────────────────────────────────────

_user_model_manager: Optional[UserModelManager] = None


def get_user_model_manager(workspace: Path) -> UserModelManager:
    """获取全局单例 UserModelManager。"""
    global _user_model_manager
    if _user_model_manager is None:
        _user_model_manager = UserModelManager(workspace)
    return _user_model_manager