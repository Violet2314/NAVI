"""
Scratchpad - ReAct 循环内的结构化便签本。
把 "试过什么 / 学到什么 / 下一步做什么" 从 messages 里抽出来作为单独段落注入。
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ScratchEntry:
    iteration: int
    action: str          # tool name + 简要参数（人类可读，会截断）
    observation: str     # tool 返回精炼摘要（≤120 字）
    success: bool
    tool_name: str = ""  # 仅 tool name（用于打转检测）
    call_sig: str = ""   # 不截断的调用签名（tool_name + 参数哈希），用于打转检测


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
        identical_call_streak: int = 3,
        same_tool_with_fail_streak: int = 3,
        consecutive_fail: int = 2,
        repeat_observation_window: int = 2,
    ) -> Optional[str]:
        """
        判断是否需要强制插入反思阶段。
        返回触发原因（str），None 表示不需要反思。

        触发条件（物理直觉）：
          1. 连续 N 次完全相同的调用（工具名+参数都相同）→ 真死循环
          2. 连续 N 次同一工具 且 其中至少 1 次失败 → 在"硬怼"一个失败路径
          3. 连续 M 次工具失败（不论是否同工具）→ 策略明显走偏
          4. 最近 K 次 observation 高度相似 → 原地打转

        说明：
          - "连续 N 次同一工具 且 全部成功 且 参数都不同"（例如依次读 3 个不同文件）
            是完全合理的 ReAct 探索行为，**不应触发反思**。
        """
        if len(self.entries) < 2:
            return None

        # 条件 1: 连续完全相同的调用（工具名+参数都完全一样）→ 真正的死循环
        # 用不截断的 call_sig（哈希）比较，避免 summarize_args 截断导致的误判
        if len(self.entries) >= identical_call_streak:
            tail = self.entries[-identical_call_streak:]
            sigs = [e.call_sig for e in tail]
            if sigs[0] and all(s == sigs[0] for s in sigs):
                return f"连续 {identical_call_streak} 次完全相同的调用：{tail[0].action[:60]}"

        # 条件 2: 连续同一工具 且 其中至少 1 次失败 → 在"硬怼"这个工具
        if len(self.entries) >= same_tool_with_fail_streak:
            tail = self.entries[-same_tool_with_fail_streak:]
            same_tool = all(e.tool_name and e.tool_name == tail[0].tool_name for e in tail)
            any_fail = any(not e.success for e in tail)
            if same_tool and any_fail:
                return f"连续 {same_tool_with_fail_streak} 次调用 {tail[0].tool_name} 且有失败"

        # 条件 3: 连续失败（不限工具）
        if len(self.entries) >= consecutive_fail:
            tail = self.entries[-consecutive_fail:]
            if all(not e.success for e in tail):
                return f"连续 {consecutive_fail} 次工具失败"

        # 条件 4: 最近两次 observation 高度相似（打转）
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
    """将工具参数压缩为简短摘要（≤80 字符），用于人类可读展示。"""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 30:
            s = s[:27] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)[:80]


def make_call_sig(tool_name: str, args: dict[str, Any] | None) -> str:
    """
    为一次工具调用生成稳定、不截断的签名，用于打转检测。

    签名 = 工具名 + 参数 JSON 的 SHA1（取前 16 位足以）。
    参数使用 sort_keys + default=str 确保同参数字典总是产生相同哈希。
    """
    try:
        payload = json.dumps(args or {}, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        payload = repr(args)
    h = hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{tool_name}:{h}"