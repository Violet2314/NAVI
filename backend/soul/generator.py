"""
Soul Generator — 管道编排器 + 存储管理

编排整个灵魂生成流程：素材收集 → 分析 → 合成 → 微调 → 保存
素材和中间状态存储在 ~/.navi/soul-workshop/ 目录下。
"""
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from soul.schema import (
    Material,
    MaterialAnalysis,
    MaterialType,
    SoulValidation,
    validate_soul,
)
from soul.analyzer import analyze_material, analyze_all
from soul.synthesizer import synthesize_soul, refine_soul

logger = logging.getLogger("navi.soul.generator")

# ---------------------------------------------------------------------------
# 存储路径
# ---------------------------------------------------------------------------

NAVI_HOME = Path.home() / ".navi"
WORKSHOP_DIR = NAVI_HOME / "soul-workshop"
SOUL_OUTPUT_PATH = Path(__file__).parent.parent / "templates" / "SOUL.md"


def _ensure_workshop():
    """确保工坊目录存在"""
    (WORKSHOP_DIR / "materials").mkdir(parents=True, exist_ok=True)
    (WORKSHOP_DIR / "analyses").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# SoulGenerator — 核心编排器
# ---------------------------------------------------------------------------

class SoulGenerator:
    """
    灵魂生成管道编排器。

    生命周期：
    1. add_material() × N  — 添加素材
    2. analyze()           — 分析所有素材
    3. generate()          — 合成 soul.md
    4. refine() × N        — 用户反馈微调（可选）
    5. save()              — 保存到 templates/SOUL.md
    """

    def __init__(self):
        _ensure_workshop()
        self._materials: list[Material] = []
        self._analyses: list[MaterialAnalysis] = []
        self._current_soul: str = ""
        self._load_state()

    # ── 素材管理 ─────────────────────────────────────────────────────────

    def add_material(
        self,
        content: str,
        material_type: str = MaterialType.GENERAL,
        label: str = "",
        filename: str = "",
    ) -> Material:
        """
        添加一份素材。

        Args:
            content: 文本内容或 base64 图片数据
            material_type: 素材类型 (text/dialogue/image/setting/general)
            label: 用户标注
            filename: 原始文件名

        Returns:
            创建的 Material 对象
        """
        mat_id = f"mat_{uuid.uuid4().hex[:8]}"
        mat = Material(
            id=mat_id,
            type=material_type,
            content=content,
            label=label,
            filename=filename,
            analyzed=False,
        )
        self._materials.append(mat)
        self._save_material(mat)
        logger.info(f"📎 添加素材 [{mat_id}] 类型={material_type} 标签='{label}'")
        return mat

    def get_materials(self) -> list[dict]:
        """获取所有素材列表（不含完整 content，避免传输大量数据）"""
        return [
            {
                "id": m.id,
                "type": m.type,
                "label": m.label,
                "filename": m.filename,
                "analyzed": m.analyzed,
                "preview": (
                    m.content[:100] + "..."
                    if m.type != MaterialType.IMAGE and len(m.content) > 100
                    else ("[图片数据]" if m.type == MaterialType.IMAGE else m.content)
                ),
            }
            for m in self._materials
        ]

    def remove_material(self, material_id: str) -> bool:
        """删除一份素材"""
        for i, m in enumerate(self._materials):
            if m.id == material_id:
                self._materials.pop(i)
                # 删除对应的分析结果
                self._analyses = [
                    a for a in self._analyses if a.material_id != material_id
                ]
                self._delete_material_file(material_id)
                logger.info(f"🗑️ 删除素材 [{material_id}]")
                return True
        return False

    def clear_all(self):
        """清空所有素材和分析结果"""
        self._materials.clear()
        self._analyses.clear()
        self._current_soul = ""
        if WORKSHOP_DIR.exists():
            shutil.rmtree(WORKSHOP_DIR)
        _ensure_workshop()
        logger.info("🧹 已清空灵魂工坊")

    # ── 分析 ─────────────────────────────────────────────────────────────

    def analyze(self) -> list[dict]:
        """
        分析所有未分析的素材。

        Returns:
            所有分析结果的摘要列表
        """
        unanalyzed = [m for m in self._materials if not m.analyzed]
        if not unanalyzed:
            logger.info("ℹ️ 没有新素材需要分析")
            return self._analyses_summary()

        logger.info(f"🔬 开始分析 {len(unanalyzed)} 份素材...")
        new_analyses = analyze_all(unanalyzed)

        # 更新状态
        for mat in unanalyzed:
            mat.analyzed = True
        self._analyses.extend(new_analyses)

        # 持久化
        self._save_analyses()
        self._save_state()

        return self._analyses_summary()

    def _analyses_summary(self) -> list[dict]:
        """返回分析结果摘要"""
        return [
            {
                "material_id": a.material_id,
                "material_type": a.material_type,
                "analysis_preview": a.analysis_text[:200] + "..."
                if len(a.analysis_text) > 200
                else a.analysis_text,
                "analysis_length": len(a.analysis_text),
            }
            for a in self._analyses
        ]

    # ── 合成 ─────────────────────────────────────────────────────────────

    def generate(
        self,
        relationship: str = "朋友",
        user_nickname: str = "你",
    ) -> dict:
        """
        合成 soul.md。

        Args:
            relationship: 用户与角色的关系
            user_nickname: 用户称呼

        Returns:
            {"soul_md": str, "validation": dict}
        """
        if not self._analyses:
            raise ValueError("请先分析素材（调用 analyze），再生成灵魂")

        self._current_soul = synthesize_soul(
            analyses=self._analyses,
            relationship=relationship,
            user_nickname=user_nickname,
        )

        # 校验
        validation = validate_soul(self._current_soul)

        # 持久化草稿
        self._save_draft()

        return {
            "soul_md": self._current_soul,
            "validation": {
                "valid": validation.valid,
                "completeness": validation.completeness,
                "missing_required": validation.missing_required,
                "missing_recommended": validation.missing_recommended,
                "missing_scenarios": validation.missing_scenarios,
                "warnings": validation.warnings,
            },
        }

    # ── 微调 ─────────────────────────────────────────────────────────────

    def refine(
        self,
        feedback: str,
        section: Optional[str] = None,
    ) -> dict:
        """
        根据反馈微调 soul.md。

        Args:
            feedback: 用户反馈
            section: 指定修改的章节（可选）

        Returns:
            {"soul_md": str, "validation": dict}
        """
        if not self._current_soul:
            raise ValueError("请先生成灵魂（调用 generate），再微调")

        self._current_soul = refine_soul(
            current_soul=self._current_soul,
            feedback=feedback,
            section=section,
        )

        validation = validate_soul(self._current_soul)
        self._save_draft()

        return {
            "soul_md": self._current_soul,
            "validation": {
                "valid": validation.valid,
                "completeness": validation.completeness,
                "missing_required": validation.missing_required,
                "missing_recommended": validation.missing_recommended,
                "missing_scenarios": validation.missing_scenarios,
                "warnings": validation.warnings,
            },
        }

    # ── 导入 & 保存 ─────────────────────────────────────────────────────

    def import_soul(self, content: str) -> dict:
        """
        导入外部生成的 soul.md。

        Returns:
            {"validation": dict, "saved": bool}
        """
        validation = validate_soul(content)
        self._current_soul = content
        self._save_draft()

        return {
            "validation": {
                "valid": validation.valid,
                "completeness": validation.completeness,
                "missing_required": validation.missing_required,
                "missing_recommended": validation.missing_recommended,
                "missing_scenarios": validation.missing_scenarios,
                "warnings": validation.warnings,
            },
        }

    def save(self) -> str:
        """
        将当前 soul.md 保存到 templates/SOUL.md（正式生效）。

        Returns:
            保存路径
        """
        if not self._current_soul:
            raise ValueError("没有可保存的灵魂内容")

        SOUL_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

        # 备份旧版本
        if SOUL_OUTPUT_PATH.exists():
            backup_name = f"SOUL.md.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_path = SOUL_OUTPUT_PATH.parent / backup_name
            shutil.copy2(SOUL_OUTPUT_PATH, backup_path)
            logger.info(f"📦 已备份旧灵魂 → {backup_path}")

        SOUL_OUTPUT_PATH.write_text(self._current_soul, encoding="utf-8")
        logger.info(f"✅ 灵魂已保存 → {SOUL_OUTPUT_PATH}")
        return str(SOUL_OUTPUT_PATH)

    def get_current_soul(self) -> dict:
        """获取当前灵魂内容"""
        # 优先读草稿，其次读正式文件
        content = self._current_soul
        source = "draft"

        if not content and SOUL_OUTPUT_PATH.exists():
            content = SOUL_OUTPUT_PATH.read_text(encoding="utf-8")
            source = "saved"

        if not content:
            return {"content": "", "source": "none", "has_soul": False}

        validation = validate_soul(content)
        return {
            "content": content,
            "source": source,
            "has_soul": True,
            "validation": {
                "valid": validation.valid,
                "completeness": validation.completeness,
                "warnings": validation.warnings,
            },
        }

    # ── 持久化（文件存储）───────────────────────────────────────────────

    def _save_material(self, mat: Material):
        """保存素材到文件"""
        path = WORKSHOP_DIR / "materials" / f"{mat.id}.json"
        data = {
            "id": mat.id,
            "type": mat.type,
            "label": mat.label,
            "filename": mat.filename,
            "analyzed": mat.analyzed,
            # 图片内容太大，单独存储
            "content": mat.content if mat.type != MaterialType.IMAGE else "",
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 图片单独存
        if mat.type == MaterialType.IMAGE:
            img_path = WORKSHOP_DIR / "materials" / f"{mat.id}.b64"
            img_path.write_text(mat.content, encoding="utf-8")

    def _delete_material_file(self, material_id: str):
        """删除素材文件"""
        for ext in [".json", ".b64"]:
            path = WORKSHOP_DIR / "materials" / f"{material_id}{ext}"
            if path.exists():
                path.unlink()

    def _save_analyses(self):
        """保存分析结果"""
        data = [
            {
                "material_id": a.material_id,
                "material_type": a.material_type,
                "analysis_text": a.analysis_text,
            }
            for a in self._analyses
        ]
        path = WORKSHOP_DIR / "analyses" / "all_analyses.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_draft(self):
        """保存 soul.md 草稿"""
        path = WORKSHOP_DIR / "SOUL_DRAFT.md"
        path.write_text(self._current_soul, encoding="utf-8")

    def _save_state(self):
        """保存管道状态"""
        state = {
            "material_ids": [m.id for m in self._materials],
            "analyzed_count": sum(1 for m in self._materials if m.analyzed),
            "has_soul_draft": bool(self._current_soul),
            "updated_at": datetime.now().isoformat(),
        }
        path = WORKSHOP_DIR / "state.json"
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self):
        """从文件恢复状态（重启后恢复）"""
        try:
            # 恢复素材
            mat_dir = WORKSHOP_DIR / "materials"
            if mat_dir.exists():
                for json_file in sorted(mat_dir.glob("*.json")):
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    content = data.get("content", "")
                    # 恢复图片内容
                    if data.get("type") == MaterialType.IMAGE and not content:
                        b64_path = mat_dir / f"{data['id']}.b64"
                        if b64_path.exists():
                            content = b64_path.read_text(encoding="utf-8")
                    mat = Material(
                        id=data["id"],
                        type=data.get("type", MaterialType.GENERAL),
                        content=content,
                        label=data.get("label", ""),
                        filename=data.get("filename", ""),
                        analyzed=data.get("analyzed", False),
                    )
                    self._materials.append(mat)

            # 恢复分析结果
            analyses_path = WORKSHOP_DIR / "analyses" / "all_analyses.json"
            if analyses_path.exists():
                data = json.loads(analyses_path.read_text(encoding="utf-8"))
                self._analyses = [
                    MaterialAnalysis(
                        material_id=a["material_id"],
                        material_type=a["material_type"],
                        analysis_text=a["analysis_text"],
                    )
                    for a in data
                ]

            # 恢复草稿
            draft_path = WORKSHOP_DIR / "SOUL_DRAFT.md"
            if draft_path.exists():
                self._current_soul = draft_path.read_text(encoding="utf-8")

            if self._materials:
                logger.info(
                    f"📂 恢复灵魂工坊状态: {len(self._materials)} 份素材, "
                    f"{len(self._analyses)} 份分析, "
                    f"{'有' if self._current_soul else '无'}草稿"
                )

        except Exception as e:
            logger.warning(f"⚠️ 恢复工坊状态失败（不影响使用）: {e}")


# ---------------------------------------------------------------------------
# 模块级单例（延迟初始化）
# ---------------------------------------------------------------------------

_generator: Optional[SoulGenerator] = None


def get_generator() -> SoulGenerator:
    """获取全局 SoulGenerator 单例"""
    global _generator
    if _generator is None:
        _generator = SoulGenerator()
    return _generator
