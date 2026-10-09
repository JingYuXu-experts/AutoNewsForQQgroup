"""抓取各信源的列表页，产出统一的条目结构。

统一结构：
    {
      "source":  "shafaq",            # 信源 key
      "title":   "标题（原文）",
      "url":     "完整链接",
      "date":    "列表页上的时间原文（可能为空）",
      "summary": "摘要（可能为空）",
      "when":    UTC datetime 或 None, # 解析后的发布时间
      "time_known": bool,              # 是否精确到时刻
    }
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..core import log, net, timeutil
from . import registry

# 正文页里常见的发布时间字段
DATE_PATTERNS = (
    r'<meta[^>]+(?:property|itemprop|name)=["\'](?:article:published_time|datePublished'
    r'|datepublished|pubdate|publishdate|publish_time|og:published_time|date)["\'][^>]*'
    r'content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|itemprop|name)=["\']'
    r'(?:article:published_time|datePublished|datepublished|pubdate|publishdate'
    r'|publish_time|og:published_time|date)["\']',
    r'<time[^>]+datetime=["\']([^"\']+)["\']',
    r'"(?:datePublished|publishedAt|date_published|created_at)"\s*:\s*"([^"]+)"',
)


def fetch_all(cfg: dict) -> list[dict]:
    """按配置抓取所有启用的信源，返回条目列表。"""
    enabled = cfg.get("sources") or {}
    timeout = int(cfg.get("timeoutSeconds", 20))
    items: list[dict] = []

    for key, src in registry.SOURCES.items():
        if not enabled.get(key, True):
            continue
        try:
            page = net.get_text(src["url"], timeout=timeout)
        except Exception as exc:
            log.log(f"· {src['name']} 抓取失败（{type(exc).__name__}），跳过")
            continue

        try:
            if src["kind"] == "rudaw":
                got = parse_rudaw(page)
            elif src["kind"] == "shafaq":
                got = parse_shafaq(page)
            elif src["kind"] == "rss":
                got = parse_rss(page, key)
            else:
                got = parse_generic(page, key, src["url"])
        except Exception as exc:
            log.log(f"· {src['name']} 解析失败（{type(exc).__name__}），跳过")
            continue

        for it in got:
            it["source"] = key
            it["_date_only"] = False
            date_s = (it.get("date") or "").strip()
            # 列表页没给时间时，从链接路径里的 /YYYY/MM/DD/ 补（只有日期）
            if not date_s:
                date_s = it["date"] = date_from_url(it.get("url", ""))
            w = timeutil.parse(date_s)
            if w:
                # 只有日期、没有时刻 → 标记为「时间不精确」
                if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", date_s):
                    it["_date_only"] = True
            it["when"] = w
            it["time_known"] = bool(w) and not it["_date_only"]

        log.log(f"· {src['name']}：抓到 {len(got)} 条")
        items.extend(got)

    return items


def probe_publish_time(item: dict, timeout: int = 20, max_ahead_hours: int = 6):
    """列表页没给时间时，回源正文页确认发布时间。"""
    try:
        page = net.get_text(item["url"], timeout=timeout)
    except Exception:
        return None
    for pat in DATE_PATTERNS:
        m = re.search(pat, page, re.I)
        if not m:
            continue
        when = timeutil.parse(m.group(1))
        if not when:
            continue
        # 明显跑到未来去了，不采信
        import datetime as dt
        if when > timeutil.now_utc() + dt.timedelta(hours=max_ahead_hours):
            continue
        return when
    return None


def fetch_article_text(url: str, timeout: int = 20, limit: int = 900) -> str:
    """抓正文片段，给大模型更多事实依据（失败返回空串）。"""
    try:
        page = net.get_text(url, timeout=timeout)
    except Exception:
        return ""
    page = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form).*?</\1>", " ", page)
    text = net.strip_tags(page)
    text = re.sub(r"(?i)(cookie|subscribe|newsletter|advertisement|sign up).*?(?=\s[A-Z])", " ", text)
    return text[:limit].strip()


# ---------------------------------------------------------------- 各站解析器
def parse_rudaw(txt: str) -> list[dict]:
    items, seen = [], set()
    for href, raw in re.findall(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', txt, re.S):
        if "/english/" not in href:
            continue
        text = net.strip_tags(raw)
        m = re.match(r"^(.*?)\s+(\d{2}-\d{2}-\d{4})\s+(.{20,})$", text)
        if not m:
            continue
        section, date_s, title = m.group(1).strip(), m.group(2), m.group(3).strip()
        if title in seen:
            continue
        seen.add(title)
        url = href if href.startswith("http") else "https://www.rudaw.net" + href
        summary = ""
        if len(title) > 130:
            mm = re.search(r"(.{30,125}?[.?!])\s+(.{40,})", title)
            if mm:
                title, summary = mm.group(1).strip(), mm.group(2).strip()
            else:
                title, summary = title[:120].strip(), title[120:].strip()
        items.append({"title": title[:180], "url": url, "section": section,
                      "date": date_s, "summary": summary[:300]})
    return items


def parse_shafaq(txt: str) -> list[dict]:
    items, seen = [], set()
    for href, raw in re.findall(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', txt, re.S):
        if not re.search(r"^/en/[A-Za-z][A-Za-z\-]*/[A-Za-z0-9\-]{15,}$", href):
            continue
        title = net.strip_tags(raw)
        if len(title) < 25 or title in seen:
            continue
        seen.add(title)
        items.append({"title": title[:180], "url": "https://shafaq.com" + href,
                      "section": href.split("/")[2], "date": "", "summary": ""})
    return items


# 链接形如 /Detail/2026/10/09/777931/... —— 有些 RSS（如 Press TV）不给 pubDate，
# 但把日期写在 URL 里，只能从这里补。
_URL_DATE = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)")


def date_from_url(url: str) -> str:
    """从链接路径里抠出 /YYYY/MM/DD/，返回 'YYYY-MM-DD'，抠不到返回空串。"""
    m = _URL_DATE.search(url or "")
    if not m:
        return ""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not (2000 <= int(y) <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y}-{mo:02d}-{d:02d}"


def parse_rss(txt: str, source: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(txt.encode("utf-8", errors="ignore"))
    except ET.ParseError:
        return items
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue

        def val(*names: str) -> str:
            for nm in names:
                for child in node:
                    if child.tag.split("}")[-1] == nm and child.text:
                        return net.strip_tags(child.text)
            return ""

        title, link = val("title"), val("link")
        if not link:
            for child in node:
                if child.tag.split("}")[-1] == "link":
                    link = child.attrib.get("href", "")
        if not title or not link:
            continue
        items.append({"title": title[:180], "url": link, "section": "",
                      "date": val("pubDate", "published", "updated")[:40],
                      "summary": val("description", "summary", "content")[:300]})
    return items


def parse_generic(txt: str, source: str, base: str) -> list[dict]:
    items, seen = [], set()
    for href, raw in re.findall(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', txt, re.S):
        title = net.strip_tags(raw)
        if len(title) < 35 or len(title) > 160 or title in seen:
            continue
        if not (href.startswith("http") or href.startswith("/")):
            continue
        if re.search(r"\.(css|js|png|jpe?g|svg|ico)$", href, re.I):
            continue
        seen.add(title)
        url = href if href.startswith("http") else base.rstrip("/") + href
        items.append({"title": title[:180], "url": url, "section": "",
                      "date": "", "summary": ""})
    return items
