"""
Scratchpad - ReAct 循环内的结构化便签本。
把 "试过什么 / 学到什么 / 下一步做什么" 从 messages 里抽出来作为单独段落注入。
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class ScratchEntry:
    iteration: int
    action: str          # tool name + 简要参数
    observation: str     # tool 返回精炼摘要（≤120 字）
    success: bool
    note: str = ""       # LLM 自己写的"学到什么"


@dataclass
class Scratchpad:
    goal: str = ""
    entries: List[ScratchEntry] = field(default_factory=list)
    learned_dont_repeat: List[str] = field(default_factory=list)

    def add(self, entry: ScratchEntry) -> None:
        self.entries.append(entry)
        if not entry.success and entry.note:
            self.learned_dont_repeat.append(f"iter {entry.iteration}: {entry.note}")

    def to_prompt(self) -> str:
        if not self.entries:
            return ""
        lines = ["<scratchpad>", f"目标：{self.goal}", "", "已尝试："]
        for e in self.entries[-10:]:
            status = "✓" if e.success else "✗"
            lines.append(f"  [{status}] iter{e.iteration}: {e.action} → {e.observation[:80]}")
        if self.learned_dont_repeat:
            lines.append("")
            lines.append("⚠ 不要重复的失败：")
            for x in self.learned_dont_repeat[-5:]:
                lines.append(f"  - {x}")
        lines.append("</scratchpad>")
        return "\n".join(lines)


def summarize_args(args: dict) -> str:
    """将工具参数压缩为简短摘要（≤80 字符）。"""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 30:
            s = s[:27] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)[:80]
