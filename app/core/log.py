"""日志：同时写文件 + 回调给界面显示。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading

from . import config

_LOCK = threading.Lock()
_listeners: list = []


def add_listener(fn) -> None:
    """界面注册一个回调，用于实时显示日志。"""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_listener(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _log_file() -> str:
    day = dt.date.today().strftime("%Y-%m-%d")
    return os.path.join(config.logs_dir(), f"{day}.log")


def _console_print(line: str) -> None:
    """往控制台打一行。

    Windows 控制台默认是 GBK，日志里的 ✅ 这类字符编码不了会抛
    UnicodeEncodeError —— 未捕获时整个程序会崩掉（发完消息才崩，最难查）。
    这里做「降级不抛异常」处理：编不了的字符换成 ?，实在不行就丢弃这一行。
    """
    stream = sys.stdout
    if stream is None:                     # 打包成 windowed exe 时没有控制台
        return
    try:
        print(line, flush=True)
        return
    except UnicodeEncodeError:
        pass
    except (OSError, ValueError, AttributeError):
        return
    try:
        enc = getattr(stream, "encoding", None) or "utf-8"
        stream.write(line.encode(enc, "replace").decode(enc, "replace") + "\n")
        stream.flush()
    except Exception:
        pass


def log(msg: str, level: str = "info") -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    with _LOCK:
        try:
            with open(_log_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    _console_print(line)
    for fn in list(_listeners):
        try:
            fn(line, level)
        except Exception:
            pass


def tail(lines: int = 300) -> str:
    """读取当天日志的最后若干行，供界面初始化显示。"""
    try:
        with open(_log_file(), "r", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


def clean_old(days: int = 14) -> None:
    """清理超过 N 天的日志文件。"""
    cutoff = dt.date.today() - dt.timedelta(days=days)
    try:
        for name in os.listdir(config.logs_dir()):
            if not name.endswith(".log"):
                continue
            try:
                day = dt.date.fromisoformat(name[:-4])
            except ValueError:
                continue
            if day < cutoff:
                os.remove(os.path.join(config.logs_dir(), name))
    except OSError:
        pass
