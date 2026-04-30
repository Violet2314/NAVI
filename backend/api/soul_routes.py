"""
Soul API 路由 — 灵魂工坊 API

提供灵魂生成的完整 API：
- 素材管理（上传/列表/删除）
- 分析 & 合成
- 微调
- 导入 & 导出
- 校验
"""
import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger("navi.api.soul")

soul_router = APIRouter(prefix="/api/soul", tags=["soul"])


def _get_gen():
    """延迟导入 SoulGenerator 单例"""
    from soul.generator import get_generator
    return get_generator()


# ============================================================
# 素材管理
# ============================================================

@soul_router.post("/materials")
async def add_material(
    content: str = Form(default=""),
    material_type: str = Form(default="general"),
    label: str = Form(default=""),
    file: Optional[UploadFile] = File(default=None),
):
    """
    添加素材。

    支持两种方式：
    1. 文本素材：通过 content 字段传入
    2. 文件素材：通过 file 字段上传（支持 .txt/.md 文本文件和图片文件）
    """
    gen = _get_gen()

    # 处理文件上传
    if file and file.filename:
        file_bytes = await file.read()
        filename = file.filename

        # 判断是否为图片
        is_image = any(
            filename.lower().endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]
        )

        if is_image:
            # 图片转 base64
            content = base64.b64encode(file_bytes).decode("utf-8")
            material_type = "image"
        else:
            # 文本文件
            try:
                content = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = file_bytes.decode("gbk", errors="replace")

        if not label:
            label = filename

    if not content:
        return JSONResponse(status_code=400, content={"error": "content 和 file 不能同时为空"})

    mat = gen.add_material(
        content=content,
        material_type=material_type,
        label=label,
        filename=file.filename if file else "",
    )

    return {
        "ok": True,
        "material": {
            "id": mat.id,
            "type": mat.type,
            "label": mat.label,
        },
    }


@soul_router.get("/materials")
def list_materials():
    """获取所有已上传的素材列表"""
    gen = _get_gen()
    return {"materials": gen.get_materials()}


@soul_router.delete("/materials/{material_id}")
def remove_material(material_id: str):
    """删除一份素材"""
    gen = _get_gen()
    ok = gen.remove_material(material_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": f"素材 {material_id} 不存在"})
    return {"ok": True}


@soul_router.delete("/materials")
def clear_all_materials():
    """清空所有素材和分析结果"""
    gen = _get_gen()
    gen.clear_all()
    return {"ok": True}


# ============================================================
# 分析 & 合成
# ============================================================

@soul_router.post("/analyze")
def analyze_materials():
    """
    分析所有未分析的素材。

    注意：这是一个同步阻塞操作，每份素材需要调用一次 LLM。
    素材较多时可能耗时较长（每份约 10-30 秒）。
    """
    gen = _get_gen()
    materials = gen.get_materials()
    if not materials:
        return JSONResponse(status_code=400, content={"error": "请先上传素材"})

    try:
        analyses = gen.analyze()
        return {
            "ok": True,
            "total_analyzed": len(analyses),
            "analyses": analyses,
        }
    except Exception as e:
        logger.error(f"分析失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@soul_router.post("/generate")
def generate_soul(body: Dict[str, Any]):
    """
    合成 soul.md。

    body: {
        "relationship": "朋友",   // 用户与角色的关系，不传默认"朋友"
        "user_nickname": "你"     // 用户希望被称呼的方式，不传默认"你"
    }
    """
    gen = _get_gen()
    relationship = body.get("relationship", "朋友")
    user_nickname = body.get("user_nickname", "你")

    try:
        result = gen.generate(
            relationship=relationship,
            user_nickname=user_nickname,
        )
        return {"ok": True, **result}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error(f"合成失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ============================================================
# 微调
# ============================================================

@soul_router.post("/refine")
def refine_soul_api(body: Dict[str, Any]):
    """
    根据反馈微调 soul.md。

    body: {
        "feedback": "她说话应该更毒舌一点",
        "section": "说话规则"               // 可选，指定修改的章节
    }
    """
    gen = _get_gen()
    feedback = body.get("feedback", "")
    section = body.get("section")

    if not feedback:
        return JSONResponse(status_code=400, content={"error": "feedback 不能为空"})

    try:
        result = gen.refine(feedback=feedback, section=section)
        return {"ok": True, **result}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error(f"微调失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ============================================================
# 导入 & 导出
# ============================================================

@soul_router.post("/import")
def import_soul(body: Dict[str, Any]):
    """
    导入外部生成的 soul.md。

    body: {
        "content": "# 凯尔希 · Navi 的灵魂\n\n## 身份\n..."
    }
    """
    gen = _get_gen()
    content = body.get("content", "")

    if not content.strip():
        return JSONResponse(status_code=400, content={"error": "content 不能为空"})

    result = gen.import_soul(content)
    return {"ok": True, **result}


@soul_router.post("/validate")
def validate_soul_api(body: Dict[str, Any]):
    """
    校验 soul.md 完整性（不保存）。

    body: {
        "content": "# 凯尔希 · Navi 的灵魂\n..."
    }
    """
    from soul.schema import validate_soul

    content = body.get("content", "")
    if not content.strip():
        return JSONResponse(status_code=400, content={"error": "content 不能为空"})

    validation = validate_soul(content)
    return {
        "valid": validation.valid,
        "completeness": validation.completeness,
        "missing_required": validation.missing_required,
        "missing_recommended": validation.missing_recommended,
        "missing_scenarios": validation.missing_scenarios,
        "warnings": validation.warnings,
    }


@soul_router.get("/current")
def get_current_soul():
    """获取当前 soul.md 内容"""
    gen = _get_gen()
    return gen.get_current_soul()


@soul_router.post("/save")
def save_soul():
    """将当前 soul.md 保存到 templates/SOUL.md（正式生效）"""
    gen = _get_gen()
    try:
        path = gen.save()
        return {"ok": True, "path": path}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error(f"保存失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@soul_router.get("/guide")
def get_guide():
    """获取灵魂制作指南文档内容"""
    guide_path = Path(__file__).parent.parent.parent / "docs" / "soul-creation-guide.md"
    if not guide_path.exists():
        return JSONResponse(status_code=404, content={"error": "指南文档不存在"})

    content = guide_path.read_text(encoding="utf-8")
    return {"content": content}


# ============================================================
# 灵魂仓库（Vault）
# ============================================================

@soul_router.get("/vault")
def vault_list():
    """列出仓库中所有灵魂"""
    from soul.vault import list_souls
    return list_souls()


@soul_router.post("/vault")
def vault_add(body: Dict[str, Any]):
    """添加灵魂到仓库。body: { content: str, name?: str }"""
    from soul.vault import add_soul
    content = body.get("content", "")
    if not content.strip():
        return JSONResponse(status_code=400, content={"error": "content 不能为空"})
    result = add_soul(content, name=body.get("name", ""))
    return {"ok": True, **result}


@soul_router.get("/vault/{soul_id}")
def vault_get(soul_id: str):
    """获取一个灵魂的完整内容"""
    from soul.vault import get_soul
    result = get_soul(soul_id)
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


@soul_router.put("/vault/{soul_id}")
def vault_update(soul_id: str, body: Dict[str, Any]):
    """编辑灵魂。body: { content: str, name?: str }"""
    from soul.vault import update_soul
    content = body.get("content", "")
    if not content.strip():
        return JSONResponse(status_code=400, content={"error": "content 不能为空"})
    result = update_soul(soul_id, content, name=body.get("name", ""))
    if "error" in result:
        return JSONResponse(status_code=404, content=result)
    return {"ok": True, **result}


@soul_router.post("/vault/{soul_id}/activate")
def vault_activate(soul_id: str):
    """激活灵魂（使其生效）"""
    from soul.vault import activate_soul
    result = activate_soul(soul_id)
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return {"ok": True, **result}


@soul_router.delete("/vault/{soul_id}")
def vault_delete(soul_id: str):
    """删除灵魂"""
    from soul.vault import delete_soul
    result = delete_soul(soul_id)
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result
