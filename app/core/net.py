"""HTTP 与文本小工具（仅用标准库，打包后无外部依赖）。"""
from __future__ import annotations

import gzip
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def request(url: str, *, data: bytes | None = None, headers: dict | None = None,
            method: str | None = None, timeout: int = 20,
            retries: int = 2, backoff: float = 1.5) -> bytes:
    """带重试的 HTTP 请求，返回响应体字节。全部失败时抛最后一次异常。

    这里刻意在读到数据后 **立刻显式关闭并断开底层套接字**：
    Windows 上如果让 urllib/ssl 的连接对象走延迟析构，
    它可能在我们之后建好别的 SSL 连接（例如 QQ 网关 WebSocket）之后才被回收，
    回收时按 fd 号 close，正好关掉了新连接 —— 典型症状是
    OSError [WinError 10038]（在一个非套接字上尝试了一个操作）。
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            hdrs = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
            hdrs.update(headers or {})
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            resp = urllib.request.urlopen(req, timeout=timeout)
            try:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.decompress(raw)
                elif "deflate" in enc:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            finally:
                _hard_close(resp)
            return raw
        except urllib.error.HTTPError as exc:
            # 4xx 不必重试（鉴权、参数问题重试也没用）
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise
            last = exc
        except Exception as exc:                       # 网络层错误，重试
            last = exc
        if attempt < retries:
            import time
            time.sleep(backoff * (attempt + 1))
    raise last if last else RuntimeError("请求失败")


def _hard_close(resp) -> None:
    """强制关闭 HTTP 响应及其底层套接字，避免延迟析构误关新连接。"""
    sock = None
    try:
        fp = getattr(resp, "fp", None)
        if fp is not None:
            sock = getattr(fp, "raw", None)
            sock = getattr(sock, "_sock", None) or sock
    except Exception:
        sock = None
    try:
        resp.close()
    except Exception:
        pass
    for obj in (sock,):
        if obj is None:
            continue
        try:
            obj.shutdown(2)              # socket.shutdown
        except Exception:
            pass
        try:
            obj.close()
        except Exception:
            pass


def get_text(url: str, timeout: int = 20, headers: dict | None = None) -> str:
    raw = request(url, timeout=timeout, headers=headers)
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def get_json(url: str, timeout: int = 20, headers: dict | None = None) -> dict:
    return json.loads(get_text(url, timeout=timeout, headers=headers) or "{}")


def post_json(url: str, payload: dict, *, headers: dict | None = None,
              timeout: int = 60, retries: int = 2) -> dict:
    hdrs = {"Content-Type": "application/json; charset=utf-8"}
    hdrs.update(headers or {})
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raw = request(url, data=body, headers=hdrs, method="POST",
                  timeout=timeout, retries=retries)
    text = raw.decode("utf-8", errors="ignore")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def strip_tags(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    compact = re.sub(r"\s", "", text)
    if not compact:
        return 0.0
    return len(re.findall(r"[\u4e00-\u9fff]", compact)) / len(compact)


def quote(text: str) -> str:
    return urllib.parse.quote(text or "")
