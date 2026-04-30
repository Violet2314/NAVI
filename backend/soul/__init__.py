"""
Soul — Navi 灵魂生成系统

提供两条路径生成角色灵魂 (soul.md)：
- Path A: 自动化管道（上传素材 → 分析 → 合成 → 微调）
- Path B: 手动导入（配合 docs/soul-creation-guide.md 外部生成后导入）

核心模块：
- generator.py  — 管道编排器 + 存储管理
- analyzer.py   — 素材分析（文本/图片 → 角色维度提取）
- synthesizer.py — 灵魂合成 + 微调
- schema.py     — soul.md 格式定义 + 校验
- prompts.py    — prompt 模板库
"""
from soul.generator import SoulGenerator, get_generator
from soul.schema import validate_soul, MaterialType

__all__ = ["SoulGenerator", "get_generator", "validate_soul", "MaterialType"]
