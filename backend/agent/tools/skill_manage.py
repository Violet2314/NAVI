"""
skill_manage 工具 — 让 AI 能对自己的技能库进行增删改操作。

操作类型：
  - create: 创建新技能
  - edit:   完整替换技能内容
  - patch:  模糊匹配局部修改（最常用，容忍空白差异）
  - delete: 删除冗余技能
  - list:   列出所有技能（附重叠检测提示）
"""

from pathlib import Path
from typing import Any

from agent.tools.base import Tool
from agent.skills import SkillsLoader, BUILTIN_SKILLS_DIR
from utils.fuzzy_match import fuzzy_find_and_replace, detect_similar_skills


class SkillManageTool(Tool):
    """让 AI 能对自己的技能库进行增删改查操作，实现 Skill 自进化。"""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._skills_dir = workspace / "skills"

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "管理 Navi 的技能库（Skills）。"
            "操作类型：create（创建新技能）、edit（完整替换技能内容）、"
            "patch（模糊局部修改，最常用）、delete（删除技能）、list（列出所有技能并检测重叠）。"
            "完成复杂任务后用 create 或 patch 保存成功经验；发现过时技能后主动用 patch/edit 更新。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "edit", "patch", "delete", "list"],
                    "description": "操作类型",
                },
                "name": {
                    "type": "string",
                    "description": "技能名称（目录名，如 'python-debug'）。list 操作时可省略。",
                },
                "content": {
                    "type": "string",
                    "description": "create/edit 时的完整 SKILL.md 内容（Markdown 格式）。",
                },
                "old_content": {
                    "type": "string",
                    "description": "patch 时要替换的旧内容片段。",
                },
                "new_content": {
                    "type": "string",
                    "description": "patch 时替换后的新内容片段。",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action", "").strip()
        name = (kwargs.get("name") or "").strip()

        if action == "list":
            return self._list_skills()

        if not name:
            return "错误：操作需要提供 name 参数。"

        if action == "create":
            content = kwargs.get("content", "").strip()
            if not content:
                return "错误：create 操作需要提供 content 参数。"
            return self._create_skill(name, content)

        elif action == "edit":
            content = kwargs.get("content", "").strip()
            if not content:
                return "错误：edit 操作需要提供 content 参数。"
            return self._edit_skill(name, content)

        elif action == "patch":
            old_content = kwargs.get("old_content", "")
            new_content = kwargs.get("new_content", "")
            if not old_content:
                return "错误：patch 操作需要提供 old_content 参数。"
            return self._patch_skill(name, old_content, new_content)

        elif action == "delete":
            return self._delete_skill(name)

        else:
            return f"错误：未知操作类型 '{action}'。支持：create、edit、patch、delete、list。"

    # ── 各操作实现 ────────────────────────────────────────────────────────────

    def _skill_path(self, name: str) -> Path:
        return self._skills_dir / name / "SKILL.md"

    def _create_skill(self, name: str, content: str) -> str:
        skill_file = self._skill_path(name)
        if skill_file.exists():
            return f"技能 '{name}' 已存在。如需修改请使用 edit 或 patch。"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(content, encoding="utf-8")
        return f"✅ 技能 '{name}' 已创建：{skill_file}"

    def _edit_skill(self, name: str, content: str) -> str:
        skill_file = self._skill_path(name)
        is_new = not skill_file.exists()
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(content, encoding="utf-8")
        action_word = "创建" if is_new else "完整更新"
        return f"✅ 技能 '{name}' 已{action_word}：{skill_file}"

    def _patch_skill(self, name: str, old_content: str, new_content: str) -> str:
        skill_file = self._skill_path(name)
        if not skill_file.exists():
            return f"错误：技能 '{name}' 不存在。请先用 create 创建，或检查名称是否正确。"

        current = skill_file.read_text(encoding="utf-8")
        updated, success = fuzzy_find_and_replace(current, old_content, new_content)

        if not success:
            return (
                f"❌ 模糊匹配失败：在技能 '{name}' 中找不到足够相似的内容片段（相似度 < 0.82）。\n"
                f"建议：用 edit 直接替换完整内容，或检查 old_content 是否与实际内容匹配。"
            )

        skill_file.write_text(updated, encoding="utf-8")
        return f"✅ 技能 '{name}' 已局部更新（模糊 patch 成功）。"

    def _delete_skill(self, name: str) -> str:
        skill_file = self._skill_path(name)
        if not skill_file.exists():
            return f"技能 '{name}' 不存在，无需删除。"

        import shutil
        skill_dir = skill_file.parent
        shutil.rmtree(skill_dir)
        return f"🗑️ 技能 '{name}' 已删除。"

    def _list_skills(self) -> str:
        loader = SkillsLoader(self._workspace, BUILTIN_SKILLS_DIR)
        all_skills = loader.list_skills(filter_unavailable=False)

        if not all_skills:
            return "当前没有任何技能。可以使用 skill_manage(action='create') 创建第一个技能。"

        lines = [f"共 {len(all_skills)} 个技能：\n"]
        skill_infos = []
        for s in all_skills:
            meta = loader.get_skill_metadata(s["name"]) or {}
            desc = meta.get("description", "（无描述）")
            source_tag = "[workspace]" if s["source"] == "workspace" else "[builtin]"
            lines.append(f"  - {s['name']} {source_tag}：{desc}")
            skill_infos.append({"name": s["name"], "description": desc})

        # 重叠检测
        similar = detect_similar_skills(skill_infos, threshold=0.75)
        if similar:
            lines.append("\n⚠️ 检测到以下技能可能存在重叠，考虑合并：")
            for a, b, sim in similar:
                lines.append(f"  - '{a}' 和 '{b}'（相似度 {sim:.0%}）")

        return "\n".join(lines)
