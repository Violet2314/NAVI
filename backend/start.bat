@echo off
chcp 65001 >nul
cd /d %~dp0
echo.
echo  ███╗   ██╗ █████╗ ██╗   ██╗██╗
echo  ████╗  ██║██╔══██╗██║   ██║██║
echo  ██╔██╗ ██║███████║██║   ██║██║
echo  ██║╚██╗██║██╔══██║╚██╗ ██╔╝██║
echo  ██║ ╚████║██║  ██║ ╚████╔╝ ██║
echo  ╚═╝  ╚═══╝╚═╝  ╚═╝  ╚═══╝  ╚═╝
echo.
echo  感知层 + AgentLoop + 对话界面 一键启动
echo  对话入口: http://localhost:1420  (打开 Tauri 客户端)
echo  API 文档: http://localhost:8000/docs
echo  WebSocket: ws://localhost:8000/ws/chat
echo.
uv run python main.py
pause


:: 如需导入 Obsidian 历史日记（首次使用时跑一次即可），请手动执行：
:: uv run python scripts/import_obsidian.py            （含图片 Vision 分析）
:: uv run python scripts/import_obsidian.py --no-vision （跳过图片，速度更快）