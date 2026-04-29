"""
WeChat iLink Bot 频道

基于腾讯官方 iLink Bot API (https://ilinkai.weixin.qq.com) 实现。
参考 ai-live2d-go/electron/bridges/adapters/wechat.ts

核心特性：
  - QR 码扫描登录（首次运行通过前端 UI 完成）
  - 长轮询接收消息（35 秒超时）
  - Context Token 管理（每个用户会话上下文）
  - 消息分片发送（速率限制保护）
  - 凭证持久化（重启免登录）
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
import uuid
from collections.abc import AsyncGenerator
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import httpx

logger = logging.getLogger("navi.wechat")

# ── iLink API Constants ─────────────────────────────────────────────

ILINK_VER = "2.2.0"
ILINK_CV = (2 << 16) | (2 << 8) | 0  # 131584
CHANNEL_VERSION = "2.2.0"
ILINK_APP_ID = "bot"

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_SEND_TYPING = "ilink/bot/sendtyping"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"

LONG_POLL_TIMEOUT_SEC = 35
API_TIMEOUT_SEC = 15
QR_TIMEOUT_SEC = 35

# 消息类型
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

# 媒体类型
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

MSG_TYPE_USER = 1
MSG_STATE_FINISH = 2

MAX_MESSAGE_LENGTH = 4000
MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY_SEC = 2

# 微信固定会话 ID（类似 __proactive__）
WECHAT_DB_SESSION_ID = "__wechat__"
WECHAT_SESSION_TITLE = "微信对话"


# ── Data Types ───────────────────────────────────────────────────────

@dataclass
class AccountCredentials:
    account_id: str
    token: str
    base_url: str
    user_id: str = ""
    saved_at: str = ""


@dataclass
class QRLoginState:
    qrcode: str = ""
    qrcode_url: str = ""
    status: str = "pending"  # pending | scanned | confirmed | expired | error
    credentials: Optional[AccountCredentials] = None
    error: Optional[str] = None


# ── Helper Functions ─────────────────────────────────────────────────

def _random_wechat_uin() -> str:
    import random
    value = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(value).encode()).decode()


def _base_info() -> dict:
    return {"channel_version": CHANNEL_VERSION}


def _build_headers(token: Optional[str], body_bytes: bytes) -> dict[str, str]:
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body_bytes)),
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_CV),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _ilink_post(
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: str,
    payload: dict,
    token: Optional[str],
    timeout_sec: float = API_TIMEOUT_SEC,
) -> dict:
    """发送 iLink API POST 请求"""
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    body_str = json.dumps(payload)
    body_bytes = body_str.encode("utf-8")

    try:
        resp = await client.post(
            url,
            content=body_bytes,
            headers=_build_headers(token, body_bytes),
            timeout=timeout_sec,
        )
    except httpx.TimeoutException:
        # 长轮询超时是正常的
        if endpoint == EP_GET_UPDATES:
            return {"ret": 0, "msgs": [], "get_updates_buf": payload.get("get_updates_buf", "")}
        raise

    if resp.status_code != 200:
        logger.error(f"[WeChat API] HTTP {resp.status_code} {endpoint}: {resp.text[:300]}")
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        return resp.json()
    except Exception:
        logger.error(f"[WeChat API] JSON 解析失败 {endpoint}: {resp.text[:300]}")
        raise


def _split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """长消息分片"""
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break
        cut_at = remaining.rfind("\n", 0, max_length)
        if cut_at < max_length // 2:
            cut_at = max_length
        chunks.append(remaining[:cut_at])
        remaining = remaining[cut_at:].lstrip()
    return chunks


def _extract_text(msg: dict) -> str:
    """从 iLink 消息中提取文本"""
    parts: list[str] = []
    for item in msg.get("item_list", []):
        if item.get("type") == ITEM_TEXT and item.get("text_item", {}).get("text"):
            parts.append(item["text_item"]["text"])
        elif item.get("voice_item", {}).get("text"):
            parts.append(f"[用户发送了语音消息：「{item['voice_item']['text']}」]")
    return "\n".join(parts).strip()


def _aes_128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密 + PKCS7 padding"""
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


def _get_mime_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".pdf": "application/pdf", ".txt": "text/plain",
        ".doc": "application/msword", ".zip": "application/zip",
    }
    return mime_map.get(ext, "application/octet-stream")


def _get_media_item_type(mime_type: str) -> tuple[int, int]:
    """返回 (media_type, item_type)"""
    if mime_type.startswith("image/"):
        return MEDIA_IMAGE, ITEM_IMAGE
    if mime_type.startswith("video/"):
        return MEDIA_VIDEO, ITEM_VIDEO
    if mime_type.startswith("audio/"):
        return MEDIA_VOICE, ITEM_VOICE
    return MEDIA_FILE, ITEM_FILE


# ── Persistence ──────────────────────────────────────────────────────

def _get_wechat_data_dir() -> Path:
    from config import get_config
    cfg = get_config()
    d = Path(cfg.data_dir) / "wechat"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_credentials(creds: AccountCredentials) -> None:
    path = _get_wechat_data_dir() / "credentials.json"
    path.write_text(json.dumps({
        "account_id": creds.account_id,
        "token": creds.token,
        "base_url": creds.base_url,
        "user_id": creds.user_id,
        "saved_at": creds.saved_at,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[WeChat] 凭证已保存: {path}")


def _load_credentials() -> Optional[AccountCredentials]:
    path = _get_wechat_data_dir() / "credentials.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AccountCredentials(**data)
    except Exception as e:
        logger.warning(f"[WeChat] 凭证加载失败: {e}")
        return None


def _delete_credentials() -> None:
    path = _get_wechat_data_dir() / "credentials.json"
    if path.exists():
        path.unlink()


def _is_valid_wechat_user_id(user_id: Optional[str]) -> bool:
    """
    判断一个 user_id 是否是合法的 iLink 微信用户 ID。
    真实格式形如 `o9cq807xLWWyKNHAmFfDBgbBGpuY@im.wechat`。
    """
    if not user_id or not isinstance(user_id, str):
        return False
    return user_id.endswith("@im.wechat")


def _load_sync_buf() -> str:
    path = _get_wechat_data_dir() / "sync_buf.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("get_updates_buf", "")
    except Exception:
        return ""


def _save_sync_buf(buf: str) -> None:
    path = _get_wechat_data_dir() / "sync_buf.json"
    path.write_text(json.dumps({"get_updates_buf": buf}, indent=2), encoding="utf-8")


def _load_context_tokens() -> dict[str, str]:
    path = _get_wechat_data_dir() / "context_tokens.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_context_tokens(tokens: dict[str, str]) -> None:
    path = _get_wechat_data_dir() / "context_tokens.json"
    path.write_text(json.dumps(tokens, indent=2, ensure_ascii=False), encoding="utf-8")


# ── QR Login Flow ────────────────────────────────────────────────────

def _generate_qr_png_b64(content: str) -> str:
    """用 qrcode 库把字符串内容生成 PNG 二维码，返回 base64 DataURL。"""
    try:
        import io
        import qrcode  # type: ignore
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except ImportError:
        logger.warning("[WeChat QR] qrcode 库未安装，运行: pip install 'qrcode[pil]'")
        return ""
    except Exception as e:
        logger.warning("[WeChat QR] 生成二维码失败: %s", e)
        return ""


async def _fetch_image_as_data_url(client: httpx.AsyncClient, url: str) -> str:
    """
    将外部 URL 转为 base64 DataURL 供前端 <img> 使用。
    - 若 URL 返回图片 → 直接 base64
    - 若 URL 返回 HTML（微信扫码落地页）→ 用 URL 本身生成二维码图片
    """
    if not url:
        return ""
    try:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()

        if content_type.startswith("image/"):
            # 真正的图片，直接 base64
            b64 = base64.b64encode(resp.content).decode()
            return f"data:{content_type};base64,{b64}"
        else:
            # HTML 或其他非图片内容（微信落地页）→ 用 URL 本身生成二维码
            logger.info("[WeChat QR] 响应为 %s，改用 URL 本身生成二维码: %s", content_type, url)
            return _generate_qr_png_b64(url)
    except Exception as e:
        logger.warning("[WeChat QR] 下载失败 %s: %s，改用 URL 生成二维码", url, e)
        return _generate_qr_png_b64(url)


async def qr_login(base_url: str = "https://ilinkai.weixin.qq.com") -> AsyncGenerator[QRLoginState, None]:
    """
    QR 码登录流程（AsyncGenerator，被 API 路由调用，通过 SSE 推送状态）

    步骤：
      1. GET 二维码
      2. 轮询扫码状态
      3. 扫码确认后保存凭证
    """
    async with httpx.AsyncClient() as client:
        current_base_url = base_url
        qrcode = ""
        qrcode_url = ""

        try:
            # Step 1: 获取二维码
            qr_resp = await _ilink_post(
                client, current_base_url,
                f"{EP_GET_BOT_QR}?bot_type=3",
                {}, None, QR_TIMEOUT_SEC,
            )
            if qr_resp.get("ret", -1) != 0 or not qr_resp.get("qrcode"):
                yield QRLoginState(status="error", error=qr_resp.get("errmsg", "获取二维码失败"))
                return

            qrcode = qr_resp["qrcode"]
            # ── 调试：打印完整响应，找出正确的图片字段 ──
            logger.info("[WeChat QR] 完整响应字段: %s", list(qr_resp.keys()))
            logger.info("[WeChat QR] 完整响应: %s", json.dumps(
                {k: (v[:80] + "...") if isinstance(v, str) and len(v) > 80 else v
                 for k, v in qr_resp.items()}, ensure_ascii=False))

            # 优先取 base64 图片内容字段，降级到图片 URL
            qrcode_img_b64: str = qr_resp.get("qrcode_img_content_base64", "") \
                or qr_resp.get("img_content", "") \
                or qr_resp.get("qrcode_content", "")
            qrcode_img_url: str = qr_resp.get("qrcode_img_url", "") \
                or qr_resp.get("qrcode_url", "") \
                or qr_resp.get("qrcode_img_content", "")

            if qrcode_img_b64:
                # 已经是 base64 图片
                if qrcode_img_b64.startswith("data:"):
                    qrcode_url = qrcode_img_b64
                else:
                    qrcode_url = f"data:image/png;base64,{qrcode_img_b64}"
                logger.info("[WeChat QR] 使用 base64 字段，长度=%d", len(qrcode_img_b64))
            elif qrcode_img_url:
                # 尝试下载图片并转 base64
                logger.info("[WeChat QR] 尝试下载图片 URL: %s", qrcode_img_url)
                qrcode_url = await _fetch_image_as_data_url(client, qrcode_img_url)
                logger.info("[WeChat QR] 图片下载结果: %s...", qrcode_url[:60])
            else:
                qrcode_url = ""
                logger.warning("[WeChat QR] 没有找到图片字段，所有字段: %s", qr_resp)

            yield QRLoginState(qrcode=qrcode, qrcode_url=qrcode_url, status="pending")

            # Step 2: 轮询状态（最多 8 分钟）
            deadline = time.time() + 480
            refresh_count = 0

            while time.time() < deadline:
                await asyncio.sleep(1.5)

                try:
                    status_resp = await _ilink_post(
                        client, current_base_url,
                        f"{EP_GET_QR_STATUS}?qrcode={qrcode}",
                        {}, None, QR_TIMEOUT_SEC,
                    )

                    status = status_resp.get("status", "wait")

                    if status == "scaned":
                        yield QRLoginState(qrcode=qrcode, qrcode_url=qrcode_url, status="scanned")

                    elif status == "scaned_but_redirect":
                        if status_resp.get("redirect_host"):
                            current_base_url = f"https://{status_resp['redirect_host']}"

                    elif status == "expired":
                        refresh_count += 1
                        if refresh_count > 3:
                            yield QRLoginState(status="expired", error="二维码多次过期")
                            return
                        # 刷新二维码
                        new_qr = await _ilink_post(
                            client, base_url,
                            f"{EP_GET_BOT_QR}?bot_type=3",
                            {}, None, QR_TIMEOUT_SEC,
                        )
                        if new_qr.get("qrcode"):
                            qrcode = new_qr["qrcode"]
                            qrcode_url = new_qr.get("qrcode_img_content", qrcode)
                            yield QRLoginState(qrcode=qrcode, qrcode_url=qrcode_url, status="pending")

                    elif status == "confirmed":
                        account_id = status_resp.get("ilink_bot_id", "")
                        token = status_resp.get("bot_token", "")
                        final_base_url = status_resp.get("baseurl", current_base_url)
                        user_id = status_resp.get("ilink_user_id", "")

                        if not account_id or not token:
                            yield QRLoginState(status="error", error="凭证不完整")
                            return

                        creds = AccountCredentials(
                            account_id=account_id,
                            token=token,
                            base_url=final_base_url,
                            user_id=user_id,
                            saved_at=datetime.now().isoformat(),
                        )
                        _save_credentials(creds)
                        yield QRLoginState(
                            qrcode=qrcode, qrcode_url=qrcode_url,
                            status="confirmed", credentials=creds,
                        )
                        return

                except Exception as e:
                    logger.warning(f"[WeChat] QR 轮询错误: {e}")
                    # 继续重试

            yield QRLoginState(status="expired", error="登录超时")

        except Exception as e:
            yield QRLoginState(status="error", error=str(e))


# ── WeChat Channel ───────────────────────────────────────────────────

class WeChatChannel:
    """
    微信 iLink Bot 频道。

    和 LocalWSChannel 平级，但通信协议不同：
    - 收消息：长轮询 iLink API
    - 发消息：POST iLink API
    - 共享 AgentLoop 做 AI 处理

    单例模式，方便 Dispatcher / API 获取实例。
    """

    _instance: Optional["WeChatChannel"] = None

    @classmethod
    def get_instance(cls) -> Optional["WeChatChannel"]:
        return cls._instance

    def __init__(self, agent_loop, send_chunk_delay: float = 0.35):
        self.agent_loop = agent_loop
        self.send_chunk_delay = send_chunk_delay

        self._token: str = ""
        self._account_id: str = ""
        self._base_url: str = "https://ilinkai.weixin.qq.com"
        self._context_tokens: dict[str, str] = {}
        self._sync_buf: str = ""
        self._running: bool = False
        self._poll_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._last_user_id: str = ""  # 最近发消息的用户，主动对话推送用

    @property
    def is_online(self) -> bool:
        return self._running and bool(self._token)

    @property
    def last_user_id(self) -> str:
        return self._last_user_id

    async def start(self) -> None:
        """启动 WeChat 频道（加载凭证 → 恢复状态 → 开始长轮询）"""
        # 无论是否有凭证，先注册单例，确保 QR 登录后能拿到实例
        WeChatChannel._instance = self

        creds = _load_credentials()
        if not creds:
            logger.info("[WeChat] 未找到凭证，等待 QR 登录")
            return

        self._token = creds.token
        self._account_id = creds.account_id
        self._base_url = creds.base_url
        self._context_tokens = _load_context_tokens()
        self._sync_buf = _load_sync_buf()

        # 从 DB 恢复最近活跃的微信用户（用于主动对话推送）
        self._restore_last_user_id()

        logger.info(
            f"[WeChat] 启动中... accountId={self._account_id[:8]}*** "
            f"baseUrl={self._base_url}"
        )

        self._client = httpx.AsyncClient()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("[WeChat] ✅ 长轮询已启动，等待消息...")

    async def start_with_credentials(self, creds: AccountCredentials) -> None:
        """使用新凭证启动（QR 登录成功后调用）"""
        _save_credentials(creds)
        self._token = creds.token
        self._account_id = creds.account_id
        self._base_url = creds.base_url
        self._context_tokens = _load_context_tokens()
        self._sync_buf = _load_sync_buf()

        # 从凭证里直接确定主动推送目标（= 主人）
        self._restore_last_user_id()

        if self._client is None:
            self._client = httpx.AsyncClient()

        self._running = True
        WeChatChannel._instance = self
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("[WeChat] ✅ QR 登录成功，长轮询已启动")

    async def stop(self) -> None:
        """停止 WeChat 频道"""
        self._running = False
        WeChatChannel._instance = None
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        _save_context_tokens(self._context_tokens)
        _save_sync_buf(self._sync_buf)
        logger.info("[WeChat] Bot 已下线")

    async def logout(self) -> None:
        """登出并清除凭证"""
        await self.stop()
        _delete_credentials()
        self._token = ""
        self._account_id = ""
        self._last_user_id = ""
        logger.info("[WeChat] 已登出，凭证已清除")

    # ── 发送消息 ──────────────────────────────────────────────────

    async def send_text(self, user_id: str, text: str) -> None:
        """发送文本消息到微信用户（自动分片）。

        iLink 即使业务失败也会返回 HTTP 200，只在 body 里带 ret != 0，
        所以这里必须显式校验 ret，否则失败会被静默吞掉，导致上游
        （例如 proactive dispatcher）打出"已推送"的误导日志。
        """
        if not self._client or not self._token:
            raise RuntimeError("WeChat Bot 未连接")

        if not _is_valid_wechat_user_id(user_id):
            raise RuntimeError(f"非法 WeChat user_id: {user_id!r}")

        chunks = _split_message(text)
        for i, chunk in enumerate(chunks):
            context_token = self._context_tokens.get(user_id, "")
            payload = {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": user_id,
                    "client_id": f"navi-{int(time.time()*1000)}-{secrets.token_hex(4)}",
                    "message_type": 2,  # bot → user
                    "message_state": MSG_STATE_FINISH,
                    "context_token": context_token,
                    "item_list": [
                        {"type": ITEM_TEXT, "text_item": {"text": chunk}},
                    ],
                },
                "base_info": _base_info(),
            }
            resp = await _ilink_post(
                self._client, self._base_url, EP_SEND_MESSAGE, payload, self._token,
            )
            ret = resp.get("ret", 0)
            if ret != 0:
                errmsg = resp.get("errmsg") or resp.get("err_msg") or str(resp)[:200]
                raise RuntimeError(
                    f"[WeChat] sendmessage 失败 to={user_id[:12]}*** "
                    f"ret={ret} errmsg={errmsg}"
                )
            if len(chunks) > 1 and i < len(chunks) - 1 and self.send_chunk_delay > 0:
                await asyncio.sleep(self.send_chunk_delay)

    async def send_media(self, user_id: str, file_path: str, caption: str = "") -> None:
        """
        上传并发送媒体文件（图片/视频/文件）到微信用户。

        iLink 上传流程（参考 ai-live2d-go/electron/bridges/adapters/wechat.ts sendFile）：
          1. 客户端生成随机 AES-128 key 和 filekey
          2. AES-128-ECB 加密文件
          3. 调用 getuploadurl（带 to_user_id / filekey / rawsize / rawfilemd5 / filesize / aeskey）
          4. POST 加密数据到 CDN（upload_param 或 upload_full_url）
          5. 从 CDN 响应头 x-encrypted-param 获取 encrypt_query_param
          6. sendmessage 里携带 encrypt_query_param + aes_key(base64) + encrypt_type=1
        """
        if not self._client or not self._token:
            raise RuntimeError("WeChat Bot 未连接")

        mime = _get_mime_type(file_path)
        media_type, item_type = _get_media_item_type(mime)
        file_data = Path(file_path).read_bytes()
        raw_size = len(file_data)
        raw_md5 = hashlib.md5(file_data).hexdigest()
        file_name = Path(file_path).name

        # Step 1: 客户端生成随机密钥
        aes_key = secrets.token_bytes(16)          # 16字节 AES key
        file_key = secrets.token_hex(16)           # 32字符 hex filekey

        # Step 2: AES-128-ECB 加密
        encrypted = _aes_128_ecb_encrypt(file_data, aes_key)
        enc_size = len(encrypted)
        logger.info(f"[WeChat] 文件加密: {file_name} raw={raw_size}B enc={enc_size}B")

        # Step 3: 获取上传 URL
        up_resp = await _ilink_post(
            self._client, self._base_url, EP_GET_UPLOAD_URL,
            {
                "to_user_id": user_id,
                "media_type": media_type,
                "filekey": file_key,
                "rawsize": raw_size,
                "rawfilemd5": raw_md5,
                "filesize": enc_size,
                "aeskey": aes_key.hex(),
                "base_info": _base_info(),
            },
            self._token,
        )
        upload_param: str = up_resp.get("upload_param", "") or up_resp.get("upload_full_url", "")
        if not upload_param:
            raise RuntimeError(f"[WeChat] getuploadurl 失败: {up_resp}")

        # Step 4: POST 加密数据到 CDN
        if upload_param.startswith("http"):
            cdn_url = upload_param
        else:
            enc_param = urllib.parse.quote(upload_param)
            enc_fkey = urllib.parse.quote(file_key)
            cdn_url = (
                f"https://novac2c.cdn.weixin.qq.com/c2c/upload"
                f"?encrypted_query_param={enc_param}&filekey={enc_fkey}"
            )
        logger.info(f"[WeChat] 上传到 CDN: {cdn_url[:80]}...")
        cdn_resp = await self._client.post(
            cdn_url,
            content=encrypted,
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
        cdn_resp.raise_for_status()

        # Step 5: 从响应头拿 encrypt_query_param
        encrypt_query_param = cdn_resp.headers.get("x-encrypted-param", "")
        if not encrypt_query_param:
            raise RuntimeError(
                f"[WeChat] CDN 响应缺少 x-encrypted-param 头: {cdn_resp.text[:200]}"
            )
        # aes_key 转 base64（hex 字符串的 ASCII bytes → base64）
        aes_key_b64 = base64.b64encode(aes_key.hex().encode()).decode()

        # Step 6: 构造 item_list 发送消息
        media_block = {
            "encrypt_query_param": encrypt_query_param,
            "aes_key": aes_key_b64,
            "encrypt_type": 1,
        }
        if item_type == ITEM_IMAGE:
            item: dict = {"type": ITEM_IMAGE, "image_item": {"media": media_block, "mid_size": enc_size}}
        elif item_type == ITEM_VIDEO:
            item = {"type": ITEM_VIDEO, "video_item": {"media": media_block, "video_size": enc_size}}
        elif item_type == ITEM_VOICE:
            item = {"type": ITEM_VOICE, "voice_item": {"media": media_block, "voice_size": enc_size}}
        else:
            item = {"type": ITEM_FILE, "file_item": {
                "media": media_block, "file_size": enc_size, "file_name": file_name,
            }}

        item_list = [item]
        if caption:
            item_list.append({"type": ITEM_TEXT, "text_item": {"text": caption}})

        context_token = self._context_tokens.get(user_id, "")
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": f"navi-{int(time.time()*1000)}-{secrets.token_hex(4)}",
                "message_type": 2,
                "message_state": MSG_STATE_FINISH,
                "context_token": context_token,
                "item_list": item_list,
            },
            "base_info": _base_info(),
        }
        await _ilink_post(self._client, self._base_url, EP_SEND_MESSAGE, payload, self._token)
        logger.info(f"[WeChat] 媒体已发送到 {user_id[:8]}***: {file_name}")

    async def send_typing(self, user_id: str, is_typing: bool) -> None:
        """发送正在输入状态"""
        if not self._client or not self._token:
            return
        try:
            await _ilink_post(
                self._client, self._base_url, EP_SEND_TYPING,
                {
                    "to_user_id": user_id,
                    "typing": 1 if is_typing else 2,
                    "base_info": _base_info(),
                },
                self._token, 5,
            )
        except Exception:
            pass  # typing 失败不影响

    def _restore_last_user_id(self) -> None:
        """
        从登录凭证恢复 Bot 主人的微信 ID（主动对话 / cron 推送目标）。

        Bot 主人 = QR 扫码登录时 iLink 返回的 `ilink_user_id`，
        存在 credentials.json 里，重装/重启都稳定可用。

        我们不再追踪"最近发消息的人"——那在多人场景下会把主动对话
        发给错误的陌生人（谁刚发过消息就会覆盖 _last_user_id）。
        """
        creds = _load_credentials()
        if creds and _is_valid_wechat_user_id(creds.user_id):
            self._last_user_id = creds.user_id
            logger.info(
                "[WeChat] 主动推送目标 = 主人: %s***", creds.user_id[:8],
            )
        else:
            logger.warning(
                "[WeChat] 凭证里的 user_id 非法（%r），主动推送将被跳过",
                creds.user_id if creds else None,
            )

    async def send_to_last_user(self, text: str) -> None:
        """发送消息给最近活跃的微信用户（主动对话推送用），自动按 [SPLIT] 拆分多段"""
        if not self._last_user_id:
            self._restore_last_user_id()  # 兜底：每次推送前尝试从 DB 恢复
        if not _is_valid_wechat_user_id(self._last_user_id):
            logger.info(
                "[WeChat] 没有合法的最近用户（当前=%r），跳过主动推送",
                self._last_user_id,
            )
            # 清空内存中的脏值，避免反复尝试
            self._last_user_id = ""
            return
        segments = [s.strip() for s in text.split("[SPLIT]") if s.strip()] or [text]
        for i, seg in enumerate(segments):
            await self.send_text(self._last_user_id, seg)
            if i < len(segments) - 1:
                await asyncio.sleep(self.send_chunk_delay)

    async def send(self, msg: "OutboundMessage") -> None:
        """
        实现 BaseChannel.send()，供 ChannelManager._dispatch_outbound 路由调用。
        用于 cron job / proactive 等主动发送场景。
        """
        from bus.events import OutboundMessage as _OM  # 避免循环导入
        if msg.content:
            await self.send_to_last_user(msg.content)

    # ── 长轮询主循环 ─────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        consecutive_failures = 0

        while self._running:
            try:
                resp = await _ilink_post(
                    self._client, self._base_url, EP_GET_UPDATES,
                    {
                        "get_updates_buf": self._sync_buf,
                        "base_info": _base_info(),
                    },
                    self._token,
                    LONG_POLL_TIMEOUT_SEC + 5,
                )

                ret = resp.get("ret", 0)  # iLink getupdates 有消息时通常不带 ret 字段，缺省视为成功
                if ret == 0 or "msgs" in resp:
                    consecutive_failures = 0
                    self._sync_buf = resp.get("get_updates_buf", self._sync_buf)
                    _save_sync_buf(self._sync_buf)

                    msgs = resp.get("msgs", [])
                    if msgs:
                        logger.info(f"[WeChat] 收到 {len(msgs)} 条消息")
                        for msg in msgs:
                            try:
                                await self._handle_message(msg)
                            except Exception as e:
                                logger.error(f"[WeChat] 消息处理失败: {e}")
                else:
                    logger.warning(f"[WeChat] getUpdates 返回错误 ret={ret}: {json.dumps(resp)[:300]}")
                    consecutive_failures += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[WeChat] 轮询错误: {e}")
                consecutive_failures += 1

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error("[WeChat] 连续失败次数过多，暂停 30 秒")
                await asyncio.sleep(30)
                consecutive_failures = 0
            elif consecutive_failures > 0:
                await asyncio.sleep(RETRY_DELAY_SEC)

    # ── 消息处理 ─────────────────────────────────────────────────

    async def _handle_message(self, msg: dict) -> None:
        """处理收到的微信消息 → AgentLoop 处理 → 回复"""
        if msg.get("message_type") != MSG_TYPE_USER:
            return
        if msg.get("message_state") != MSG_STATE_FINISH:
            return

        from_user_id = msg.get("from_user_id", "")
        context_token = msg.get("context_token", "")
        text = _extract_text(msg)

        if not text:
            return

        # 更新 context token
        if context_token:
            self._context_tokens[from_user_id] = context_token
            _save_context_tokens(self._context_tokens)

        # 注意：这里 **不** 更新 self._last_user_id。
        # 主动对话/cron 的推送目标固定是 Bot 主人（来自 credentials.user_id），
        # 不能被"刚刚发过消息的任何人"覆盖，否则陌生人一发消息就会把
        # Navi 的主动问候发到他那里去。
        logger.info(f"[WeChat] 收到消息 from={from_user_id[:8]}***: {text[:50]}")

        # 发送 typing 状态
        await self.send_typing(from_user_id, True)

        try:
            # 持久化用户消息到 DB
            from api.chat_routes import save_message, ensure_session
            ensure_session(WECHAT_DB_SESSION_ID, title=WECHAT_SESSION_TITLE)
            save_message(WECHAT_DB_SESSION_ID, "user", text)

            # 注入记忆上下文
            memory_ctx = ""
            try:
                from memory.episodic_memory import get_episodic_memory, should_retrieve
                if should_retrieve(text):
                    em = get_episodic_memory()
                    ctx = em.to_context_text(text, n=3)
                    if ctx:
                        memory_ctx = (
                            "【相关历史记忆，仅供参考，"
                            "请勿将其视为用户当前的发言内容】\n"
                            + ctx + "\n\n"
                        )
            except Exception as e:
                logger.debug(f"[WeChat] 记忆加载跳过: {e}")

            # 构造入站消息
            from bus.events import InboundMessage
            inbound = InboundMessage(
                channel="wechat",
                chat_id=WECHAT_DB_SESSION_ID,
                sender_id=from_user_id,
                content=(memory_ctx + "【用户消息】\n" + text) if memory_ctx else text,
                media=[],
                metadata={"wechat_user_id": from_user_id},
            )

            # 拦截 message 工具的 send_callback，让 media 走 WeChat 发送通道
            from agent.tools.message import MessageTool
            from bus.events import OutboundMessage as _OutboundMsg
            _msg_tool: Optional[MessageTool] = self.agent_loop.tools.get("message")  # type: ignore
            _original_cb = getattr(_msg_tool, "_send_callback", None) if _msg_tool else None
            _wechat_sent_media: list[str] = []  # 记录已通过工具发出的文件
            _tool_db_contents: list[str] = []   # 工具发出的消息内容（供 outer 广播用）

            async def _wechat_send(out: _OutboundMsg) -> None:
                """拦截：media 走 iLink 上传发送，同时持久化到 DB 供前端展示。
                不在这里 broadcast，由 outer 统一广播触发 re-fetch（避免闭包 lazy import 问题）。
                """
                if out.channel == "wechat":
                    # 先发文字（若有）
                    if out.content:
                        for seg in [s.strip() for s in out.content.split("[SPLIT]") if s.strip()]:
                            await self.send_text(from_user_id, seg)
                    # 再发媒体，并持久化到 media 目录
                    persisted_filenames: list[str] = []
                    for mf in (out.media or []):
                        try:
                            await self.send_media(from_user_id, mf)
                            _wechat_sent_media.append(mf)
                            from utils.media import persist_image
                            fn = persist_image(mf)
                            if fn:
                                persisted_filenames.append(fn)
                        except Exception as e:
                            logger.error(f"[WeChat] 媒体发送失败 {mf}: {e}")
                            await self.send_text(from_user_id, f"（图片发送失败: {e}）")

                    # 保存到 DB（文字 + 图片文件名），由 outer 广播触发前端 re-fetch
                    db_content = out.content or "（图片）"
                    save_message(
                        WECHAT_DB_SESSION_ID, "assistant", db_content,
                        images=persisted_filenames if persisted_filenames else None,
                    )
                    _tool_db_contents.append(db_content)
                    logger.info(f"[WeChat] 工具消息已存 DB: {db_content[:30]}")
                elif _original_cb:
                    await _original_cb(out)

            if _msg_tool:
                _msg_tool._send_callback = _wechat_send  # type: ignore

            try:
                # AgentLoop 处理
                response = await self.agent_loop._process_message(inbound)
                reply = response.content if response else ""
            finally:
                # 还原 callback
                if _msg_tool and _original_cb:
                    _msg_tool._send_callback = _original_cb  # type: ignore

            # 清理情绪标签
            import re
            reply = re.sub(r'\s*\[EMOTION:\w+\]', '', reply, flags=re.IGNORECASE).strip()

            # 广播用户消息到前端
            try:
                from bus.broadcaster import get_broadcaster
                broadcaster = get_broadcaster()
                await broadcaster.broadcast({
                    "type": "wechat_message",
                    "role": "user",
                    "content": text,
                    "chat_id": WECHAT_DB_SESSION_ID,
                    "from_user_id": from_user_id,
                })
            except Exception as e:
                logger.debug(f"[WeChat] WS 广播跳过: {e}")

            if reply:
                # 按 [SPLIT] 拆分段落，逐段发送到微信
                segments = [s.strip() for s in reply.split("[SPLIT]") if s.strip()] or [reply]
                # 去掉 [SPLIT] 后的干净内容（供 TTS/Live2D 使用）
                clean_reply = " ".join(segments)

                # 持久化（保留原始 [SPLIT] 供前端展示）
                save_message(WECHAT_DB_SESSION_ID, "assistant", reply)

                # 广播 assistant 消息到前端（wechat_message 供聊天界面刷新）
                try:
                    await broadcaster.broadcast({
                        "type": "wechat_message",
                        "role": "assistant",
                        "content": reply,
                        "chat_id": WECHAT_DB_SESSION_ID,
                    })
                except Exception as e:
                    logger.debug(f"[WeChat] WS 广播跳过: {e}")

                # ① 最高优先：立即逐段发到微信，不等 TTS
                for i, seg in enumerate(segments):
                    await self.send_text(from_user_id, seg)
                    if i < len(segments) - 1:
                        await asyncio.sleep(self.send_chunk_delay)

                # ② 后台广播 reply 给 Live2D / TTS（不阻塞微信发送）
                async def _wechat_reply_broadcast():
                    try:
                        await broadcaster.broadcast({
                            "type": "reply",
                            "content": clean_reply,
                            "emotion": None,
                            "chat_id": WECHAT_DB_SESSION_ID,
                            "source": "wechat",
                        })
                        from tts.manager import get_tts_manager
                        from channels.local_ws import _strip_markdown  # type: ignore
                        tts_mgr = get_tts_manager()
                        if tts_mgr.enabled and clean_reply.strip():
                            tts_text = _strip_markdown(clean_reply)
                            tts_audio_b64 = await tts_mgr.synthesize_to_base64(tts_text)
                            await broadcaster.broadcast({
                                "type": "tts_audio",
                                "audio": tts_audio_b64,
                                "format": "mp3",
                                "chat_id": WECHAT_DB_SESSION_ID,
                            })
                    except Exception as e:
                        logger.debug(f"[WeChat] reply/TTS 广播跳过: {e}")

                asyncio.create_task(_wechat_reply_broadcast())
                logger.info(f"[WeChat] 文字回复已发送 ({len(segments)} 段): {segments[0][:50]}...")
            else:
                # Agent 已通过 message 工具直接发送（如图片）
                logger.info(f"[WeChat] Agent 已通过工具发送回复 ({len(_tool_db_contents)} 条已存 DB)，outer 广播触发刷新")
                # user message broadcast 已经触发了 re-fetch，此处不需额外广播
                # 但为保险再发一次（前端重新 fetch DB 会拿到完整数据）
                try:
                    await broadcaster.broadcast({
                        "type": "wechat_message",
                        "role": "assistant",
                        "content": _tool_db_contents[-1] if _tool_db_contents else "",
                        "chat_id": WECHAT_DB_SESSION_ID,
                    })
                except Exception:
                    pass

        except Exception as e:
            error_msg = f"❌ 处理失败：{str(e)[:100]}"
            logger.error(f"[WeChat] 消息处理失败: {e}")
            await self.send_text(from_user_id, error_msg)
        finally:
            await self.send_typing(from_user_id, False)

    # ── 状态查询 ─────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "online": self.is_online,
            "account_id": self._account_id[:8] + "***" if self._account_id else "",
            "base_url": self._base_url,
            "last_user_id": self._last_user_id[:8] + "***" if self._last_user_id else "",
            "has_credentials": bool(_load_credentials()),
        }
