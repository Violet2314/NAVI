# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — 将 Navi Python 后端打包为单个 exe（供 Tauri Sidecar 使用）

打包命令:
    cd backend
    pyinstaller navi-backend.spec --clean

输出:
    backend/dist/navi-backend.exe
"""

import sys
import os
from pathlib import Path

# spec 文件中 __file__ 不可用，用当前工作目录
_SPEC_DIR = os.path.abspath(os.path.dirname(sys.argv[0])) if hasattr(sys, 'argv') and sys.argv else os.getcwd()

_block_cipher = None

# templates 目录里有用户私人内容（USER.md / SOUL.md 等），打包时必须排除
# 只保留模板/示例/默认文件，避免泄露本地画像和人设
_TEMPLATES_BLACKLIST = {
    'USER.md',
    'USER.md.bak',
    'SOUL.md',
}


def _collect_templates(src_dir: str = 'templates'):
    """递归收集 templates/ 下的文件，跳过黑名单。返回 PyInstaller datas 三元组列表。"""
    collected = []
    src_root = Path(_SPEC_DIR) / src_dir
    if not src_root.exists():
        return collected
    for p in src_root.rglob('*'):
        if not p.is_file():
            continue
        if p.name in _TEMPLATES_BLACKLIST:
            continue
        rel_dir = p.parent.relative_to(src_root)
        dst_dir = src_dir if str(rel_dir) == '.' else f"{src_dir}/{rel_dir.as_posix()}"
        collected.append((str(p), dst_dir))
    return collected


a = Analysis(
    ['main.py'],
    pathex=[_SPEC_DIR],
    binaries=[],
    datas=[
        # 技能定义
        ('skills', 'skills'),
        # docs/ 是开发设计文档（.gitignore 中标记为不公开），不进发布包
    ] + _collect_templates('templates'),
    hiddenimports=[
        # FastAPI / uvicorn
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.wsproto_impl',
        'fastapi',
        'fastapi.middleware',
        'fastapi.staticfiles',
        'starlette',
        # 数据库
        'sqlalchemy',
        'sqlalchemy.ext.asyncio',
        # chromadb 向量数据库
        'chromadb',
        'chromadb.config',
        'chromadb.db',
        'chromadb.api',
        'chromadb.utils.embedding_functions',
        # 图像处理
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'cv2',
        'numpy',
        # ONNX 推理（情绪识别）
        'onnxruntime',
        # 日志
        'loguru',
        # 配置
        'yaml',
        'dotenv',
        # HTTP 客户端
        'httpx',
        # 加密
        'cryptography',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.primitives',
        # TTS
        'edge_tts',
        # 定时任务
        'apscheduler',
        'apscheduler.schedulers.asyncio',
        'apscheduler.triggers.interval',
        'apscheduler.triggers.cron',
        # Token 计数
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        # 网页可读性
        'readability_lxml',
        # JSON 修复
        'json_repair',
        # 图像哈希
        'imagehash',
        # 屏幕截图
        'mss',
        # 系统信息
        'psutil',
        # Windows API
        'pywin32',
        'win32api',
        'win32con',
        'win32gui',
        'win32process',
        'win32clipboard',
        'pythoncom',
        # Windows 通知
        'win10toast',
        # 二维码
        'qrcode',
        'qrcode.image.pil',
        # 文件上传
        'multipart',
        'python_multipart',
        # 搜索引擎
        'ddgs',
        # Anthropic
        'anthropic',
        # OpenAI
        'openai',
        # websockets
        'websockets',
        'websockets.legacy',
        # asyncio
        'asyncio',
        # pydantic
        'pydantic',
        'pydantic.deprecated',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'pandas',
        'scipy',
        'jupyter',
        'IPython',
        'notebook',
        'pip',
        'setuptools',
        'wheel',
        'pytest',
        'unittest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='navi-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=True 保留控制台窗口（调试用），发布时可改为 False
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 添加图标（如果存在）
    icon=str(Path(_SPEC_DIR).parent / 'icon.png') if (Path(_SPEC_DIR).parent / 'icon.png').exists() else None,
)
