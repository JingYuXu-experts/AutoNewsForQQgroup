"""一轮完整流程：抓取 → 选稿 → DeepSeek 翻译编辑 → 复核 → 质检 → 推送 → 记账。

界面和命令行都调用这里。所有失败都在内部处理成中文提示，不往外抛异常。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

from .core import config, log, timeutil
from .compose import brief, llm, select
from .push import qq
from .sources import fetch, registry


class RunResult:
    def __init__(self) -> None:
        self.sent = 0
        self.skipped_reason = ""
        self.error = ""
        self.tier = ""

    @property
    def ok(self) -> bool:
        return not self.error


# ---------------------------------------------------------------- 状态
def load_state() -> dict:
    path = config.state_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            pass
    return {"seen": {}, "recentTopics": [], "dailyCount": {}, "log": []}


def save_state(state: dict) -> None:
    try:
        with open(config.state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- 主流程
def run_once(cfg: dict, *, dry_run: bool = False) -> RunResult:
    """执行一轮。dry_run=True 时不发消息、不写状态。"""
    res = RunResult()
    state = load_state()

    # ---- 每日上限 ----
    today = timeutil.today_beijing().isoformat()
    counts = state.setdefault("dailyCount", {})
    if counts.get(today, 0) >= int(cfg.get("dailyLimit", 30)):
        res.skipped_reason = f"今日已达推送上限（{cfg.get('dailyLimit')} 条），明天再继续。"
        log.log(f"· {res.skipped_reason}")
        return res

    # ---- 抓取 ----
    log.log("开始抓取信源…")
    items = fetch.fetch_all(cfg)
    if not items:
        res.skipped_reason = "所有信源都抓不到内容（可能是网络问题）。本轮不推送。"
        log.log(f"· {res.skipped_reason}")
        return res
    log.log(f"共 {len(items)} 条候选，按时间与重要性筛选…")

    # ---- 选稿 ----
    picked = select.pick(cfg, items, state)
    res.tier = picked.tier
    if not picked.items:
        res.skipped_reason = picked.empty_reason(cfg)
        log.log(f"· {res.skipped_reason}")
        return res
    log.log(f"选中 {len(picked.items)} 条（时效层级：{picked.tier}）")

    # ---- 翻译与编辑（DeepSeek）----
    api_key = cfg.get("deepseekApiKey") or ""
    if not api_key:
        res.error = "未填写 DeepSeek API Key，无法翻译。请在设置页填写后重试。"
        log.log(f"· {res.error}")
        return res

    for it in picked.items:
        it["source_name"] = registry.SOURCES.get(it["source"], {}).get("name", it["source"])
        it["pub_display"] = brief.pub_display(it)

    final = []
    for it in picked.items:
        log.log(f"· 翻译编辑中：{it['title'][:50]}")
        zh = llm.compose(it, it["source_name"], api_key, cfg)
        if not zh:
            log.log("· 该条翻译失败，丢弃")
            continue
        log.log(f"· 复核中：{zh['title'][:40]}")
        rv = llm.review(it, zh, it["source_name"], api_key, cfg)
        zh = {**rv["brief"], "method": zh.get("method", "")}
        it["_zh"] = zh
        it["_review"] = rv
        note = f"｜{rv['note']}" if rv["note"] else ""
        log.log(f"· 复核[{rv['verdict']}]{note}")
        ok, why = llm.passes_quality(zh)
        if not ok:
            log.log(f"· 质检未通过（{why}），丢弃该条")
            continue
        final.append(it)

    if not final:
        res.skipped_reason = "本轮稿件未通过质检（翻译或复核未成功），不推送，下一轮会重试。"
        log.log(f"· {res.skipped_reason}")
        return res

    # ---- 组装与配图 ----
    now = dt.datetime.now()
    stem = f"中东要闻-{now:%Y-%m-%d}-{now:%H%M}"
    for it in final:
        it["_media"] = [] if dry_run else brief.attach_media(it, stem, cfg)
    text = brief.build(final, now)

    if dry_run:
        log.log("（试跑模式：未发送）")
        print("\n" + "=" * 56 + "\n" + text + "\n" + "=" * 56)
        res.sent = len(final)
        return res

    # ---- 推送 ----
    app_id = cfg.get("appId") or ""
    app_secret = cfg.get("appSecret") or ""
    gid = cfg.get("groupOpenid") or ""
    parts = brief.split(text, int(cfg.get("maxChars", 1800)))
    media = brief.media_for(stem, cfg, int(cfg.get("maxMedia", 2)))

    try:
        seq = 1
        for part in parts:
            qq.send_text(app_id, app_secret, gid, part, seq=seq)
            seq += 1
            if seq <= len(parts):
                time.sleep(1.5)
        for mpath in media:
            info = qq.upload_media(app_id, app_secret, gid, mpath,
                                   max_image_mb=float(cfg.get("maxImageMB", 4)),
                                   max_video_mb=float(cfg.get("maxVideoMB", 20)))
            if info:
                qq.send_media(app_id, app_secret, gid, info, seq=seq)
                seq += 1
                time.sleep(1.5)
    except qq.QQError as exc:
        res.error = str(exc)
        log.log(f"· 推送失败：{exc}")
        return res
    except Exception as exc:
        res.error = f"推送异常：{type(exc).__name__} {exc}"
        log.log(f"· {res.error}")
        return res

    # ---- 落盘与记账 ----
    out_dir = config.reports_dir()
    out_path = os.path.join(out_dir, stem + ".md")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass

    counts[today] = counts.get(today, 0) + len(final)
    state["dailyCount"] = counts
    for it in final:
        state.setdefault("seen", {})[it["url"]] = time.time()
        state.setdefault("recentTopics", []).append({
            "at": time.time(), "score": it.get("score", 0),
            "tokens": it.get("tokens") or sorted(select.title_tokens(it["title"])),
            "title": it["title"][:80],
        })
    state["recentTopics"] = state["recentTopics"][-500:]
    state.setdefault("log", []).append({
        "at": f"{now:%Y-%m-%d %H:%M}", "count": len(final),
        "titles": [i["_zh"].get("title", "")[:60] for i in final],
    })
    state["log"] = state["log"][-200:]
    save_state(state)

    res.sent = len(final)
    titles = "、".join(i["_zh"].get("title", "")[:30] for i in final)
    log.log(f"✅ 已推送 {len(final)} 条：{titles}")
    return res


def notify_empty(cfg: dict, reason: str, dry_run: bool = False) -> bool:
    """没有符合时效要求的内容时，向群里明确说明（按间隔节流）。"""
    if not cfg.get("emptyNotice", True):
        return False
    state = load_state()
    gap_h = float(cfg.get("emptyNoticeHours", 6))
    if time.time() - float(state.get("lastEmptyNotice", 0) or 0) < gap_h * 3600:
        log.log(f"· 「暂无最新新闻」提示未满 {gap_h:.0f} 小时，不重复发送")
        return False

    text = f"【中东要闻速报】暂时没有最新新闻\n{reason}"
    if dry_run:
        print("\n" + text + "\n")
        return False
    try:
        qq.send_text(cfg.get("appId", ""), cfg.get("appSecret", ""),
                     cfg.get("groupOpenid", ""), text)
    except qq.QQError as exc:
        log.log(f"· 状态提示发送失败：{exc}")
        return False
    state["lastEmptyNotice"] = time.time()
    save_state(state)
    log.log("· 已发送「暂时没有最新新闻」提示")
    return True
