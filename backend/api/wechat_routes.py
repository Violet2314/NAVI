"""
微信频道 REST API

- GET  /api/wechat/status      获取微信在线状态
- GET  /api/wechat/qr-login    SSE 流：获取二维码并轮询扫码状态
- POST /api/wechat/logout      登出并清除凭证
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger("navi.wechat.api")

router = APIRouter(prefix="/api/wechat", tags=["wechat"])


def _get_wechat_channel():
    """获取 WeChatChannel 单例（可能为 None）"""
    from channels.wechat import WeChatChannel
    return WeChatChannel.get_instance()


@router.get("/status")
def wechat_status():
    """获取微信频道状态"""
    ch = _get_wechat_channel()
    if ch:
        return ch.get_status()
    # 没有实例，检查是否有已保存凭证
    from channels.wechat import _load_credentials
    creds = _load_credentials()
    return {
        "online": False,
        "account_id": "",
        "base_url": "",
        "last_user_id": "",
        "has_credentials": creds is not None,
    }


@router.get("/qr-login")
async def wechat_qr_login():
    """
    SSE 流：触发 QR 登录流程。

    前端用 EventSource 连接，接收事件：
      data: {"status": "pending", "qrcode_url": "https://..."}
      data: {"status": "scanned"}
      data: {"status": "confirmed"}
      data: {"status": "error", "error": "..."}
    """
    from channels.wechat import qr_login, WeChatChannel

    async def event_stream():
        async for state in qr_login():
            # confirmed：先启动 channel，等 online 后再通知前端
            if state.status == "confirmed" and state.credentials:
                ch = _get_wechat_channel()
                if ch:
                    try:
                        # 踢掉旧连接，重新用新凭证启动
                        await ch.start_with_credentials(state.credentials)
                        # 等待 channel 真正 online（最多 5 秒）
                        for _ in range(10):
                            if ch.is_online:
                                break
                            await asyncio.sleep(0.5)
                        # 此时 fetchStatus 会返回 online:true
                        yield f"data: {json.dumps({'status': 'confirmed', 'qrcode_url': '', 'error': ''})}\n\n"
                    except Exception as e:
                        yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'error', 'error': 'WeChatChannel 未初始化'})}\n\n"
                return

            # 其他状态正常转发
            payload = {
                "status": state.status,
                "qrcode_url": state.qrcode_url or "",
                "error": state.error or "",
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            if state.status in ("error", "expired"):
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/logout")
async def wechat_logout():
    """登出微信并清除凭证"""
    ch = _get_wechat_channel()
    if ch and ch.is_online:
        await ch.logout()
        return {"ok": True, "message": "已登出"}
    # 即使没有在线实例，也清除凭证
    from channels.wechat import _delete_credentials
    _delete_credentials()
    return {"ok": True, "message": "凭证已清除"}
