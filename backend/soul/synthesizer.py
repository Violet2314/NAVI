"""
Soul Synthesizer — 灵魂合成 + 微调模块

将多份分析结果合并，调用 LLM 生成完整的 soul.md。
支持基于用户反馈的局部/全局微调。
"""
import logging
from typing import Optional

from soul.schema import MaterialAnalysis, MaterialType
from soul.prompts import (
    SYNTHESIZE_SOUL,
    REFINE_SECTION,
    REFINE_GENERAL,
)

logger = logging.getLogger("navi.soul.synthesizer")


def _combine_analyses(analyses: list[MaterialAnalysis]) -> str:
    """
    将多份分析结果合并为一个结构化文本。

    按类型分组，让 LLM 更容易理解各维度的信息来源。
    """
    # 按类型分组
    groups = {
        "设定分析": [],
        "台词/语料分析": [],
        "图片分析": [],
        "其他素材分析": [],
    }

    for a in analyses:
        if a.material_type == MaterialType.SETTING:
            groups["设定分析"].append(a.analysis_text)
        elif a.material_type == MaterialType.DIALOGUE:
            groups["台词/语料分析"].append(a.analysis_text)
        elif a.material_type == MaterialType.IMAGE:
            groups["图片分析"].append(a.analysis_text)
        else:
            groups["其他素材分析"].append(a.analysis_text)

    # 拼接
    parts = []
    for group_name, texts in groups.items():
        if texts:
            parts.append(f"### {group_name}")
            for i, text in enumerate(texts, 1):
                if len(texts) > 1:
                    parts.append(f"#### 素材 {i}")
                parts.append(text)
                parts.append("")  # 空行分隔

    return "\n".join(parts)


def synthesize_soul(
    analyses: list[MaterialAnalysis],
    relationship: str = "朋友",
    user_nickname: str = "你",
) -> str:
    """
    从分析结果合成完整的 soul.md。

    Args:
        analyses: 所有素材的分析结果
        relationship: 用户与角色的关系描述
        user_nickname: 用户希望被称呼的方式

    Returns:
        生成的 soul.md 完整文本
    """
    from llm_client import chat

    combined = _combine_analyses(analyses)
    logger.info(f"🧬 开始合成灵魂，分析结果共 {len(combined)} 字，关系={relationship}，称呼={user_nickname}")

    prompt = SYNTHESIZE_SOUL.format(
        combined_analysis=combined,
        relationship=relationship,
        user_nickname=user_nickname,
    )

    try:
        from llm_constants import SOUL_SYNTHESIZER_MAX_TOKENS, SOUL_SYNTHESIZER_TEMPERATURE
        result = chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个顶级的 AI 角色设计师。请严格按照要求的格式输出，不要遗漏任何章节。用中文输出。",
            temperature=SOUL_SYNTHESIZER_TEMPERATURE,
            max_tokens=SOUL_SYNTHESIZER_MAX_TOKENS,
        )

        # 清理可能的 markdown 代码块包裹
        result = _clean_markdown_wrapper(result)

        logger.info(f"✅ 灵魂合成完成，soul.md 长度={len(result)} 字")
        return result

    except Exception as e:
        logger.error(f"❌ 灵魂合成失败: {e}")
        raise


def refine_soul(
    current_soul: str,
    feedback: str,
    section: Optional[str] = None,
) -> str:
    """
    根据用户反馈微调 soul.md。

    Args:
        current_soul: 当前的 soul.md 内容
        feedback: 用户的修改意见
        section: 指定修改的章节名（可选，None 表示全局修改）

    Returns:
        修改后的完整 soul.md
    """
    from llm_client import chat

    logger.info(f"🔧 微调灵魂，section={section or '全局'}，反馈='{feedback[:50]}...'")

    if section:
        prompt = REFINE_SECTION.format(
            current_soul=current_soul,
            feedback=feedback,
            section=section,
        )
    else:
        prompt = REFINE_GENERAL.format(
            current_soul=current_soul,
            feedback=feedback,
        )

    try:
        from llm_constants import SOUL_REFINE_MAX_TOKENS, SOUL_REFINE_TEMPERATURE
        result = chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个 AI 角色设计师。请根据反馈修改 soul.md，输出修改后的完整文本。用中文输出。",
            temperature=SOUL_REFINE_TEMPERATURE,
            max_tokens=SOUL_REFINE_MAX_TOKENS,
        )

        result = _clean_markdown_wrapper(result)

        logger.info(f"✅ 微调完成，新 soul.md 长度={len(result)} 字")
        return result

    except Exception as e:
        logger.error(f"❌ 微调失败: {e}")
        raise


def _clean_markdown_wrapper(text: str) -> str:
    """
    清理 LLM 可能在 soul.md 外面套的 ```markdown ``` 代码块。
    """
    text = text.strip()

    # 移除开头的 ```markdown 或 ```
    if text.startswith("```markdown"):
        text = text[len("```markdown"):].strip()
    elif text.startswith("```md"):
        text = text[len("```md"):].strip()
    elif text.startswith("```"):
        text = text[3:].strip()

    # 移除结尾的 ```
    if text.endswith("```"):
        text = text[:-3].strip()

    return text
