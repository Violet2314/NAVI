"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []

        # Workspace skills (highest priority)
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})

        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})

        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    # ── P1-3: Skill 健康度排序与打标阈值 ──────────────────────────────────────
    _STATS_WINDOW_DAYS = 7        # 拉取最近 N 天的使用统计
    _HOT_THRESHOLD = 5            # 7 天内调用 ≥ 5 次 → 高频
    _FAIL_RATE_THRESHOLD = 0.5    # 失败率 > 50% → 高失败率
    _STALE_DAYS = 30              # 创建 ≥ 30 天但 7 天内零调用 → 长期未用

    def _get_skill_usage_stats(self) -> dict[str, dict]:
        """
        从 InsightsEngine 拉取 skill 使用统计。失败时返回空 dict（优雅降级）。
        """
        try:
            from agent.insights_engine import get_skill_usage_stats
            from config import get_config
            db_path = str(Path(get_config().data_dir) / "app.db")
            return get_skill_usage_stats(db_path, days=self._STATS_WINDOW_DAYS)
        except Exception:
            return {}

    def _get_skill_age_days(self, name: str) -> int | None:
        """获取 skill 文件的存在天数（基于 SKILL.md 的 mtime）。失败返回 None。"""
        for base in (self.workspace_skills, self.builtin_skills):
            if base is None:
                continue
            skill_file = base / name / "SKILL.md"
            if skill_file.exists():
                try:
                    import time as _t
                    age_sec = _t.time() - skill_file.stat().st_mtime
                    return max(0, int(age_sec / 86400))
                except Exception:
                    return None
        return None

    def _build_health_tags(self, name: str, stats: dict, age_days: int | None) -> list[str]:
        """根据使用统计生成健康度标签（用于 system prompt 提示 LLM）。"""
        tags: list[str] = []
        count = stats.get("count", 0)
        fail_rate = stats.get("fail_rate", 0.0)

        if count >= self._HOT_THRESHOLD:
            tags.append(f"🔥 hot({count})")
        if count > 0 and fail_rate > self._FAIL_RATE_THRESHOLD:
            tags.append(f"❌ fail_rate={int(fail_rate * 100)}%")
        if count == 0 and age_days is not None and age_days >= self._STALE_DAYS:
            tags.append(f"⚠️ unused-{age_days}d")
        return tags

    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        P1-3 增强：
          - 接入 InsightsEngine 的 skill_usage_log 反馈
          - 按最近 7 天使用频次倒序排列（高频 skill 排前面，提高 LLM 命中率）
          - 标注健康度：🔥 hot / ❌ fail_rate / ⚠️ unused
          - LLM 据此动态决策：哪些 skill 该优先尝试、哪些可能已失效

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # ── 拉取使用统计并按调用次数倒序排序 ────────────────────────────────
        usage_stats = self._get_skill_usage_stats()
        all_skills.sort(
            key=lambda s: usage_stats.get(s["name"], {}).get("count", 0),
            reverse=True,
        )

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)

            # 健康度标签（基于最近 7 天的真实使用反馈）
            stats = usage_stats.get(s["name"], {})
            age_days = self._get_skill_age_days(s["name"])
            tags = self._build_health_tags(s["name"], stats, age_days)

            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            if tags:
                lines.append(f"    <usage>{escape_xml(' '.join(tags))}</usage>")

            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_skill_metadata(self, raw: str) -> dict:
        """Parse skill metadata JSON from frontmatter (supports navi/nanobot/openclaw keys for backward compat)."""
        try:
            data = json.loads(raw)
            return data.get("navi", data.get("nanobot", data.get("openclaw", {}))) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict:
        """Get navi metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_skill_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_skill_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content:
            return None

        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Simple YAML parsing
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata

        return None
