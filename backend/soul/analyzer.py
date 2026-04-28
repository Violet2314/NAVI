"""
Soul Analyzer — 素材分析模块

对每份素材调用 LLM 提取角色维度信息。
支持文本分析（chat）和图片分析（vision_chat）。
"""
import logging
from typing import Optional

from soul.schema import Material, MaterialAnalysis, MaterialType
from soul.prompts import (
    ANALYZE_SETTING,
    ANALYZE_DIALOGUE,
    ANALYZE_IMAGE,
    ANALYZE_GENERAL,
)

logger = logging.getLogger("navi.soul.analyzer")

# 文本分析 token 上限（避免超长素材爆 context）
MAX_MATERIAL_CHARS = 15000


def _truncate(text: str, max_chars: int = MAX_MATERIAL_CHARS) -> str:
    """截断过长文本，保留首尾"""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [已截断，原文共 {len(text)} 字，保留首尾各 {half} 字] ...\n\n"
        + text[-half:]
    )


def analyze_material(material: Material) -> MaterialAnalysis:
    """
    分析单份素材，返回结构化分析结果。

    根据素材类型选择不同的分析 prompt：
    - setting  → ANALYZE_SETTING（提取设定、外貌、背景）
    - dialogue → ANALYZE_DIALOGUE（提取说话风格、句式、语气词）
    - image    → ANALYZE_IMAGE（Vision 模型分析立绘）
    - text/general → ANALYZE_GENERAL（通用分析）
    """
    from llm_client import chat, vision_chat

    logger.info(f"📝 分析素材 [{material.id}] 类型={material.type} 标签='{material.label}'")

    if material.type == MaterialType.IMAGE:
        # 图片走 Vision 模型
        analysis = _analyze_image(material, vision_chat)
    else:
        # 文本类素材走 chat 模型
        analysis = _analyze_text(material, chat)

    logger.info(f"✅ 素材 [{material.id}] 分析完成，结果长度={len(analysis)} 字")

    return MaterialAnalysis(
        material_id=material.id,
        material_type=material.type,
        analysis_text=analysis,
    )


def _analyze_text(material: Material, chat_fn) -> str:
    """分析文本类素材"""
    content = _truncate(material.content)

    # 根据类型选择 prompt
    if material.type == MaterialType.SETTING:
        prompt = ANALYZE_SETTING.format(material=content)
    elif material.type == MaterialType.DIALOGUE:
        prompt = ANALYZE_DIALOGUE.format(material=content)
    else:
        prompt = ANALYZE_GENERAL.format(material=content)

    try:
        from llm_constants import SOUL_ANALYZER_TEXT_MAX_TOKENS, SOUL_ANALYZER_TEXT_TEMPERATURE
        result = chat_fn(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个专业的角色分析师，擅长从各种素材中提取角色特征。请用中文回答。",
            temperature=SOUL_ANALYZER_TEXT_TEMPERATURE,
            max_tokens=SOUL_ANALYZER_TEXT_MAX_TOKENS,
        )
        return result
    except Exception as e:
        logger.error(f"❌ 素材 [{material.id}] 分析失败: {e}")
        return f"[分析失败: {e}]"


def _analyze_image(material: Material, vision_fn) -> str:
    """分析图片素材"""
    try:
        from llm_constants import SOUL_ANALYZER_IMAGE_MAX_TOKENS
        result = vision_fn(
            image_b64=material.content,
            prompt=ANALYZE_IMAGE,
            max_tokens=SOUL_ANALYZER_IMAGE_MAX_TOKENS,
        )
        return result or "[图片分析未返回结果，可能 Vision 模型未配置]"
    except Exception as e:
        logger.error(f"❌ 图片素材 [{material.id}] 分析失败: {e}")
        return f"[图片分析失败: {e}]"


def analyze_all(materials: list[Material]) -> list[MaterialAnalysis]:
    """
    分析所有素材。

    当前是串行执行（简单可靠）。
    如果素材很多（>10份），未来可以改为并发。
    """
    results = []
    for i, mat in enumerate(materials):
        logger.info(f"📊 分析进度: {i + 1}/{len(materials)}")
        analysis = analyze_material(mat)
        results.append(analysis)
    return results
