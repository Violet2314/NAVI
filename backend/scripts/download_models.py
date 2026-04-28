"""
NAVI 模型下载脚本

下载人脸检测 + 情绪识别 ONNX 模型到 data/models/ 目录

用法：
  uv run python scripts/download_models.py

如果 GitHub 下载失败，请手动下载后放到 C:\\Users\\你的用户名\\.navi\\data\\models\\
  - ultraface_slim.onnx  (人脸检测, ~1.2MB)
  - emotion_mobilenet.onnx (情绪识别, ~13MB)
"""
import sys
import os
from pathlib import Path

# 确保 backend 在 path 里
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config


# 下载源（按优先级排列）
MODELS = {
    "ultraface_slim.onnx": {
        "desc": "UltraFace 人脸检测模型 (~1.2MB)",
        "urls": [
            # HuggingFace 镜像（国内更快）
            "https://hf-mirror.com/onnx-community/ultraface/resolve/main/version-RFB-320.onnx",
            # HuggingFace 官方
            "https://huggingface.co/onnx-community/ultraface/resolve/main/version-RFB-320.onnx",
            # GitHub ONNX Model Zoo
            "https://github.com/onnx/models/raw/main/validated/vision/body_analysis/ultraface/models/version-RFB-320.onnx",
        ],
    },
    "emotion_mobilenet.onnx": {
        "desc": "FER 情绪识别模型 (~13MB)",
        "urls": [
            # HuggingFace 镜像
            "https://hf-mirror.com/onnx-community/facial-expression-recognition/resolve/main/emotion-ferplus-8.onnx",
            # HuggingFace 官方
            "https://huggingface.co/onnx-community/facial-expression-recognition/resolve/main/emotion-ferplus-8.onnx",
            # GitHub ONNX Model Zoo
            "https://github.com/onnx/models/raw/main/validated/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx",
        ],
    },
}


def download_file(url: str, dest: Path, timeout: int = 30) -> bool:
    """下载文件，返回是否成功"""
    import urllib.request
    import ssl

    try:
        # 跳过 SSL 验证（某些镜像站证书可能有问题）
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        print(f"  尝试: {url[:80]}...")

        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            chunk_size = 1024 * 64

            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(f"\r  下载中: {downloaded/1024/1024:.1f}MB / {total/1024/1024:.1f}MB ({pct:.0f}%)", end="", flush=True)

            print(f"\n  ✅ 下载成功: {dest.name} ({downloaded/1024/1024:.1f}MB)")
            return True

    except Exception as e:
        print(f"\n  ❌ 失败: {e}")
        if dest.exists():
            dest.unlink()
        return False


def main():
    config = get_config()
    model_dir = Path(config.data_dir) / "models"
    model_dir.mkdir(parents=True, exist_ok=True)

    print(f"模型目录: {model_dir}\n")

    all_ok = True
    for filename, info in MODELS.items():
        dest = model_dir / filename
        if dest.exists():
            print(f"✅ {filename} 已存在 ({dest.stat().st_size / 1024:.0f}KB)，跳过")
            continue

        print(f"📥 下载 {info['desc']}...")
        ok = False
        for url in info["urls"]:
            if download_file(url, dest):
                ok = True
                break

        if not ok:
            print(f"\n❌ {filename} 所有下载源均失败！")
            print(f"   请手动下载并放到: {dest}")
            print(f"   下载地址: {info['urls'][-1]}")
            all_ok = False

        print()

    if all_ok:
        print("🎉 所有模型下载完成！现在可以启动摄像头了。")
    else:
        print("⚠️  部分模型下载失败，请手动下载。")


if __name__ == "__main__":
    main()
