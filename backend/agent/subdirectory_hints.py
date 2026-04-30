"""
SubdirectoryHints — 目录进入时自动注入项目规则（第七层补全）

Hermes 对应模块: agent/subdirectory_hints.py SubdirectoryHintTracker

功能：
  当 AI 在工具调用中（list_dir / read_file / shell）涉及某个目录时，
  自动扫描该目录是否存在已知的规则文件，并将内容注入到下一轮 AI 上下文中，
  使 AI 立刻获知该项目的编码规范、禁止事项等。

支持的规则文件（优先级从高到低）：
    AGENTS.md / .cursorrules / CLAUDE.md / .claude / .rules

典型场景：
    AI 进入 my-project/ 目录 → 发现 AGENTS.md → 自动注入项目规则 → AI 遵从项目约定

使用方式：
    tracker = SubdirectoryHintTracker(workspace)
    hint = tracker.check_directory("/path/to/some/dir")
    # hint 非空时，注入到 AI 当前轮次的系统提示或 user 消息前缀
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("navi.subdirectory_hints")

# 按优先级排列的规则文件名
HINT_FILES = [
    "AGENTS.md",
    ".cursorrules",
    "CLAUDE.md",
    ".claude",
    ".rules",
]

# 单个规则文件最大读取字符数（防止恶意超大文件撑爆上下文）
MAX_HINT_CHARS = 4_000


class SubdirectoryHintTracker:
    """
    追踪 AI 进入的目录，自动发现并缓存项目规则。

    缓存机制：
    - 同一目录只读一次，后续命中直接返回缓存
    - workspace 根目录不触发（已由 ContextBuilder 固定加载）
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace.expanduser().resolve()
        # 缓存：path_str → hint_content（空字符串表示已扫描但无规则）
        self._cache: dict[str, str] = {}

    def check_directory(self, path: str | Path) -> str:
        """
        扫描指定目录是否有规则文件。

        Args:
            path: 要检查的目录路径（绝对或相对均可）

        Returns:
            规则文件内容（非空），或空字符串（无规则文件 / workspace根目录 / 已缓存为空）
        """
        try:
            resolved = Path(path).expanduser().resolve()
        except Exception:
            return ""

        # workspace 根目录跳过（已由 ContextBuilder.BOOTSTRAP_FILES 处理）
        if resolved == self.workspace:
            return ""

        cache_key = str(resolved)
        if cache_key in self._cache:
            return self._cache[cache_key]

        hint = self._scan(resolved)
        self._cache[cache_key] = hint
        if hint:
            logger.info("SubdirectoryHints: 注入规则 from %s", resolved)
        return hint

    def extract_paths_from_tool_call(self, tool_name: str, tool_args: dict) -> list[str]:
        """
        从工具调用参数中提取路径，供 loop.py 调用后触发检测。

        支持的工具：list_dir / read_file / write_file / edit_file / shell(exec)
        """
        paths: list[str] = []

        if tool_name in ("list_dir", "list_directory"):
            p = tool_args.get("path") or tool_args.get("dir")
            if p:
                paths.append(str(p))

        elif tool_name in ("read_file", "write_file", "edit_file", "replace_in_file"):
            p = tool_args.get("path") or tool_args.get("file_path") or tool_args.get("target_file")
            if p:
                # 取父目录
                paths.append(str(Path(p).parent))

        elif tool_name in ("exec", "shell", "run_command"):
            # 从 cwd 参数提取
            cwd = tool_args.get("cwd") or tool_args.get("working_dir")
            if cwd:
                paths.append(str(cwd))

        return paths

    def format_hint_block(self, hint: str, source_dir: str) -> str:
        """将规则内容包装成带标签的注入块。"""
        return (
            f"<subdirectory_rules path=\"{source_dir}\">\n"
            f"{hint}\n"
            f"</subdirectory_rules>"
        )

    def clear_cache(self) -> None:
        """清空缓存（会话结束时调用）。"""
        self._cache.clear()

    # ── 内部扫描 ──────────────────────────────────────────────────────────────

    def _scan(self, directory: Path) -> str:
        """扫描目录，按优先级查找并读取规则文件。"""
        if not directory.is_dir():
            return ""

        for filename in HINT_FILES:
            candidate = directory / filename
            if candidate.is_file():
                try:
                    content = candidate.read_text(encoding="utf-8", errors="ignore")
                    content = content[:MAX_HINT_CHARS]
                    if content.strip():
                        return content.strip()
                except OSError:
                    continue
        return ""


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_tracker: SubdirectoryHintTracker | None = None


def get_hint_tracker(workspace: Path | None = None) -> SubdirectoryHintTracker | None:
    """获取全局单例 SubdirectoryHintTracker。首次调用需传入 workspace。"""
    global _tracker
    if _tracker is None and workspace is not None:
        _tracker = SubdirectoryHintTracker(workspace)
    return _tracker
