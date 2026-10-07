"""简报组装：把选中的条目拼成一条可发进群的纯文本消息。"""
from __future__ import annotations

import datetime as dt
import re

from ..core import config, log, net, timeutil

WEEKDAYS = "一二三四五六日"

# 占位字段标签不写进推送内容（【开场白】【结束语】等）
FIELD_LABEL_RE = re.compile(
    r"【(?:开场白|开场词|开场语|开场|结束语|结语|结束词|收尾语|收尾|结尾|正文|说明|备注|寄语)】")


def strip_field_labels(text: str) -> str:
    out = []
    for line in text.splitlines():
        had = bool(FIELD_LABEL_RE.search(line))
        line = FIELD_LABEL_RE.sub("", line).strip()
        if had and not line:
            continue
        out.append(line)
    return "\n".join(out)


def build(items: list[dict], run_time: dt.datetime | None = None) -> str:
    """把若干已翻译好的条目拼成简报文本。"""
    run_time = run_time or dt.datetime.now()
    lines = [
        "国际新闻简报 · 中东要闻",
        f"{run_time:%Y年%m月%d日} 星期{WEEKDAYS[run_time.weekday()]} "
        f"{run_time:%H:%M} 北京时间",
        "",
    ]

    for it in items:
        zh = it.get("_zh") or {}
        lines.append(f"【要闻】{zh.get('title', '')}")
        lines.append(f"【发布】{it.get('pub_display') or '发布时间未标注'}")
        for key, seg in zh.get("sections", []):
            seg = (seg or "").strip()
            if seg and seg[-1] not in "。！？…）】":
                seg += "。"
            lines.append(f"【{key}】{seg}")
        src_name = it.get("source_name") or it.get("source", "")
        lines.append(f"【来源】{src_name} · {(it.get('title') or '')[:80]}")
        lines.append(it.get("url", ""))
        if it.get("_media"):
            lines.append(f"【配图】{len(it['_media'])} 张")
        lines.append("")

    lines.append("附：本稿内容均据公开报道整理，涉及争议性表述以原始报道来源为准。")
    return strip_field_labels("\n".join(lines)).strip()


def split(text: str, max_chars: int = 1800) -> list[str]:
    """按段落切分，保证单条消息不超长。"""
    if len(text) <= max_chars:
        return [text]
    parts, buf, size = [], [], 0
    for para in text.split("\n"):
        plen = len(para) + 1
        if size + plen > max_chars and buf:
            parts.append("\n".join(buf).strip())
            buf, size = [], 0
        buf.append(para)
        size += plen
    if buf:
        parts.append("\n".join(buf).strip())
    return [p for p in parts if p]


# ---------------------------------------------------------------- 配图
def attach_media(item: dict, stem: str, cfg: dict) -> list[str]:
    """从正文页取 og:image，存到本期专属目录，返回文件路径列表。"""
    if not cfg.get("withMedia", True):
        return []
    from ..sources import fetch

    timeout = int(cfg.get("timeoutSeconds", 20))
    try:
        page = net.get_text(item["url"], timeout=timeout)
    except Exception:
        return []

    m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                   page, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                      page, re.I))
    if not m:
        return []
    img_url = m.group(1)

    # 防乱配图三道闸
    if re.search(r"(logo|icon|avatar|placeholder|default|banner|sprite|favicon"
                 r"|no-?image|noimage|cover-default)", img_url, re.I):
        log.log("· 跳过配图：疑似站点默认图/图标")
        return []
    if not re.search(r"\.(jpe?g|png|webp)(\?|$)", img_url, re.I):
        return []

    try:
        data = net.request(img_url, timeout=timeout, retries=1)
    except Exception:
        return []
    if not data or len(data) > float(cfg.get("maxImageMB", 4)) * 1024 * 1024:
        return []
    if len(data) < 20 * 1024:
        log.log(f"· 跳过配图：仅 {len(data)/1024:.0f} KB，疑似图标")
        return []

    ext = ".jpg" if re.search(r"jpe?g", img_url, re.I) else \
          (".png" if "png" in img_url.lower() else ".jpg")
    sub = config.media_dir()
    import os
    d = os.path.join(sub, stem)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{stem}-01{ext}")
    with open(path, "wb") as f:
        f.write(data)
    log.log(f"· 已下载配图 {os.path.basename(path)}（{len(data)/1024:.0f} KB）")
    return [path]


def media_for(stem: str, cfg: dict, max_n: int = 2) -> list[str]:
    """取某期简报对应的媒体文件（只认本期专属目录，避免串图）。"""
    import os
    d = os.path.join(config.media_dir(), stem)
    if not os.path.isdir(d):
        return []
    exts = (".jpg", ".jpeg", ".png", ".mp4")
    found = [os.path.join(d, n) for n in sorted(os.listdir(d))
             if n.lower().endswith(exts)]
    found.sort(key=lambda p: (not p.lower().endswith((".jpg", ".jpeg", ".png")), p))
    return found[:max_n]


def pub_display(item: dict) -> str:
    return timeutil.format_pub(item.get("when"), item.get("time_known", True))
