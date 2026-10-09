#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中东要闻推送 · 程序入口。

直接双击 exe 打开图形界面；也可以用命令行参数跑一次性任务。

命令行用法：
    news-pusher                    打开图形界面
    news-pusher --run              立刻跑一轮（有界面也能看到日志）
    news-pusher --dry-run          试跑，不发送
    news-pusher --capture-group    命令行抓取群 ID（配合 --appid/--secret）
    news-pusher --version
"""
from __future__ import annotations

import argparse
import os
import sys

# 打包后以 exe 运行也能正确导入 app 包
if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "1.0.2"


def _soften_console() -> None:
    """让控制台编码不再成为崩溃源。

    Windows 控制台默认 GBK，日志里的 ✅ 之类字符编码不了会抛
    UnicodeEncodeError；未捕获时程序直接崩（而且往往是在「已经推送成功、
    只差打一行日志」的时候崩，最难排查）。这里统一把不可编码字符降级成 ?。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def _report_fatal(exc: BaseException) -> None:
    """兜底：把未处理异常写进日志，并弹一个看得懂的提示框。"""
    try:
        from app.core import config, log
        log.log(f"程序异常退出：{type(exc).__name__}: {exc}")
        where = config.logs_dir()
    except Exception:
        where = ""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "中东要闻推送 · 出错了",
            f"{type(exc).__name__}: {exc}\n\n详情见日志：\n{where}",
        )
        root.destroy()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(prog="news-pusher",
                                description="中东要闻自动推送（QQ 群）")
    ap.add_argument("--run", action="store_true", help="立刻执行一轮抓取与推送")
    ap.add_argument("--dry-run", action="store_true", help="试跑一轮，不发送、不写状态")
    ap.add_argument("--capture-group", action="store_true", help="监听群事件，抓取群 ID")
    ap.add_argument("--appid", help="配合 --capture-group 使用")
    ap.add_argument("--secret", help="配合 --capture-group 使用")
    ap.add_argument("--version", action="version", version=f"news-pusher {VERSION}")
    args = ap.parse_args()

    from app.core import config, log

    if args.capture_group:
        from app.push import gateway
        cfg = config.load()
        app_id = args.appid or cfg.get("appId")
        secret = args.secret or cfg.get("appSecret")
        if not (app_id and secret):
            print("请先填写 AppID 与 AppSecret（或在命令行传入 --appid --secret）")
            return 1
        print("正在监听群事件。请把机器人拉进群，并在群里 @机器人 发一句话…")
        ok, value = gateway.capture_group_openid(
            app_id, secret, timeout=300,
            on_status=lambda t: print(f"  · {t}"))
        if ok:
            cfg["groupOpenid"] = value
            config.save(cfg)
            print(f"✅ 已获取并保存群 ID：{value}")
            return 0
        print(f"❌ 获取失败：{value}")
        return 1

    if args.run or args.dry_run:
        from app import runner
        cfg = config.load()
        ok, missing = config.is_ready(cfg)
        if not ok:
            print("配置不完整，还缺：" + "、".join(missing))
            return 1
        res = runner.run_once(cfg, dry_run=args.dry_run)
        if res.error:
            print(f"失败：{res.error}")
            return 1
        if res.sent:
            print(f"完成，推送 {res.sent} 条")
        else:
            print(f"本轮没有推送：{res.skipped_reason}")
        return 0

    # 默认：打开界面
    try:
        from app.gui import main as gui_main
    except ImportError as exc:
        print(f"无法启动图形界面（{exc}）。")
        print("可用命令行模式：news-pusher --run")
        return 1
    gui_main()
    return 0


if __name__ == "__main__":
    _soften_console()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except BaseException as exc:            # 兜底：不要让用户看到裸 traceback
        _report_fatal(exc)
        sys.exit(1)
