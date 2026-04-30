"""
import_obsidian.py - Obsidian 历史日记冷启动导入
把 D:\\obsidian\\obsidan 下的所有日记导入 EpisodicMemory

用法：
  cd backend
  uv run python scripts/import_obsidian.py
  uv run python scripts/import_obsidian.py --no-vision   # 跳过图片分析
  uv run python scripts/import_obsidian.py --dry-run     # 只扫描不导入

支持断点续传：已导入的日记会跳过（根据 doc_id 去重）
图片处理：先压缩再发给 Vision LLM，生成文字描述后嵌入正文
"""
import os
import sys
import re
import time
import base64
import logging
from pathlib import Path
from datetime import datetime
from io import BytesIO
from typing import Optional

# 让脚本能找到 backend 的包
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from memory.episodic_memory import get_episodic_memory
from utils.image_compress import compress_for_llm
from llm_client import vision_chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("navi.import")

# 你的 Obsidian 目录
OBSIDIAN_DIR = Path(r"D:\obsidian\obsidan")

# 支持的图片扩展名
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 图片分析结果缓存（path → 描述），同一图片出现多次只分析一次
_image_cache: dict[str, str] = {}

# 预先构建 Obsidian 图片索引（文件名 → 完整路径），避免每次 rglob
_image_index: dict[str, Path] = {}

# 日记文件名匹配（支持多种格式）
DATE_PATTERNS = [
    r"^(\d{4}-\d{2}-\d{2})",       # 2026-04-19.md
    r"^(\d{4}_\d{2}_\d{2})",        # 2026_04_19.md
    r"^(\d{4}\d{2}\d{2})",          # 20260419.md
]


def extract_date_from_filename(name: str) -> str | None:
    """从文件名提取日期字符串"""
    for pattern in DATE_PATTERNS:
        m = re.match(pattern, name)
        if m:
            raw = m.group(1).replace("_", "-")
            if len(raw) == 8:  # 20260419 → 2026-04-19
                raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
            return raw
    return None


def build_image_index():
    """
    一次性扫描整个 Obsidian 目录，建立 文件名 → 完整路径 的索引
    避免每次 find_image_path 都 rglob 全量遍历
    """
    global _image_index
    if _image_index:
        return  # 已建立，跳过
    logger.info("正在建立图片索引...")
    for ext in IMAGE_EXTS:
        for f in OBSIDIAN_DIR.rglob(f"*{ext}"):
            # 同名文件保留第一个（通常 attachments/ 下的优先级最高）
            if f.name not in _image_index:
                _image_index[f.name] = f
    logger.info(f"图片索引建立完成，共 {len(_image_index)} 张图片")


def find_image_path(image_name: str, diary_path: Path) -> Optional[Path]:
    """
    查找图片文件：先检查日记同级目录，再查全局索引
    """
    # 1. 优先查日记文件附近（最常见的情况）
    candidates = [
        diary_path.parent / image_name,
        diary_path.parent / "attachments" / image_name,
        diary_path.parent / "assets" / image_name,
    ]
    for c in candidates:
        if c.exists():
            return c

    # 2. 查预建索引（O(1)，无需重新遍历）
    return _image_index.get(image_name)


def analyze_image_with_vision(image_path: Path) -> Optional[str]:
    """
    压缩图片后发给 Vision LLM，返回文字描述。
    结果会缓存，同一张图片跨多篇日记只分析一次。
    """
    cache_key = str(image_path.resolve())

    # 命中缓存直接返回
    if cache_key in _image_cache:
        logger.debug(f"图片缓存命中：{image_path.name}")
        return _image_cache[cache_key]

    b64_data = compress_for_llm(image_path)
    if b64_data is None:
        return None

    try:
        description = vision_chat(
            image_b64=b64_data,
            prompt=(
                "这是一张来自个人日记的图片，请用简洁的中文描述图片内容。"
                "重点描述：图片展示了什么、有什么文字信息、整体氛围。"
                "控制在100字以内。"
            ),
        )
    except Exception as e:
        logger.warning(f"Vision 调用异常：{image_path.name} - {e}")
        return None

    if description:
        _image_cache[cache_key] = description
        logger.debug(f"图片分析完成：{image_path.name} → {description[:50]}...")

    return description


def resolve_images_in_markdown(
    text: str,
    diary_path: Path,
    use_vision: bool = True,
) -> str:
    """
    处理 Markdown 中的图片引用：
    - 找到图片文件
    - 压缩后发给 Vision LLM
    - 用 [图片描述：xxx] 替换原来的图片语法
    """
    # 匹配 Obsidian wiki 风格 ![[image.png]] 和标准 ![alt](path)
    wiki_pattern = re.compile(r"!\[\[([^\]]+)\]\]")
    md_pattern = re.compile(r"!\[([^\]]*)\]\(([^\)]+)\)")

    vision_call_count = 0

    def replace_image(image_name: str, alt: str = "") -> str:
        nonlocal vision_call_count

        # 过滤掉非图片文件（如 pdf、mp4 等）
        suffix = Path(image_name).suffix.lower()
        if suffix not in IMAGE_EXTS:
            return f"[附件：{image_name}]"

        image_path = find_image_path(image_name, diary_path)
        if image_path is None:
            logger.debug(f"图片文件未找到：{image_name}")
            return f"[图片：{alt or image_name}（文件缺失）]"

        if not use_vision:
            return f"[图片：{alt or image_name}]"

        # 每篇日记最多分析 5 张图片，避免 token 爆炸
        if vision_call_count >= 5:
            logger.debug(f"已达单篇图片分析上限，跳过：{image_name}")
            return f"[图片：{alt or image_name}]"

        description = analyze_image_with_vision(image_path)
        vision_call_count += 1

        if description:
            return f"[图片描述：{description}]"
        else:
            return f"[图片：{alt or image_name}]"

    # 替换 ![[image.png]]
    def sub_wiki(m: re.Match) -> str:
        return replace_image(m.group(1))

    # 替换 ![alt](path)
    def sub_md(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        # 忽略网络图片（http 开头）
        if src.startswith("http"):
            return f"[网络图片：{alt or src}]"
        return replace_image(Path(src).name, alt)

    text = wiki_pattern.sub(sub_wiki, text)
    text = md_pattern.sub(sub_md, text)
    return text


def clean_markdown(text: str) -> str:
    """清理剩余的 Markdown 语法，保留纯文本"""
    # 去掉普通链接，保留文字
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # 去掉 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)
    # 去掉多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def find_diary_files() -> list[tuple[str, str]]:
    """找出所有日记文件，返回 [(date_str, file_path), ...]"""
    results = []
    for f in sorted(OBSIDIAN_DIR.rglob("*.md")):
        date_str = extract_date_from_filename(f.stem)
        if date_str:
            results.append((date_str, str(f)))
    return sorted(results)  # 按日期排序


def get_already_imported(episodic) -> set[str]:
    """获取已导入的日记日期集合（避免重复导入）"""
    try:
        results = episodic._col.get(
            where={"type": "diary"},
            include=["metadatas"],
        )
        dates = {m.get("date", "") for m in results["metadatas"]}
        return dates
    except Exception:
        return set()


# Vision 分析失败的特征字符串
_VISION_FAIL_MARKERS = [
    "[图片：",            # 占位符（vision 跳过或文件缺失）
    "我没有看到您实际上传的图",   # Anthropic SDK 格式错误导致的废描述
    "没有看到您上传",
    "未看到图片",
    "没有收到图片",
    "图片似乎没有",
]


def get_pending_vision_dates(episodic) -> set[str]:
    """找出 Vision 失败或描述异常的日记日期"""
    try:
        results = episodic._col.get(
            where={"type": "diary"},  # type: ignore[arg-type]
            include=["metadatas", "documents"],
        )
        pending = set()
        for meta, doc in zip(results["metadatas"] or [], results["documents"] or []):
            if doc and any(marker in doc for marker in _VISION_FAIL_MARKERS):
                pending.add(meta.get("date", ""))
        return pending
    except Exception:
        return set()


def retry_vision_for_dates(episodic, dates: set[str], diary_files: list[tuple[str, str]]):
    """对指定日期的日记重新做 Vision 分析，更新 ChromaDB 中的内容"""
    file_map = {d: f for d, f in diary_files}
    total = len(dates)
    logger.info(f"开始 Vision 重试，共 {total} 篇含图片占位符的日记")

    for i, date_str in enumerate(sorted(dates)):
        file_path = file_map.get(date_str)
        if not file_path:
            logger.warning(f"找不到 {date_str} 对应的文件，跳过")
            continue
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            content = resolve_images_in_markdown(
                content, diary_path=Path(file_path), use_vision=True
            )
            content = clean_markdown(content)

            # 删除旧条目，重新写入
            episodic.delete_by_date(date_str)
            episodic.add_diary(
                content=content,
                diary_date=date_str,
                source_file=file_path,
            )
            logger.info(f"[{i+1}/{total}] Vision 重试完成：{date_str}")
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"Vision 重试失败：{date_str} - {e}")


def import_all(dry_run: bool = False, limit: int = 0, use_vision: bool = True):
    """
    导入所有日记
    dry_run: True 时只扫描不导入
    limit: 限制导入条数（调试用），0 表示全部
    use_vision: True 时用 Vision LLM 分析图片
    """
    config = get_config()
    episodic = get_episodic_memory(config.memory_dir)

    diary_files = find_diary_files()
    already = get_already_imported(episodic)

    # 启动时前置检查
    if use_vision:
        minimax_key = os.getenv("MINIMAX_API_KEY", "")
        if not minimax_key or minimax_key.startswith("你的新"):
            logger.warning("⚠️  未配置 MINIMAX_API_KEY，图片将跳过 Vision 分析（用 --no-vision 可消除此提示）")
            use_vision = False
        else:
            build_image_index()  # 提前建立图片索引，只扫描一次

    logger.info(f"扫描到 {len(diary_files)} 篇日记，已导入 {len(already)} 篇")

    to_import = [(d, f) for d, f in diary_files if d not in already]
    if limit > 0:
        to_import = to_import[:limit]

    logger.info(f"本次需要导入：{len(to_import)} 篇")

    if dry_run:
        for d, f in to_import:
            print(f"  {d}  {f}")
        return

    success = 0
    failed = 0

    for i, (date_str, file_path) in enumerate(to_import):
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            # 先处理图片（压缩 → Vision LLM → 替换为文字描述）
            content = resolve_images_in_markdown(
                content, diary_path=Path(file_path), use_vision=use_vision
            )
            content = clean_markdown(content)

            if len(content.strip()) < 10:
                logger.debug(f"跳过空文件：{file_path}")
                continue

            episodic.add_diary(
                content=content,
                diary_date=date_str,
                source_file=file_path,
            )
            success += 1

            # 进度打印
            if (i + 1) % 10 == 0 or i == len(to_import) - 1:
                logger.info(f"进度：{i+1}/{len(to_import)}，成功 {success}，失败 {failed}")

            # 避免 API 速率限制（每 5 条暂停一下）
            if (i + 1) % 5 == 0:
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"导入失败：{date_str} {file_path}: {e}")
            failed += 1

    logger.info(f"\n✅ 导入完成！成功 {success} 篇，失败 {failed} 篇")
    logger.info(f"ChromaDB 总记录数：{episodic.count()}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="导入 Obsidian 日记到 Navi EpisodicMemory")
    parser.add_argument("--dry-run", action="store_true", help="只扫描不导入")
    parser.add_argument("--limit", type=int, default=0, help="限制导入条数（调试用）")
    parser.add_argument("--no-vision", action="store_true", help="跳过图片 Vision LLM 分析")
    parser.add_argument("--retry-vision", action="store_true", help="重新分析已导入但图片识别失败的日记")
    args = parser.parse_args()

    if args.retry_vision:
        config = get_config()
        episodic = get_episodic_memory(config.memory_dir)
        diary_files = find_diary_files()
        build_image_index()
        pending = get_pending_vision_dates(episodic)
        if not pending:
            logger.info("✅ 没有需要重试的日记，所有图片已分析完成")
        else:
            retry_vision_for_dates(episodic, pending, diary_files)
    else:
        import_all(dry_run=args.dry_run, limit=args.limit, use_vision=not args.no_vision)
