"""QQ 群机器人接口（官方 v2）。

只做四件事：取 token、发文字、上传媒体、发媒体。
token 带缓存，避免每轮重复申请；出错时抛出带中文说明的异常，界面可直接显示。
"""
from __future__ import annotations

import base64
import os
import threading
import time

from ..core import net

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_HOST = "https://api.sgroup.qq.com"
SANDBOX_HOST = "https://sandbox.api.sgroup.qq.com"

IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4"}
AUDIO_EXT = {".silk", ".wav", ".mp3", ".flac"}

_TOKEN_LOCK = threading.Lock()
_token_cache: dict = {"value": "", "expire_at": 0.0, "key": ""}


class QQError(RuntimeError):
    """QQ 接口错误，message 是可以直接展示给用户的中文说明。"""


def _host(sandbox: bool = False) -> str:
    return SANDBOX_HOST if sandbox else API_HOST


def get_token(app_id: str, app_secret: str, *, force: bool = False,
              sandbox: bool = False) -> str:
    """获取 access_token（带缓存，剩余不足 5 分钟会自动续期）。"""
    cache_key = f"{app_id}:{app_secret}"
    with _TOKEN_LOCK:
        if (not force and _token_cache["value"] and _token_cache["key"] == cache_key
                and time.time() < _token_cache["expire_at"]):
            return _token_cache["value"]

    body = net.post_json(TOKEN_URL, {"appId": str(app_id), "clientSecret": app_secret},
                         timeout=30, retries=2)
    token = body.get("access_token")
    if not token:
        code = body.get("code")
        msg = str(body.get("message") or "")
        if code == 100016 or "appid" in msg.lower() or "secret" in msg.lower():
            raise QQError("AppID 或 AppSecret 不正确。请到 QQ 开放平台 → 开发设置，"
                          "重新复制机器人密钥后再试。")
        if code == 100007:
            raise QQError("该机器人尚未在 QQ 开放平台完成配置或未上线。")
        raise QQError(f"获取 access_token 失败：{msg or body}")

    with _TOKEN_LOCK:
        _token_cache.update({
            "value": token,
            "key": cache_key,
            "expire_at": time.time() + max(60, int(body.get("expires_in") or 7200) - 300),
        })
    return token


def clear_token_cache() -> None:
    with _TOKEN_LOCK:
        _token_cache.update({"value": "", "expire_at": 0.0, "key": ""})


def _headers(token: str) -> dict:
    return {"Authorization": f"QQBot {token}"}


def send_text(app_id: str, app_secret: str, group_openid: str, content: str,
              *, seq: int = 1, sandbox: bool = False, retries: int = 3) -> str:
    """发一条文字消息，返回消息 ID。"""
    token = get_token(app_id, app_secret, sandbox=sandbox)
    url = f"{_host(sandbox)}/v2/groups/{group_openid}/messages"
    payload = {"content": content, "msg_type": 0, "msg_seq": seq}

    for _ in range(retries):
        try:
            resp = net.request(url, data=_json_bytes(payload),
                               headers={**_headers(token),
                                        "Content-Type": "application/json; charset=utf-8"},
                               method="POST", timeout=30, retries=0)
            body = _load(resp)
        except Exception as exc:
            raise QQError(_net_error_hint(exc)) from exc

        if body.get("id"):
            return body["id"]
        code = body.get("code")
        msg = str(body.get("message") or body)
        if code in (11244, 11253):           # token 过期，重取一次
            token = get_token(app_id, app_secret, force=True, sandbox=sandbox)
            continue
        if code == 22009:                    # 主动消息额度不足
            raise QQError("主动消息额度已用尽或该群未开启「接收机器人主动消息」。"
                          "请在群资料页 → 机器人，打开主动消息开关。")
        if code in (11251, 11252):
            raise QQError("机器人不在该群，或群 ID 不正确。请重新执行「获取群 ID」。")
        raise QQError(f"发送失败：{msg}")
    raise QQError("发送失败：多次重试仍未成功，请稍后再试。")


def upload_media(app_id: str, app_secret: str, group_openid: str, path: str,
                 *, sandbox: bool = False, max_image_mb: float = 4,
                 max_video_mb: float = 20) -> str | None:
    """上传本地图片/视频，返回 file_info（失败返回 None）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        file_type = 1
        limit = max_image_mb
    elif ext in VIDEO_EXT:
        file_type = 2
        limit = max_video_mb
    elif ext in AUDIO_EXT:
        file_type = 3
        limit = max_image_mb
    else:
        return None

    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb > limit:
        return None

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    token = get_token(app_id, app_secret, sandbox=sandbox)
    url = f"{_host(sandbox)}/v2/groups/{group_openid}/files"
    payload = {"file_type": file_type, "file_data": b64,
               "file_name": os.path.basename(path), "srv_send_msg": False}
    try:
        resp = net.request(url, data=_json_bytes(payload),
                           headers={**_headers(token),
                                    "Content-Type": "application/json; charset=utf-8"},
                           method="POST", timeout=180, retries=1)
        body = _load(resp)
    except Exception:
        return None
    return body.get("file_info")


def send_media(app_id: str, app_secret: str, group_openid: str, file_info: str,
               *, seq: int = 2, sandbox: bool = False) -> bool:
    """发送已上传的媒体。"""
    token = get_token(app_id, app_secret, sandbox=sandbox)
    url = f"{_host(sandbox)}/v2/groups/{group_openid}/messages"
    payload = {"msg_type": 7, "media": {"file_info": file_info}, "msg_seq": seq}
    try:
        resp = net.request(url, data=_json_bytes(payload),
                           headers={**_headers(token),
                                    "Content-Type": "application/json; charset=utf-8"},
                           method="POST", timeout=180, retries=1)
        return bool(_load(resp).get("id"))
    except Exception:
        return False


def test_connection(app_id: str, app_secret: str) -> tuple[bool, str]:
    """界面「测试连接」用：只验证密钥能否换来 token。"""
    try:
        get_token(app_id, app_secret, force=True)
        return True, "密钥有效，已成功获取 access_token"
    except QQError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"网络异常：{type(exc).__name__}"


# ---------------------------------------------------------------- 内部工具
def _json_bytes(payload: dict) -> bytes:
    import json
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _load(raw: bytes) -> dict:
    import json
    text = raw.decode("utf-8", errors="ignore")
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"message": text[:200]}


def _net_error_hint(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "请求超时：本机网络无法访问 api.sgroup.qq.com，检查网络或代理。"
    if "getaddrinfo" in text or "Name or service not known" in text:
        return "无法解析服务器地址：检查本机网络或代理设置。"
    return f"网络请求失败（{name}）：{text[:150]}"
