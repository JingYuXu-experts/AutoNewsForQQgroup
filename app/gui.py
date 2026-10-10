"""图形界面：设置、启停、日志、使用说明。

一个窗口三个标签页：
    推送     —— 运行状态、启停按钮、下一轮时间、手动跑一轮
    设置     —— 密钥、群 ID 抓取、间隔与门槛等参数
    说明     —— 使用流程与注意事项（软件里自带教程）

只依赖 Python 自带的 tkinter，打包后无需额外运行库。
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .core import config, log
from .push import gateway, qq
from . import runner
from .scheduler import Scheduler

APP_TITLE = "中东要闻推送"
FONT = ("Microsoft YaHei UI", 10)

HELP_TEXT = """【三步开始使用】

1. 填密钥
   打开「设置」页，填入两样东西：
     · DeepSeek API Key —— 到 platform.deepseek.com → API Keys 创建
     · QQ 机器人 AppID 和 AppSecret —— 到 QQ 开放平台 → 你的机器人 → 开发设置
   填完点「测试连接」，绿字表示通过。

2. 获取群 ID
   点「获取群 ID」按钮，然后：
     · 把机器人拉进你要推送的 QQ 群
     · 在群里 @机器人 随便发一句话（例如「@机器人 你好」）
   ID 会自动抓取并填入，显示「已获取 ✓」即可。

3. 启动
   回到「推送」页点「启动」，就会按时自动抓取、翻译、推送到群里。

【开机自动运行】
设置页勾选「开机自动启动」，下次开机程序会自动打开。想连推送也自动开始，
再勾选「启动后自动开始推送」。

【常见问题】
· 提示「AppID 或 AppSecret 不正确」
  密钥有一次性的：在 QQ 开放平台重置 AppSecret 后，旧密钥立即失效，必须重新复制。
· 提示「主动消息额度已用尽」
  群资料页 → 机器人 → 打开「接收机器人主动消息」。这是 QQ 的限制，不是程序问题。
· 群 ID 抓不到
  确认机器人确实在群里；必须 @机器人 发消息，只是聊天不算。
· 没收到新闻
  看日志页。若显示「最近 2 小时内没有符合条件的新进展」，说明确实没有新消息，
  程序不会拿旧闻凑数。可以把「间隔」调短。
· 推送的内容想更严或更松
  设置页「重要性门槛」：数字越大越挑剔（推荐 3），越小越容易推。
· 想只推最近的新闻
  「优先窗口」默认 2 小时；调小只推最新的，调大放宽到更早但仍在今天范围内。
  跨日旧闻一律不会推。

【几点说明】
· 配置保存在 %APPDATA%\\中东要闻推送\\config.json，程序换位置或升级都不会丢。
· 日志按天存放在同一目录的 logs 文件夹。
· 所有推送内容都是中文，由 DeepSeek 翻译并校对，不使用原文直出。
"""


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = config.load()
        self.scheduler: Scheduler | None = None
        self.catcher: gateway.GroupIdCatcher | None = None
        self._ui_queue: queue.Queue = queue.Queue()

        root.title(APP_TITLE)
        root.geometry("760x620")
        root.minsize(700, 560)
        self._apply_window_icon(root)

        self._build_ui()
        self._bind_log()
        self._refresh_check()
        self._poll_queue()

        log.log(f"{APP_TITLE} 已打开，配置目录：{config.config_dir()}")
        if not config.is_ready(self.cfg)[0]:
            missing = "、".join(config.is_ready(self.cfg)[1])
            log.log(f"· 还差这些才能开始推送：{missing}")
        if self.cfg.get("autoStartPush"):
            self.root.after(1500, self._start_push)

    # ---------------------------------------------------------------- 界面
    @staticmethod
    def _apply_window_icon(root: tk.Tk) -> None:
        """给窗口标题栏/任务栏设置图标（打包与源码运行都适用）。"""
        ico = config.resource_path("assets", "app.ico")
        try:
            if os.path.exists(ico):
                root.iconbitmap(default=ico)
        except Exception:
            try:
                root.iconbitmap(ico)
            except Exception:
                pass                              # 设不上就算了，不影响使用

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Ok.TLabel", foreground="#137333")
        style.configure("Err.TLabel", foreground="#c5221f")
        style.configure("Hint.TLabel", foreground="#5f6368")

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        self.nb = nb

        self.tab_run = ttk.Frame(nb, padding=14)
        self.tab_set = ttk.Frame(nb, padding=14)
        self.tab_log = ttk.Frame(nb, padding=14)
        self.tab_help = ttk.Frame(nb, padding=14)
        nb.add(self.tab_run, text="  推送  ")
        nb.add(self.tab_set, text="  设置  ")
        nb.add(self.tab_log, text="  日志  ")
        nb.add(self.tab_help, text="  使用说明  ")

        self._build_run_tab()
        self._build_settings_tab()
        self._build_log_tab()
        self._build_help_tab()

    # ---- 推送页 ----
    def _build_run_tab(self) -> None:
        f = self.tab_run
        ttk.Label(f, text="运行状态", style="Title.TLabel").pack(anchor="w")

        box = ttk.LabelFrame(f, text=" 当前状态 ", padding=14)
        box.pack(fill="x", pady=(10, 12))
        self.var_state = tk.StringVar(value="未启动")
        ttk.Label(box, textvariable=self.var_state,
                  font=("Microsoft YaHei UI", 12)).pack(anchor="w")
        self.var_next = tk.StringVar(value="—")
        ttk.Label(box, textvariable=self.var_next, style="Hint.TLabel").pack(
            anchor="w", pady=(6, 0))

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(0, 12))
        self.btn_start = ttk.Button(btns, text="启 动", width=14,
                                    command=self._toggle_push)
        self.btn_start.pack(side="left")
        ttk.Button(btns, text="立即跑一轮", width=14,
                   command=self._run_now).pack(side="left", padx=8)
        ttk.Button(btns, text="试跑（不发送）", width=14,
                   command=self._dry_run).pack(side="left")

        info = ttk.LabelFrame(f, text=" 配置检查 ", padding=14)
        info.pack(fill="both", expand=True)
        self.var_check = tk.StringVar()
        self.lbl_check = ttk.Label(info, textvariable=self.var_check,
                                   justify="left", wraplength=660)
        self.lbl_check.pack(anchor="w")

    # ---- 设置页 ----
    def _build_settings_tab(self) -> None:
        f = self.tab_set
        canvas = tk.Canvas(f, highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=690)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ① 大模型
        g1 = ttk.LabelFrame(inner, text=" ① DeepSeek（用于翻译与校对） ", padding=12)
        g1.pack(fill="x", pady=(0, 10))
        self.v_ds_key = tk.StringVar(value=self.cfg.get("deepseekApiKey", ""))
        self._row(g1, "API Key", self.v_ds_key, secret=True,
                  hint="platform.deepseek.com → API Keys")
        self.v_ds_model = tk.StringVar(value=self.cfg.get("deepseekModel", "deepseek-chat"))
        self._row(g1, "模型", self.v_ds_model,
                  hint="默认 deepseek-chat，一般不用改")

        # ② QQ 机器人
        g2 = ttk.LabelFrame(inner, text=" ② QQ 机器人 ", padding=12)
        g2.pack(fill="x", pady=(0, 10))
        self.v_appid = tk.StringVar(value=self.cfg.get("appId", ""))
        self._row(g2, "AppID", self.v_appid,
                  hint="QQ 开放平台 → 开发设置")
        self.v_secret = tk.StringVar(value=self.cfg.get("appSecret", ""))
        self._row(g2, "AppSecret", self.v_secret, secret=True,
                  hint="重置后会变，必须重新复制")

        row = ttk.Frame(g2)
        row.pack(fill="x", pady=(8, 0))
        self.v_gid = tk.StringVar(value=self.cfg.get("groupOpenid", ""))
        ttk.Label(row, text="群 ID", width=11).pack(side="left")
        ttk.Entry(row, textvariable=self.v_gid, width=34).pack(side="left")
        self.btn_gid = ttk.Button(row, text="获取群 ID", width=11,
                                  command=self._toggle_capture)
        self.btn_gid.pack(side="left", padx=6)
        self.v_gid_state = tk.StringVar(value="")
        self.lbl_gid_state = ttk.Label(g2, textvariable=self.v_gid_state, style="Hint.TLabel")
        self.lbl_gid_state.pack(anchor="w", pady=(4, 0), padx=(96, 0))

        row2 = ttk.Frame(g2)
        row2.pack(fill="x", pady=(10, 0))
        ttk.Button(row2, text="测试连接", width=11,
                   command=self._test_qq).pack(side="left")
        ttk.Button(row2, text="发送测试消息", width=14,
                   command=self._test_message).pack(side="left", padx=6)

        # ③ 推送节奏
        g3 = ttk.LabelFrame(inner, text=" ③ 推送节奏 ", padding=12)
        g3.pack(fill="x", pady=(0, 10))
        self.v_interval = tk.StringVar(value=str(self.cfg.get("intervalMinutes", 60)))
        self._row(g3, "间隔（分钟）", self.v_interval, width=8,
                  hint="多久抓一次，建议 30~120")
        self.v_daily = tk.StringVar(value=str(self.cfg.get("dailyLimit", 30)))
        self._row(g3, "每日上限", self.v_daily, width=8, hint="每天最多推几条")
        self.v_score = tk.StringVar(value=str(self.cfg.get("minScore", 3)))
        self._row(g3, "重要性门槛", self.v_score, width=8,
                  hint="越大越挑剔，推荐 3；软新闻会被滤掉")
        self.v_fresh = tk.StringVar(value=str(self.cfg.get("freshHours", 2)))
        self._row(g3, "优先窗口（小时）", self.v_fresh, width=8,
                  hint="只推这么久以内的；没有才放宽到当日，跨日不推")
        self.v_media = tk.BooleanVar(value=bool(self.cfg.get("withMedia", True)))
        ttk.Checkbutton(g3, text="推送时附带新闻配图",
                        variable=self.v_media).pack(anchor="w", pady=(6, 0))

        # ④ 启动方式
        g4 = ttk.LabelFrame(inner, text=" ④ 开机与启动 ", padding=12)
        g4.pack(fill="x", pady=(0, 10))
        self.v_auto = tk.BooleanVar(value=bool(self.cfg.get("autoStart", False)))
        ttk.Checkbutton(g4, text="开机自动启动本程序", variable=self.v_auto,
                        command=self._apply_autostart).pack(anchor="w")
        self.v_autopush = tk.BooleanVar(value=bool(self.cfg.get("autoStartPush", False)))
        ttk.Checkbutton(g4, text="打开后自动开始推送（免点「启动」）",
                        variable=self.v_autopush).pack(anchor="w")

        # 保存
        save_row = ttk.Frame(inner)
        save_row.pack(fill="x", pady=(4, 16))
        ttk.Button(save_row, text="保存设置", width=14,
                   command=self._save).pack(side="left")
        self.v_save_state = tk.StringVar(value="")
        ttk.Label(save_row, textvariable=self.v_save_state,
                  style="Ok.TLabel").pack(side="left", padx=10)

    def _row(self, parent, label: str, var, *, secret: bool = False,
             width: int = 44, hint: str = "") -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=11).pack(side="left")
        ent = ttk.Entry(row, textvariable=var, width=width,
                        show="●" if secret else "")
        ent.pack(side="left")
        if secret:
            def toggle():
                ent.configure(show="" if ent.cget("show") else "●")
            ttk.Button(row, text="显示", width=5, command=toggle).pack(side="left", padx=4)
        if hint:
            ttk.Label(row, text=hint, style="Hint.TLabel").pack(side="left", padx=6)

    # ---- 日志页 ----
    def _build_log_tab(self) -> None:
        f = self.tab_log
        ttk.Label(f, text="运行日志", style="Title.TLabel").pack(anchor="w")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(8, 6))
        ttk.Button(bar, text="刷新", width=8, command=self._reload_log).pack(side="left")
        ttk.Button(bar, text="打开日志文件夹", width=16,
                   command=lambda: os.startfile(config.logs_dir())).pack(side="left", padx=6)
        ttk.Button(bar, text="打开配置文件夹", width=16,
                   command=lambda: os.startfile(config.config_dir())).pack(side="left")

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        self.txt_log = tk.Text(wrap, wrap="word", font=("Consolas", 9),
                               bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set, state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._reload_log()

    # ---- 说明页 ----
    def _build_help_tab(self) -> None:
        f = self.tab_help
        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True)
        txt = tk.Text(wrap, wrap="word", font=FONT, relief="flat",
                      bg="#fafafa", padx=14, pady=10)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")

    # ---------------------------------------------------------------- 交互
    def _save(self) -> bool:
        try:
            cfg = self.cfg
            cfg["deepseekApiKey"] = self.v_ds_key.get().strip()
            cfg["deepseekModel"] = self.v_ds_model.get().strip() or "deepseek-chat"
            cfg["appId"] = self.v_appid.get().strip()
            cfg["appSecret"] = self.v_secret.get().strip()
            cfg["groupOpenid"] = self.v_gid.get().strip()
            cfg["intervalMinutes"] = max(1, int(self.v_interval.get() or 60))
            cfg["dailyLimit"] = max(1, int(self.v_daily.get() or 30))
            cfg["minScore"] = max(0, int(self.v_score.get() or 3))
            cfg["freshHours"] = max(1, float(self.v_fresh.get() or 2))
            cfg["withMedia"] = bool(self.v_media.get())
            cfg["autoStart"] = bool(self.v_auto.get())
            cfg["autoStartPush"] = bool(self.v_autopush.get())
        except ValueError:
            messagebox.showwarning("提示", "间隔、上限、门槛这些请填数字。")
            return False

        path = config.save(cfg)
        self.v_save_state.set("已保存 ✓")
        self.root.after(2500, lambda: self.v_save_state.set(""))
        log.log(f"设置已保存 → {path}")
        self._refresh_check()
        if self.scheduler and self.scheduler.running:
            log.log(f"· 新间隔 {cfg['intervalMinutes']} 分钟将在下一轮后生效")
        return True

    def _refresh_check(self) -> None:
        ok, missing = config.is_ready(self.cfg)
        if ok:
            self.var_check.set("✓ 配置完整，可以开始推送。")
            self.lbl_check.configure(style="Ok.TLabel")
        else:
            self.var_check.set("还差这些才能开始推送：\n  · " + "\n  · ".join(missing))
            self.lbl_check.configure(style="Err.TLabel")

    def _test_qq(self) -> None:
        if not self._save():
            return
        app_id, secret = self.cfg.get("appId", ""), self.cfg.get("appSecret", "")
        if not app_id or not secret:
            messagebox.showwarning("提示", "请先填写 AppID 和 AppSecret。")
            return
        log.log("正在测试 QQ 机器人密钥…")
        ok, msg = qq.test_connection(app_id, secret)
        log.log(("✅ " if ok else "❌ ") + msg)
        (messagebox.showinfo if ok else messagebox.showerror)("测试连接", msg)

    def _test_message(self) -> None:
        if not self._save():
            return
        ok, missing = config.is_ready(self.cfg)
        if not ok:
            messagebox.showwarning("提示", "还缺：" + "、".join(missing))
            return
        try:
            import datetime as dt
            qq.send_text(self.cfg["appId"], self.cfg["appSecret"],
                         self.cfg["groupOpenid"],
                         f"【测试消息】{APP_TITLE} 已连通，这是一条测试推送。\n"
                         f"{dt.datetime.now():%Y-%m-%d %H:%M}")
        except qq.QQError as exc:
            log.log(f"❌ 测试消息失败：{exc}")
            messagebox.showerror("发送失败", str(exc))
            return
        log.log("✅ 测试消息已发送，请到群里查看")
        messagebox.showinfo("成功", "测试消息已发送，请到 QQ 群里查看。")

    def _toggle_capture(self) -> None:
        if self.catcher and self.catcher.running:
            self.catcher.stop()
            self.catcher = None
            self.btn_gid.configure(text="获取群 ID")
            self.v_gid_state.set("已取消")
            return

        if not self._save():
            return
        app_id, secret = self.cfg.get("appId", ""), self.cfg.get("appSecret", "")
        if not app_id or not secret:
            messagebox.showwarning("提示", "请先填写 AppID 和 AppSecret，再获取群 ID。")
            return

        self.v_gid_state.set("正在连接 QQ 网关…")
        self.btn_gid.configure(text="停止获取")
        self.nb.select(self.tab_set)

        def on_status(text: str) -> None:
            self._ui_queue.put(("gid_state", text, ""))

        def on_result(ok: bool, value: str, message: str) -> None:
            self._ui_queue.put(("gid_result", ok, value or message))

        self.catcher = gateway.GroupIdCatcher(app_id, secret,
                                              on_result=on_result,
                                              on_status=on_status)
        self.catcher.start()
        # start() 是同步的：凭证/网络有问题会当场失败，on_result 已经把失败
        # 塞进队列了（_poll_queue 会恢复按钮并弹窗），这里只要别弹「请 @机器人」
        # 的引导框就行。
        if not self.catcher.running:
            log.log(f"获取群 ID 未启动：{self.catcher.fatal or '连接失败'}")
            return
        log.log("开始监听群事件。请把机器人拉进群，并在群里 @机器人 发一句话。")
        messagebox.showinfo(
            "获取群 ID",
            "接下来请：\n\n"
            "1. 把机器人拉进你要推送的 QQ 群\n"
            "2. 在群里 @机器人 随便发一句话（例如「@机器人 你好」）\n\n"
            "抓到后会自动填入，最多等 5 分钟。")

    def _apply_autostart(self) -> None:
        """写/删注册表启动项。"""
        try:
            import sys
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            name = APP_TITLE
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                if self.v_auto.get():
                    if getattr(sys, "frozen", False):
                        cmd = f'"{sys.executable}"'
                    else:
                        cmd = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
                    log.log("· 已设置开机自动启动")
                else:
                    try:
                        winreg.DeleteValue(key, name)
                        log.log("· 已取消开机自动启动")
                    except FileNotFoundError:
                        pass
        except Exception as exc:
            log.log(f"· 设置开机启动失败：{exc}")

    def _toggle_push(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.stop()
            self.btn_start.configure(text="启 动")
            self.var_state.set("未启动")
            self.var_next.set("—")
            return

        if not self._save():
            return
        ok, missing = config.is_ready(self.cfg)
        if not ok:
            messagebox.showwarning("还不能启动", "请先补全：\n\n· " + "\n· ".join(missing))
            return

        self.scheduler = Scheduler(self.cfg,
                                   on_tick=self._on_tick,
                                   on_state=lambda t: self._ui_queue.put(("state", t, "")))
        self.scheduler.start()
        self.btn_start.configure(text="停 止")
        self.var_state.set("运行中")
        self.nb.select(self.tab_run)

    def _run_now(self) -> None:
        if not self._save():
            return
        ok, missing = config.is_ready(self.cfg)
        if not ok:
            messagebox.showwarning("还不能推送", "请先补全：\n\n· " + "\n· ".join(missing))
            return
        if not self.scheduler:
            self.scheduler = Scheduler(self.cfg, on_tick=self._on_tick,
                                       on_state=lambda t: self._ui_queue.put(("state", t, "")))
        log.log("手动触发一轮…")
        self.scheduler.run_now()

    def _dry_run(self) -> None:
        if not self._save():
            return
        log.log("试跑：只抓取与翻译，不发送…")

        def job():
            try:
                res = runner.run_once(self.cfg, dry_run=True)
                if res.sent:
                    log.log(f"试跑成功，本应推送 {res.sent} 条（内容见控制台/日志）")
                elif res.error:
                    log.log(f"试跑失败：{res.error}")
                else:
                    log.log(f"试跑结果：{res.skipped_reason}")
            except Exception as exc:
                log.log(f"试跑出错：{type(exc).__name__} {exc}")
        threading.Thread(target=job, daemon=True).start()

    def _on_tick(self, res) -> None:
        if res.error:
            self._ui_queue.put(("state", "出错"))
        elif res.sent:
            self._ui_queue.put(("state", f"运行中 · 刚推送 {res.sent} 条"))
        else:
            self._ui_queue.put(("state", "运行中"))

    # ---------------------------------------------------------------- 日志与队列
    def _bind_log(self) -> None:
        log.add_listener(lambda line, level="info": self._ui_queue.put(("log", line, "")))
        self._append(log.tail(200))

    def _reload_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.insert("1.0", log.tail(400))
        self.txt_log.configure(state="disabled")
        self.txt_log.see("end")

    def _append(self, text: str) -> None:
        if not text:
            return
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text if text.endswith("\n") else text + "\n")
        # 只保留最近 2000 行，避免长时间运行后界面变卡
        if int(self.txt_log.index("end-1c").split(".")[0]) > 2000:
            self.txt_log.delete("1.0", "500.0")
        self.txt_log.configure(state="disabled")
        self.txt_log.see("end")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, a, b = self._ui_queue.get_nowait()
                if kind == "log":
                    self._append(a)
                elif kind == "state":
                    self.var_state.set(a)
                    if self.scheduler and self.scheduler.next_run:
                        self.var_next.set(f"下次运行："
                                          f"{self.scheduler.next_run:%Y-%m-%d %H:%M}")
                elif kind == "gid_state":
                    self.v_gid_state.set(a)
                    self.lbl_gid_state.configure(style="Hint.TLabel")
                elif kind == "gid_result":
                    if a:
                        self.v_gid.set(b)
                        self.v_gid_state.set("已获取 ✓")
                        self.lbl_gid_state.configure(style="Ok.TLabel")
                        self.btn_gid.configure(text="获取群 ID")
                        self._save()
                        self.catcher = None
                        log.log(f"✅ 已获取群 ID：{b}")
                    else:
                        self.v_gid_state.set("获取失败")
                        self.lbl_gid_state.configure(style="Err.TLabel")
                        self.btn_gid.configure(text="获取群 ID")
                        self.catcher = None
                        log.log(f"❌ 获取群 ID 失败：{b}")
                        messagebox.showerror("获取失败", b)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def on_close(self) -> None:
        if self.scheduler and self.scheduler.running:
            if not messagebox.askokcancel("退出", "推送正在运行，退出将停止推送。确定退出？"):
                return
            self.scheduler.stop()
        if self.catcher:
            self.catcher.stop()
        log.log("程序退出")
        self.root.destroy()


def main() -> None:
    log.clean_old(14)
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
