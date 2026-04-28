"""
模糊补丁工具 (Fuzzy Match & Replace)

用于 Skill 内容的模糊局部替换，容忍空白/缩进差异。
在 AI 自动 patch SKILL.md 时比精确字符串匹配更鲁棒。
"""

from difflib import SequenceMatcher
from typing import Optional


def fuzzy_find_and_replace(
    content: str,
    old: str,
    new: str,
    threshold: float = 0.82,
) -> tuple[str, bool]:
    """
    在 content 中找到与 old 最相似的片段并替换为 new。

    Args:
        content:   原始文本
        old:       期望替换的旧内容（允许与实际内容有空白差异）
        new:       替换后的新内容
        threshold: 相似度阈值（0-1），低于此值不替换

    Returns:
        (新内容, 是否成功替换)
    """
    if not old.strip():
        return content, False

    # 规范化：去除首尾空白、合并连续空白（用于相似度比较，不影响输出）
    old_normalized = " ".join(old.split())
    old_lines = old.splitlines()
    window = max(len(old_lines), 1)

    content_lines = content.splitlines()
    if len(content_lines) < window:
        # 内容行数不足，降级为整体替换
        ratio = SequenceMatcher(None, old_normalized, " ".join(content.split())).ratio()
        if ratio >= threshold:
            return new, True
        return content, False

    best_start = -1
    best_ratio = 0.0

    for i in range(len(content_lines) - window + 1):
        chunk = content_lines[i : i + window]
        chunk_normalized = " ".join(" ".join(chunk).split())
        ratio = SequenceMatcher(None, old_normalized, chunk_normalized).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    if best_ratio >= threshold and best_start >= 0:
        result_lines = (
            content_lines[:best_start]
            + new.splitlines()
            + content_lines[best_start + window :]
        )
        return "\n".join(result_lines), True

    return content, False


def fuzzy_similarity(a: str, b: str) -> float:
    """计算两段文本的语义相似度（0-1），用于 Skill 重叠检测。"""
    a_norm = " ".join(a.lower().split())
    b_norm = " ".join(b.lower().split())
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def detect_similar_skills(
    skills: list[dict],
    threshold: float = 0.75,
) -> list[tuple[str, str, float]]:
    """
    检测名称或描述相似的 Skill，用于提示 AI 合并。

    Args:
        skills:    [{"name": "...", "description": "..."}, ...]
        threshold: 相似度阈值

    Returns:
        [(name_a, name_b, similarity), ...] 超过阈值的 Skill 对
    """
    similar_pairs = []
    for i in range(len(skills)):
        for j in range(i + 1, len(skills)):
            a = skills[i]
            b = skills[j]
            # 名称相似度（权重 0.6）+ 描述相似度（权重 0.4）
            name_sim = fuzzy_similarity(a.get("name", ""), b.get("name", ""))
            desc_sim = fuzzy_similarity(a.get("description", ""), b.get("description", ""))
            combined = name_sim * 0.6 + desc_sim * 0.4
            if combined >= threshold:
                similar_pairs.append((a["name"], b["name"], round(combined, 2)))
    return similar_pairs
