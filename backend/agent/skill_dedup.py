"""
Skill 硬去重引擎（第三层补全）

Hermes 对应模块: tools/skills_hub.py _dedupe_results() + website/scripts/extract-skills.py

功能：
  1. 扫描 workspace/skills/ 目录，检测同名/相似名 Skill
  2. 本地 Skill 优先：同名 builtin Skill 自动跳过（已在 SkillsLoader 实现）
  3. 小分类合并：Skill 名前缀相同且内容高度重复的，生成合并建议
  4. 注入提示词：把去重摘要作为 AI 的维护提醒注入一次

调用方式：
    from agent.skill_dedup import SkillDeduplicator
    dedup = SkillDeduplicator(workspace)
    report = dedup.scan()          # 返回结构化报告
    hint = dedup.format_hint(report)  # 注入 AI 系统提示的简短提醒
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NamedTuple


class SkillRecord(NamedTuple):
    name: str
    path: Path
    description: str
    content_hash: str   # sha256 前16字节，快速比较


class DedupeReport(NamedTuple):
    total: int
    exact_dupes: list[tuple[str, str]]   # (name_a, name_b) 内容完全相同
    similar_names: list[tuple[str, str]] # (name_a, name_b) 名称高度相似
    hint_needed: bool                    # 是否需要注入 AI 提醒


class SkillDeduplicator:
    """
    扫描 workspace/skills/ 目录，检测重复和相似 Skill。

    设计原则：
    - 轻量：只做文件扫描，不调用 LLM（成本为零）
    - 幂等：多次调用结果相同
    - 非侵入：只生成报告和提示词片段，不自动删除任何文件
    """

    _SIMILARITY_THRESHOLD = 0.75  # 名称 Jaccard 相似度阈值

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.skills_dir = workspace / "skills"

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def scan(self) -> DedupeReport:
        """扫描所有 workspace Skill，返回去重报告。"""
        if not self.skills_dir.exists():
            return DedupeReport(total=0, exact_dupes=[], similar_names=[], hint_needed=False)

        records = self._load_records()
        exact_dupes = self._find_exact_dupes(records)
        similar_names = self._find_similar_names(records)
        hint_needed = bool(exact_dupes or similar_names)

        return DedupeReport(
            total=len(records),
            exact_dupes=exact_dupes,
            similar_names=similar_names,
            hint_needed=hint_needed,
        )

    def format_hint(self, report: DedupeReport) -> str:
        """
        生成注入 AI 系统提示的维护提醒（≤300字）。
        只在 hint_needed=True 时返回非空字符串。
        """
        if not report.hint_needed:
            return ""

        lines = ["<skill_maintenance_hint>"]
        lines.append("⚠️ 检测到 Skill 库存在以下问题，请在本次会话结束前清理：")

        if report.exact_dupes:
            lines.append(f"\n内容完全相同的 Skill（选一个保留，删除另一个）：")
            for a, b in report.exact_dupes[:5]:  # 最多展示5条
                lines.append(f"  - {a}  ≡  {b}")

        if report.similar_names:
            lines.append(f"\n名称高度相似的 Skill（检查是否可以合并）：")
            for a, b in report.similar_names[:5]:
                lines.append(f"  - {a}  ≈  {b}")

        lines.append("\n使用 skill_manage(action='list') 查看，skill_manage(action='delete', name='xxx') 删除。")
        lines.append("</skill_maintenance_hint>")
        return "\n".join(lines)

    # ── 内部逻辑 ─────────────────────────────────────────────────────────────

    def _load_records(self) -> list[SkillRecord]:
        records: list[SkillRecord] = []
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                raw = skill_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            desc = self._extract_description(raw)
            # 去除前置 frontmatter 后计算哈希，避免只改了描述就误判为不同
            body = self._strip_frontmatter(raw).strip()
            content_hash = hashlib.sha256(body.encode()).hexdigest()[:16]
            records.append(SkillRecord(
                name=skill_dir.name,
                path=skill_file,
                description=desc,
                content_hash=content_hash,
            ))
        return records

    def _find_exact_dupes(self, records: list[SkillRecord]) -> list[tuple[str, str]]:
        """检测内容完全相同的 Skill（content_hash 碰撞）。"""
        seen: dict[str, str] = {}  # hash → first_name
        dupes: list[tuple[str, str]] = []
        for r in records:
            if r.content_hash in seen:
                dupes.append((seen[r.content_hash], r.name))
            else:
                seen[r.content_hash] = r.name
        return dupes

    def _find_similar_names(self, records: list[SkillRecord]) -> list[tuple[str, str]]:
        """检测名称高度相似的 Skill（Jaccard 字词级相似度）。"""
        similar: list[tuple[str, str]] = []
        names = [r.name for r in records]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if self._name_similarity(a, b) >= self._SIMILARITY_THRESHOLD:
                    similar.append((a, b))
        return similar

    @staticmethod
    def _name_similarity(a: str, b: str) -> float:
        """Jaccard 相似度：把名称按 `-_` 分词后计算集合交并比。"""
        def tokenize(s: str) -> set[str]:
            return set(re.split(r'[-_\s]+', s.lower()))
        ta, tb = tokenize(a), tokenize(b)
        if not ta or not tb:
            return 0.0
        intersection = len(ta & tb)
        union = len(ta | tb)
        return intersection / union if union else 0.0

    @staticmethod
    def _extract_description(content: str) -> str:
        """从 frontmatter 中提取 description 字段（简单解析，不依赖 yaml）。"""
        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if match:
            for line in match.group(1).splitlines():
                if line.startswith("description"):
                    _, _, val = line.partition(":")
                    return val.strip().strip('"\'')
        return ""

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        if content.startswith("---"):
            match = re.match(r'^---\n.*?\n---\n', content, re.DOTALL)
            if match:
                return content[match.end():]
        return content


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_deduplicator: SkillDeduplicator | None = None


def get_skill_deduplicator(workspace: Path | None = None) -> SkillDeduplicator | None:
    """获取全局单例 SkillDeduplicator。首次调用需传入 workspace。"""
    global _deduplicator
    if _deduplicator is None and workspace is not None:
        _deduplicator = SkillDeduplicator(workspace)
    return _deduplicator
