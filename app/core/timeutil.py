"""时间处理：统一按北京时间展示，内部统一用 UTC 比较。

中东站点多数用当地时间（UTC+3 / UTC+4）。页面没写时区时按 UTC+3 保守换算——
宁可把稿子判老一点，也不能把隔夜旧闻当新闻推出去。
"""
from __future__ import annotations

import datetime as dt
import email.utils
import re

BEIJING_TZ = dt.timezone(dt.timedelta(hours=8), "CST")
SRC_TZ = dt.timezone(dt.timedelta(hours=3), "SRC")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def today_beijing() -> dt.date:
    return now_utc().astimezone(BEIJING_TZ).date()


def parse(raw: str):
    """把各种写法的时间串解析成 UTC datetime；解析不出返回 None。

    支持：RSS（Mon, 06 Oct 2026 10:00:00 GMT）、ISO 8601、epoch 秒/毫秒、
    「2026-10-06 14:20」、「06-10-2026」（日-月-年）、纯日期。
    """
    s = (raw or "").strip().strip("'\"")
    if not s:
        return None
    try:                                            # RSS / RFC 822
        when = email.utils.parsedate_to_datetime(s)
        if when:
            return (when.astimezone(dt.timezone.utc) if when.tzinfo
                    else when.replace(tzinfo=dt.timezone.utc))
    except Exception:
        pass
    if re.fullmatch(r"\d{10}", s):
        return dt.datetime.fromtimestamp(int(s), dt.timezone.utc)
    if re.fullmatch(r"\d{13}", s):
        return dt.datetime.fromtimestamp(int(s) / 1000, dt.timezone.utc)
    try:                                            # ISO 8601（含 Z 与偏移）
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
        return (d.astimezone(dt.timezone.utc) if d.tzinfo
                else d.replace(tzinfo=SRC_TZ).astimezone(dt.timezone.utc))
    except ValueError:
        pass
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T](\d{1,2}):(\d{2})", s)
    if m:                                           # 无时区的「2026-10-06 14:20」
        d = dt.datetime(*[int(x) for x in m.groups()], tzinfo=SRC_TZ)
        return d.astimezone(dt.timezone.utc)
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s)    # Rudaw：06-10-2026
    if m:
        d = dt.datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=SRC_TZ)
        return d.astimezone(dt.timezone.utc)
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=SRC_TZ)
        return d.astimezone(dt.timezone.utc)
    return None


def to_beijing(when) -> str:
    if not when:
        return ""
    return when.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def human_ago(when) -> str:
    """「12 分钟前」这类相对时间。"""
    if not when:
        return ""
    mins = int((now_utc() - when).total_seconds() // 60)
    if mins < 1:
        return "刚刚"
    if mins < 60:
        return f"{mins} 分钟前"
    if mins < 48 * 60:
        return f"{mins // 60} 小时前"
    return f"{mins // 1440} 天前"


def format_pub(when, time_known: bool = True) -> str:
    """统一的发布时间标注，例如：
    发布于 2026-10-07 09:11 北京时间（1 小时前）
    """
    if not when:
        return "发布时间未标注"
    tail = f"（{human_ago(when)}）" if time_known else f"（源站未标注具体时刻，{human_ago(when)}）"
    return f"发布于 {to_beijing(when)} 北京时间{tail}"


def in_hours(when, hours: float) -> bool:
    return bool(when) and (now_utc() - when) <= dt.timedelta(hours=hours)
