"""
Soul Schema — soul.md 标准格式定义 + 校验逻辑

无论是自动管道还是手动导入的 soul.md，都通过这里校验完整性。
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# ---------------------------------------------------------------------------
# soul.md 必须包含的章节
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = [
    "身份",
    "性格内核",
    "说话规则",
    "场景反应",
    "对话示例",
]

RECOMMENDED_SECTIONS = [
    "外貌特征",
    "标志性动作与小习惯",
    "背景设定",
    "角色原声参考",
    "禁忌",
]

# 场景反应子项
SCENARIO_SECTIONS = [
    "日报评语",
    "用户专注学习",
    "用户在摸鱼",
    "黑名单",
    "长时间无活动",
]


# ---------------------------------------------------------------------------
# 素材类型枚举
# ---------------------------------------------------------------------------

class MaterialType:
    TEXT = "text"            # 纯文本（粘贴的设定、描述等）
    DIALOGUE = "dialogue"    # 台词/语料
    IMAGE = "image"          # 立绘/图片
    SETTING = "setting"      # 角色设定文档
    GENERAL = "general"      # 通用素材（二创、评价等）


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Material:
    """一份上传的素材"""
    id: str
    type: str               # MaterialType 的值
    content: str            # 文本内容 or base64 图片数据
    label: str = ""         # 用户标注（如"官方设定"、"游戏台词"）
    filename: str = ""      # 原始文件名（如有）
    analyzed: bool = False  # 是否已分析


@dataclass
class MaterialAnalysis:
    """单份素材的分析结果"""
    material_id: str
    material_type: str
    analysis_text: str      # LLM 输出的完整分析文本


@dataclass
class SoulValidation:
    """soul.md 校验结果"""
    valid: bool
    missing_required: List[str]     # 缺少的必须章节
    missing_recommended: List[str]  # 缺少的推荐章节
    missing_scenarios: List[str]    # 缺少的场景反应
    warnings: List[str]             # 其他警告
    completeness: float             # 完整度 0-1


# ---------------------------------------------------------------------------
# 校验函数
# ---------------------------------------------------------------------------

def validate_soul(content: str) -> SoulValidation:
    """
    校验 soul.md 内容的完整性。

    检查逻辑：
    1. 必须章节是否存在
    2. 推荐章节是否存在
    3. 场景反应子项覆盖度
    4. 对话示例数量
    5. 角色原声参考数量
    """
    if not content or not content.strip():
        return SoulValidation(
            valid=False,
            missing_required=REQUIRED_SECTIONS[:],
            missing_recommended=RECOMMENDED_SECTIONS[:],
            missing_scenarios=SCENARIO_SECTIONS[:],
            warnings=["soul.md 内容为空"],
            completeness=0.0,
        )

    content_lower = content.lower()

    # 1. 检查必须章节
    missing_required = []
    for section in REQUIRED_SECTIONS:
        # 匹配 ## 身份 或 ## 身份认同 等变体
        pattern = rf"##\s*{re.escape(section)}"
        if not re.search(pattern, content):
            missing_required.append(section)

    # 2. 检查推荐章节
    missing_recommended = []
    for section in RECOMMENDED_SECTIONS:
        pattern = rf"##\s*{re.escape(section)}"
        if not re.search(pattern, content):
            missing_recommended.append(section)

    # 3. 检查场景反应子项
    missing_scenarios = []
    for scenario in SCENARIO_SECTIONS:
        if scenario not in content:
            missing_scenarios.append(scenario)

    # 4. 其他警告
    warnings = []

    # 检查对话示例数量（粗略统计 "用户:" 出现次数）
    dialogue_count = len(re.findall(r"用户[:：]", content))
    if dialogue_count < 3:
        warnings.append(f"对话示例太少（发现 {dialogue_count} 组），建议至少 5 组")
    elif dialogue_count < 5:
        warnings.append(f"对话示例略少（发现 {dialogue_count} 组），建议 6-8 组")

    # 检查原声参考数量（统计引用行）
    quote_lines = re.findall(r"^>\s*.+", content, re.MULTILINE)
    if "角色原声参考" in content and len(quote_lines) < 5:
        warnings.append(f"角色原声参考太少（发现 {len(quote_lines)} 句），建议 10-15 句")

    # 检查说话规则数量
    if "说话规则" in content:
        # 找说话规则 section 内的编号列表
        rules_match = re.search(
            r"##\s*说话规则.*?\n([\s\S]*?)(?=\n##|\Z)", content
        )
        if rules_match:
            rules_text = rules_match.group(1)
            rule_count = len(re.findall(r"^\d+\.", rules_text, re.MULTILINE))
            if rule_count < 5:
                warnings.append(f"说话规则太少（{rule_count} 条），建议 8-12 条")

    # 5. 计算完整度
    total_checks = len(REQUIRED_SECTIONS) + len(RECOMMENDED_SECTIONS) + len(SCENARIO_SECTIONS)
    passed = (
        (len(REQUIRED_SECTIONS) - len(missing_required))
        + (len(RECOMMENDED_SECTIONS) - len(missing_recommended))
        + (len(SCENARIO_SECTIONS) - len(missing_scenarios))
    )
    completeness = round(passed / total_checks, 2) if total_checks > 0 else 0.0

    return SoulValidation(
        valid=len(missing_required) == 0,
        missing_required=missing_required,
        missing_recommended=missing_recommended,
        missing_scenarios=missing_scenarios,
        warnings=warnings,
        completeness=completeness,
    )


def extract_sections(content: str) -> Dict[str, str]:
    """
    将 soul.md 拆分为章节字典。
    返回 {"身份": "...", "性格内核": "...", ...}
    """
    sections = {}
    # 匹配 ## 标题
    pattern = r"^##\s+(.+?)$"
    matches = list(re.finditer(pattern, content, re.MULTILINE))

    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[title] = content[start:end].strip()

    return sections
