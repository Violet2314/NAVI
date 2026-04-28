"""TTS (Text-to-Speech) Provider 插件系统。

参考 airi 的多 Provider 架构，支持本地 + 云端切换：
  - edge_tts:    免费，固定音色，零资源
  - fish_audio:  云端声音克隆，角色音色
  - openai:      OpenAI TTS API
  - cosyvoice:   阿里云 CosyVoice2
  - volcengine:  火山引擎（豆包同款）
  - gptsovits:   本地 GPT-SoVITS（需 GPU）
"""
