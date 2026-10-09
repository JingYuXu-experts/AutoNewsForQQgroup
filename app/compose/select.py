"""选稿：时效过滤、去重、打分排序。

时效是硬规则（优先级高于重要性）：
    1. 先用「最近 freshHours 小时」（默认 2 小时）的条目
    2. 这一层没有达标的，才放宽到「当日 00:00 起」
    3. 跨日旧闻一律剔除；发布时间确认不了的一律不用
    4. 输出按发布时间从新到旧
"""
from __future__ import annotations

import re

from ..core import log, timeutil
from ..sources import fetch, registry

# 标题里无信息量的常用词（同事件去重时剔除）
GENERIC_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "has", "have", "was", "were",
    "says", "said", "say", "amid", "after", "before", "over", "into", "about", "more",
    "than", "new", "news", "report", "reported", "claim", "claimed", "warn", "warned",
    "target", "targeted", "deny", "denied", "kill", "killed", "dead", "death", "deaths",
    "injured", "injury", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "latest", "live", "update", "updates", "breaking", "exclusive", "video",
    "photo", "analysis", "opinion", "middle", "east", "day", "week", "year", "people",
}


def title_tokens(text: str) -> set[str]:
    """标题切成关键信息词（小写、去复数、剔除常用词）。"""
    out = set()
    for w in re.findall(r"[a-z][a-z0-9\-']{2,}", (text or "").lower()):
        w = w.replace("'", "").strip("-")
        if w.endswith("s") and len(w) > 4:
            w = w[:-1].strip("-")
        if w and w not in GENERIC_WORDS:
            out.add(w)
    return out


def same_event(a: set, b: set, ratio: float = 0.32, min_shared: int = 2) -> bool:
    if not a or not b:
        return False
    shared = a & b
    if len(shared) < min_shared:
        return False
    return len(shared) / min(len(a), len(b)) >= ratio


def overlap_ratio(a: set, b: set) -> float:
    """两条标题的信息词重合度（0~1）。用来识别「几乎是同一篇稿子」。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class PickResult:
    """选稿结果，含统计信息，便于日志与界面展示。"""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.tier = ""            # "2 小时内" 或 "当日"
        self.total = 0
        self.stale = 0            # 跨日/超时旧闻
        self.undated = 0          # 确认不了时间
        self.duplicate = 0        # 同事件重复
        self.below_score = 0      # 未达重要性门槛
        self.excluded = 0         # 软新闻/被排除

    def empty_reason(self, cfg: dict) -> str:
        fresh_h = cfg.get("freshHours", 2)
        if self.total == 0:
            return (f"本轮未抓到任何条目（信源暂时不可达）。最近 {fresh_h} 小时内、"
                    "以及今日范围内均无符合条件的新进展，未用旧闻替代。")
        bits = []
        if self.stale:
            bits.append(f"跨日/超时旧闻 {self.stale} 条")
        if self.undated:
            bits.append(f"无法确认发布时间 {self.undated} 条")
        if self.duplicate:
            bits.append(f"近期已推过的同一事件 {self.duplicate} 条")
        if self.below_score:
            bits.append(f"未达重要性门槛 {self.below_score} 条")
        detail = ("（已剔除：" + "、".join(bits) + "）") if bits else ""
        return (f"最近 {fresh_h} 小时内、以及今日范围内均无符合条件的新进展，"
                f"为避免以旧闻充数，本轮不推送。{detail}")


def pick(cfg: dict, items: list[dict], state: dict) -> PickResult:
    """按「时效优先 + 重要性 + 去重」挑出本轮要推的条目。"""
    res = PickResult()
    res.total = len(items)

    seen = state.setdefault("seen", {})
    recent = state.setdefault("recentTopics", [])
    now = timeutil.now_utc()
    today = timeutil.today_beijing()

    fresh_hours = float(cfg.get("freshHours", 2))
    max_age = float(cfg.get("maxAgeHours", 24))
    today_only = bool(cfg.get("todayOnly", True))
    min_score = int(cfg.get("minScore", 3))
    dedupe_hours = float(cfg.get("dedupeHours", 24))
    probe_limit = int(cfg.get("probeLimit", 8))

    # 清理 7 天前的记录
    import time
    cutoff = time.time() - 7 * 86400
    for url in list(seen):
        if seen[url] < cutoff:
            seen.pop(url, None)
    recent[:] = [r for r in recent if time.time() - r.get("at", 0) < 7 * 86400]

    # ---- 第一轮：粗筛 ----
    candidates = []
    need_probe = []
    for it in items:
        if it["url"] in seen:
            continue
        if registry.is_excluded(it):
            res.excluded += 1
            continue
        sc = registry.score(it)
        if sc < min_score:
            res.below_score += 1
            continue
        it["score"] = sc
        if not it.get("when"):
            need_probe.append(it)
        candidates.append(it)

    # ---- 第二轮：回源确认发布时间（分数高的先查）----
    if need_probe and probe_limit > 0:
        need_probe.sort(key=lambda x: -x["score"])
        for it in need_probe[:probe_limit]:
            got = fetch.probe_publish_time(it, int(cfg.get("timeoutSeconds", 20)))
            if got:
                it["when"] = got
                it["time_known"] = True

    # ---- 第三轮：时效过滤 + 去重 ----
    pool = []
    for it in candidates:
        when = it.get("when")
        if not when:
            res.undated += 1
            log.log(f"· 跳过（确认不了发布时间）：{it['title'][:55]}")
            continue
        age = now - when
        if (age > __import__("datetime").timedelta(hours=max_age)
                or (today_only and when.astimezone(timeutil.BEIJING_TZ).date() != today)):
            res.stale += 1
            continue

        toks = title_tokens(it["title"])
        blocked = None
        if dedupe_hours > 0:
            for r in recent:
                if time.time() - r.get("at", 0) > dedupe_hours * 3600:
                    continue
                rtoks = set(r.get("tokens") or [])
                if not same_event(toks, rtoks):
                    continue
                # 措辞几乎一致（同一篇稿子换了个源）→ 一律算重复
                if overlap_ratio(toks, rtoks) >= 0.8:
                    blocked = r
                    break
                # 措辞有变化，但分数没高出一截 → 还是同一件事的复述，算重复。
                # 只有「明显更重要」才当作新的重大进展（(更新)）放行。
                if it["score"] < int(r.get("score", 0)) + 4:
                    blocked = r
                    break

        if blocked is not None:
            res.duplicate += 1
            log.log(f"· 跳过同事件旧稿：{it['title'][:55]}（近期已推："
                    f"{blocked.get('title', '')[:35]}）")
            continue

        it["tokens"] = sorted(toks)
        it["age_hours"] = max(0.0, age.total_seconds() / 3600)
        it["tag"] = registry.cn_tag(it)
        it["fresh"] = it["time_known"] and age.total_seconds() <= fresh_hours * 3600
        pool.append(it)

    if res.stale:
        log.log(f"· 已剔除 {res.stale} 条跨日/超时旧闻（{fresh_hours:.0f} 小时内优先、"
                f"最宽只到当日，绝对上限 {max_age:.0f} 小时）")
    if res.duplicate:
        log.log(f"· 已跳过 {res.duplicate} 条与近期推送同事件的稿件")

    # ---- 分层：先「N 小时内」，没有才用「当日」 ----
    fresh_pool = [it for it in pool if it["fresh"]]
    if fresh_pool:
        chosen, res.tier = fresh_pool, f"{fresh_hours:.0f} 小时内"
    else:
        chosen, res.tier = pool, "当日"

    # ---- 排序：重要性 + 时效折减（越旧越扣分） ----
    rank_src = {"rudaw": 0, "shafaq": 0, "iswnews": 1, "almonitor": 1, "presstv": 1}
    for it in chosen:
        it["rank"] = it["score"] - min(3.0, it.get("age_hours", 0) * 0.5)
    chosen.sort(key=lambda x: (-x["rank"], -x["when"].timestamp(),
                               rank_src.get(x["source"], 2), x["url"]))

    picked = chosen[: int(cfg.get("localDigestMax", 1))]
    picked.sort(key=lambda x: -x["when"].timestamp())      # 输出从新到旧
    res.items = picked
    return res
