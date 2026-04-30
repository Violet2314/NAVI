"""
Scratchpad - ReAct 循环内的结构化便签本。
把 "试过什么 / 学到什么 / 下一步做什么" 从 messages 里抽出来作为单独段落注入。
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ScratchEntry:
    iteration: int
    action: str          # tool name + 简要参数
    observation: str     # tool 返回精炼摘要（≤120 字）
    success: bool
    tool_name: str = ""  # 仅 tool name（用于打转检测）


@dataclass
class Scratchpad:
    goal: str = ""
    entries: List[ScratchEntry] = field(default_factory=list)
    learned_dont_repeat: List[str] = field(default_factory=list)

    def add(self, entry: ScratchEntry) -> None:
        self.entries.append(entry)
        # 失败条目自动入"不要重复"清单，不再依赖外部传 note
        if not entry.success:
            self.learned_dont_repeat.append(
                f"iter {entry.iteration}: {entry.action} 失败 — {entry.observation[:80]}"
            )

    # ── 动态反思触发条件 ──────────────────────────────────────────────────
    def should_reflect(
        self,
        same_tool_streak: int = 3,
        consecutive_fail: int = 2,
        repeat_observation_window: int = 2,
    ) -> Optional[str]:
        """
        判断是否需要强制插入反思阶段。
        返回触发原因（str），None 表示不需要反思。

        触发条件（物理直觉）：
          - 连续 N 次调用同一工具 → 可能在无效重试
          - 连续 M 次工具失败 → 策略明显走偏
          - 最近 K 次 observation 高度相似 → 在打转
        """
        if len(self.entries) < 2:
            return None

        # 条件 1: 连续同名工具
        if len(self.entries) >= same_tool_streak:
            tail = self.entries[-same_tool_streak:]
            if all(e.tool_name and e.tool_name == tail[0].tool_name for e in tail):
                return f"连续 {same_tool_streak} 次调用同一工具 {tail[0].tool_name}"

        # 条件 2: 连续失败
        if len(self.entries) >= consecutive_fail:
            tail = self.entries[-consecutive_fail:]
            if all(not e.success for e in tail):
                return f"连续 {consecutive_fail} 次工具失败"

        # 条件 3: 最近两次 observation 高度相似（打转）
        if len(self.entries) >= repeat_observation_window + 1:
            recent = self.entries[-(repeat_observation_window + 1):]
            obs = [e.observation[:60] for e in recent if e.success]
            if len(obs) >= 2 and len(set(obs)) == 1 and obs[0]:
                return "最近的工具返回完全相同，可能在打转"

        return None

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