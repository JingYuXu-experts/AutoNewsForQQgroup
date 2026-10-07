"""从群事件里抓 group_openid。

QQ 官方要求：群消息只能用 group_openid（不是群号），而它只能从机器人收到的事件里拿。
这里直连 QQ 的 WebSocket 网关（标准库实现，无需第三方包）：

    1. 用 AppID/AppSecret 换 access_token
    2. GET /gateway 拿 wss 地址
    3. 连上去，等 READY / GROUP_AT_MESSAGE_CREATE 事件
    4. 从事件的 group_openid 字段取值

用户只需要：点「开始获取」→ 把机器人拉进群 → 在群里 @机器人 发一句话。
"""
from __future__ import annotations

import base64
import gc
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse as urllib_parse

from ..core import log, net
from . import qq

API_HOST = "https://api.sgroup.qq.com"
SANDBOX_HOST = "https://sandbox.api.sgroup.qq.com"
WS_TIMEOUT = 15


class GroupIdCatcher:
    """在后台线程里监听群事件，抓到 group_openid 后回调。

    on_result(ok: bool, value: str, message: str)
        ok=True 时 value 是 group_openid；ok=False 时 message 是失败原因。
    on_status(text: str) 用于向界面反馈进度。
    """

    def __init__(self, app_id: str, app_secret: str, *, sandbox: bool = False,
                 on_result=None, on_status=None):
        self.app_id = app_id
        self.app_secret = app_secret
        self.sandbox = sandbox
        self.on_result = on_result or (lambda *a: None)
        self.on_status = on_status or (lambda *a: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._token = ""
        self._ws_url = ""
        self._fatal = ""
        self._prebuilt = None

    @property
    def fatal(self) -> str:
        """start() 阶段就失败的原因（空字符串表示没失败）。"""
        return self._fatal

    # ---------------------------------------------------------------- 控制
    def start(self) -> None:
        """启动监听（同步完成握手，再交给后台线程收发）。

        设计要点：
        1) 取 token、取网关地址、建立 WebSocket 握手这三步都在**调用方线程**
           里同步做完（就在 _prepare 里）。好处是失败能当场反馈给界面/命令行，
           不用等后台线程；也避免 windows 上 SSL socket 与 urllib 残留连接
           交叉回收时偶发的 fd 被误关。

        2) 握手好的 socket 通过 self._prebuilt 交给后台线程 _run 使用，
           后台线程只负责 select 等待 + 收发 + 心跳，逻辑单一。

        3) 帧类型与 QQ 业务 op 是两套编号，_ws_recv 用负数表示控制帧，
           避免 ping(0x9)/pong(0xA) 与业务 op(9/10) 撞车。
        """
        self._stop.clear()
        self._token = ""
        self._ws_url = ""
        self._fatal = ""
        self._prebuilt = None

        self._prepare()
        if self._fatal:
            self.on_result(False, "", self._fatal)
            return
        if self._prebuilt is None:
            self.on_result(False, "", "网关连接建立失败，请检查网络后重试。")
            return

        self._thread = threading.Thread(target=self._run, name="groupid-catcher",
                                        daemon=True)
        self._thread.start()

    def _prepare(self) -> None:
        """在调用方线程里取 token 与网关地址，失败写入 self._fatal。"""
        host = SANDBOX_HOST if self.sandbox else API_HOST
        try:
            self._token = qq.get_token(self.app_id, self.app_secret, force=True,
                                       sandbox=self.sandbox)
        except qq.QQError as exc:
            self._fatal = str(exc)
            return
        except Exception as exc:
            self._fatal = f"网络异常，无法获取 access_token：{exc}"
            return

        try:
            info = net.get_json(f"{host}/gateway", timeout=20,
                                headers={"Authorization": f"QQBot {self._token}"})
        except Exception as exc:
            self._fatal = f"无法获取网关地址：{exc}"
            return

        self._ws_url = info.get("url") or ""
        if not self._ws_url:
            self._fatal = f"网关返回异常：{info}"
            return

        # Windows 特有：urllib 的 HTTPS 连接对象要立刻回收干净。
        # 否则它可能在 WebSocket 建好之后才析构，析构时按 fd 号 close，
        # 误关掉活跃的 WebSocket —— 症状是首帧之后突然 WinError 10038。
        for _ in range(3):
            gc.collect()

        # 紧接着就在当前帧里把 WebSocket 建好，不留窗口期。
        self._prebuilt = _ws_connect_retry(self._ws_url, self._stop)

    def stop(self, wait: float = 3.0) -> None:
        """请求停止并尽量等线程退出。

        _listen 用的是 1 秒超时轮询，所以一般能在 1 秒内退出；
        重连等待用 self._stop.wait() 而不是 sleep()，也能被立刻叫醒。
        """
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        if wait and self._thread:
            self._thread.join(timeout=wait)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------------------------------------------------------------- 主流程
    def _run(self) -> None:
        """后台线程：只做 WebSocket 收发（HTTP 部分已在 start() 里完成）。"""
        host = SANDBOX_HOST if self.sandbox else API_HOST
        ws_url = self._ws_url

        deadline = time.time() + 300          # 最多等 5 分钟
        fail_streak = 0
        prebuilt = self._prebuilt
        self._prebuilt = None
        while not self._stop.is_set() and time.time() < deadline:
            try:
                found = self._listen(ws_url, prebuilt=prebuilt)
                prebuilt = None
                if found:
                    self.on_result(True, found, "已获取群 ID")
                    return
                # _listen 正常返回（说明是 stop() 触发）——直接退出
                break
            except _AuthExpired:
                fail_streak = 0
                self.on_status("连接凭证过期，正在重新获取…")
                try:
                    token = qq.get_token(self.app_id, self.app_secret, force=True,
                                         sandbox=self.sandbox)
                    info = net.get_json(f"{host}/gateway", timeout=20,
                                        headers={"Authorization": f"QQBot {token}"})
                    ws_url = info.get("url") or ws_url
                    # 同样要把 urllib 的 socket 收干净，否则重连会被误关 fd
                    for _ in range(3):
                        gc.collect()
                except Exception:
                    pass
            except PermissionError as exc:
                # 鉴权/配置问题，重连无用
                self.on_result(False, "", _friendly_ws_error(exc))
                return
            except Exception as exc:
                # 我们自己调 stop() 关掉 socket 时，阻塞在 recv 的线程会立刻
                # 收到 OSError/ValueError —— 这是我们主动结束，不是故障。
                if self._stop.is_set():
                    break
                fail_streak += 1
                self.on_status(f"连接断开（{type(exc).__name__}），正在重连…")
                if fail_streak >= 5:
                    self.on_result(False, "", _friendly_ws_error(exc))
                    return
                # 用事件等待而不是 sleep：stop() 能立刻叫醒它
                self._stop.wait(2)

        if not self._stop.is_set():
            self.on_result(False, "", "等待超时（5 分钟）。请确认机器人已入群，"
                                      "并在群里 @机器人 发一句话后重试。")

    # ---------------------------------------------------------------- WS 通信
    def _listen(self, ws_url: str | None = None,
                prebuilt=None) -> str | None:
        if prebuilt is not None:
            sock = prebuilt
        else:
            sock = _ws_connect_retry(ws_url or self._ws_url, self._stop)
        self._sock = sock
        last_ping = time.time()
        hb_interval = 30.0
        # 短超时 + 轮询：这样既不会因为长时间没事件而假死，
        # 也能及时响应 stop()，还不会把「静默等待」误判成断线。
        sock.settimeout(1.0)
        try:
            while not self._stop.is_set():
                if time.time() - last_ping > hb_interval:
                    _ws_send(sock, json.dumps({"op": 1, "d": None}))
                    last_ping = time.time()

                try:
                    op, payload = _ws_recv(sock)
                except (TimeoutError, socket.timeout):
                    # 没有新帧是完全正常的（群里还没人说话），继续等
                    continue

                if op == -1:                              # ping 帧 → 回 pong
                    _ws_send(sock, b"", frame_op=0xA)
                    continue
                if op in (-2, None):                      # pong / 无法解析
                    continue
                if op == -3:
                    raise ConnectionError("服务端关闭了连接")

                if op == 10:                              # Hello：给出心跳间隔
                    hb = ((payload.get("d") or {}).get("heartbeat_interval") or 30000) / 1000.0
                    hb_interval = max(5.0, hb * 0.9)
                    _ws_send(sock, json.dumps({"op": 1, "d": None}))
                    last_ping = time.time()
                elif op == 0:                             # 事件
                    t = payload.get("t") or ""
                    d = payload.get("d") or {}
                    if t == "READY":
                        self.on_status("已连接 QQ 网关，等待群消息…")
                    else:
                        gid = _extract_group_openid(t, d)
                        if gid:
                            return gid
                elif op == 7:                             # 要求重连
                    raise ConnectionError("服务端要求重连")
                elif op == 9:                             # 无效 session
                    raise _AuthExpired()
        finally:
            try:
                sock.close()
            except Exception:
                pass
            self._sock = None
        return None


class _AuthExpired(Exception):
    pass


def _friendly_ws_error(exc: Exception) -> str:
    """把底层异常翻译成用户能看懂的中文提示。"""
    text = str(exc)
    name = type(exc).__name__
    low = text.lower()
    if "handshake" in low or "握手" in text:
        return f"网关握手失败，可能被网络或代理拦截：{text[:160]}"
    if "timed out" in low or "timeout" in low:
        return "连接 QQ 网关超时：检查本机网络能否访问 api.sgroup.qq.com。"
    if "getaddrinfo" in low or "name or service" in low:
        return "无法解析 QQ 网关地址：检查本机网络或代理设置。"
    if "403" in text or "401" in text:
        return ("网关拒绝了连接（鉴权失败）。请确认：① 机器人已上线；"
                "② 已开通「群聊」能力；③ AppSecret 正确。")
    return f"网关连接失败（{name}）：{text[:160]}"


def _extract_group_openid(event_type: str, d: dict) -> str:
    """只从**群相关**事件里取 group_openid。

    必须认事件类型，不能瞎扫字段：私聊事件（C2C_MESSAGE_CREATE）里也有
    user_openid，扫错了会把私聊的 ID 当群 ID 存进去。
    群消息事件：GROUP_AT_MESSAGE_CREATE / GROUP_ADD_ROBOT / GROUP_MSG_RECEIVE 等，
    它们的 d 顶层就有 group_openid。
    """
    if not isinstance(d, dict):
        return ""
    is_group_event = event_type.startswith("GROUP_") or event_type.startswith("INTERACTION")
    if not is_group_event:
        return ""
    v = d.get("group_openid")
    if isinstance(v, str) and v:
        return v
    # 少数事件把群信息放在 group 子对象里
    group = d.get("group")
    if isinstance(group, dict):
        for key in ("group_openid", "openid", "id"):
            v = group.get(key)
            if isinstance(v, str) and v:
                return v
    return ""


# ---------------------------------------------------------------- WS 协议实现
def _ws_connect(url: str) -> socket.socket:
    """极简 WebSocket 客户端握手（只做我们需要的部分）。"""
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    conn = socket.create_connection((host, port), timeout=WS_TIMEOUT)
    if parsed.scheme == "wss":
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(conn, server_hostname=host)
    else:
        sock = conn

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (f"GET {path} HTTP/1.1\r\n"
           f"Host: {host}\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\n"
           "Sec-WebSocket-Version: 13\r\n"
           "\r\n")
    sock.sendall(req.encode("ascii"))

    # 读取响应头
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("握手失败：连接被关闭")
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    if "101" not in head.split("\r\n")[0]:
        raise ConnectionError(f"握手失败：{head.splitlines()[0] if head else '无响应'}")
    leftover = buf.split(b"\r\n\r\n", 1)[1]

    # 交给 _SocketWithBuffer：它会把 socket 切回「阻塞模式」，
    # 之后所有等待都靠 select.select()，避免 SSL 层超时状态机，
    # 也就不会再出现 WinError 10038。
    return _SocketWithBuffer(sock, leftover)


def _ws_connect_retry(url: str, stop: threading.Event, attempts: int = 3):
    """连接网关，失败就重试几次。

    单次网络抖动不该让整轮监听重新开始（重新握手 + 重新心跳），
    所以这里自己做小重试；三次都失败才把异常抛给上层。
    """
    last: Exception | None = None
    for i in range(attempts):
        if stop.is_set():
            raise ConnectionError("已取消")
        try:
            return _ws_connect(url)
        except Exception as exc:
            last = exc
            if i < attempts - 1:
                stop.wait(1.5)
    raise last if last else ConnectionError("连接失败")


class _SocketWithBuffer:
    """把握手时多读到的字节保留下来，避免丢帧。

    为什么不用 socket.settimeout() 做轮询？
        Windows 上 SSLSocket.recv() 一旦触发超时，内部会走
        「非阻塞 fd + select」的路径；反复超时会让 SSL 层把底层 fd 状态弄坏，
        最终下一次 recv 直接抛 WinError 10038（在一个非套接字上尝试了一个操作）。
    做法：
        socket 保持阻塞模式，用 select.select() 等可读，等到才 recv。
        这样 SSL 层永远只在「确定有数据」时才被调用，不会踩到超时路径。
    """

    def __init__(self, sock: socket.socket, initial: bytes = b""):
        self._sock = sock
        self._buf = bytearray(initial or b"")
        # 阻塞模式，避免 SSL 内部超时状态机
        try:
            self._sock.setblocking(True)
            self._sock.settimeout(None)
        except Exception:
            pass

    def _wait_readable(self, timeout: float) -> bool:
        import select
        try:
            r, _, _ = select.select([self._sock], [], [], timeout)
        except (OSError, ValueError):
            raise ConnectionError("连接已失效")
        return bool(r)

    def recv_exact(self, n: int, timeout: float | None = None) -> bytes:
        """精确读取 n 字节。

        缓冲区里已经有足够数据就直接返回，不碰 socket。
        数据不足时用 select 等待，等待期间可以被 stop() 打断（由 timeout 控制）。
        """
        while len(self._buf) < n:
            if not self._wait_readable(timeout if timeout is not None else self._default_timeout):
                raise TimeoutError("等待数据超时")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("连接已关闭")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    _default_timeout = 2.0

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass

    def settimeout(self, t) -> None:
        # 记录等待上限（供 recv_exact 用），不改 socket 本身的阻塞模式
        self._default_timeout = t if (t is None or t > 0) else 2.0


def _ws_send(sock, text: str | bytes, frame_op: int = 0x1) -> None:
    """发送一个客户端帧（带掩码，符合 RFC 6455）。

    frame_op: 0x1 文本帧（默认）、0x8 关闭、0x9 ping、0xA pong。
    """
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text or b"")
    header = bytearray([0x80 | (frame_op & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv(sock) -> tuple[int | None, dict]:
    """读取一帧。

    注意：这里返回的是 **WebSocket 帧类型**，与 QQ 的业务 op 字段是两回事，
    所以约定用负数表示控制帧，避免和业务 op（0/1/2/6/7/9/10）撞车：
        -1 表示 ping 帧（需回 pong）
        -2 表示 pong 帧
        -3 表示服务端关闭
        非负数是业务 op，附带已解析的事件体。
    """
    hdr = sock.recv_exact(2)
    b1, b2 = hdr[0], hdr[1]
    frame_op = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", sock.recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv_exact(8))[0]

    mask = sock.recv_exact(4) if masked else None
    data = sock.recv_exact(length) if length else b""
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

    if frame_op == 0x8:                      # 关闭
        return -3, {}
    if frame_op == 0x9:                      # ping
        return -1, {}
    if frame_op == 0xA:                      # pong
        return -2, {}
    # 文本 / 二进制帧：里面才是业务协议
    if frame_op in (0x1, 0x2):
        try:
            payload = json.loads(data.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return None, {}
        op = payload.get("op")
        return (op if isinstance(op, int) else None), payload
    return None, {}


def capture_group_openid(app_id: str, app_secret: str, *, timeout: int = 300,
                         on_status=None) -> tuple[bool, str]:
    """同步版本：阻塞等待群事件（命令行/无界面场景用）。"""
    status = on_status or (lambda *a: None)
    result: dict = {}
    done = threading.Event()

    def _cb(ok, value, message):
        result.update({"ok": ok, "value": value, "message": message})
        done.set()

    catcher = GroupIdCatcher(app_id, app_secret, on_result=_cb, on_status=status)
    catcher.start()
    try:
        done.wait(timeout + 10)
    finally:
        catcher.stop()
    if not result:
        return False, "等待超时"
    return result["ok"], result["value"] or result.get("message", "")


def quick_capture(app_id: str, app_secret: str, seconds: int = 20) -> tuple[bool, str]:
    """命令行辅助：监听若干秒，抓到就返回。"""
    status = lambda t: log.log(f"· {t}")          # noqa: E731
    return capture_group_openid(app_id, app_secret, timeout=seconds, on_status=status)
