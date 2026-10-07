"""后台调度：按间隔跑 run_once，并按间隔检查「无内容」提示。

在 daemon 线程里跑，界面通过 start()/stop() 控制，状态通过回调上报。
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from .core import config, log
from . import runner


class Scheduler:
    """定时执行一轮抓取推送。

    参数：
        cfg       配置字典（内存里的当前值，界面改完直接更新即可生效）
        on_tick   每轮结束后回调 (result: RunResult)
        on_state  状态变化回调 (text: str)，用于界面显示「下次运行时间」
    """

    def __init__(self, cfg: dict, *, on_tick=None, on_state=None):
        self.cfg = cfg
        self.on_tick = on_tick or (lambda *a: None)
        self.on_state = on_state or (lambda *a: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._next_run: dt.datetime | None = None
        self._running = False

    # ---------------------------------------------------------------- 控制
    def start(self) -> None:
        if self.running:
            log.log("· 调度器已在运行")
            return
        self._stop.clear()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.log(f"▶ 已启动：每 {self.cfg.get('intervalMinutes', 60)} 分钟推送一轮")

    def stop(self) -> None:
        if not self.running:
            return
        self._stop.set()
        self._running = False
        self._next_run = None
        log.log("■ 已停止定时推送")
        self.on_state("已停止")

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_run(self) -> dt.datetime | None:
        return self._next_run

    def run_now(self) -> None:
        """手动触发一轮（在独立线程里，不阻塞界面）。"""
        def _job():
            self.on_state("正在执行…")
            try:
                res = runner.run_once(self.cfg)
                if res.sent == 0 and res.skipped_reason and not res.error:
                    runner.notify_empty(self.cfg, res.skipped_reason)
                self.on_tick(res)
            except Exception as exc:
                log.log(f"· 执行出错：{type(exc).__name__} {exc}")
            self._schedule_next()
        threading.Thread(target=_job, daemon=True).start()

    # ---------------------------------------------------------------- 主循环
    def _loop(self) -> None:
        # 启动后先立刻跑一轮，让用户马上看到效果
        self._run_round()
        while not self._stop.is_set():
            # 每 5 秒醒一次，便于随时改间隔或停止
            for _ in range(int(self._interval_seconds() / 5)):
                if self._stop.is_set():
                    return
                time.sleep(5)
            self._run_round()

    def _interval_seconds(self) -> int:
        return max(60, int(self.cfg.get("intervalMinutes", 60)) * 60)

    def _run_round(self) -> None:
        if self._stop.is_set():
            return
        self.on_state("正在执行…")
        try:
            res = runner.run_once(self.cfg)
            if res.sent == 0 and res.skipped_reason and not res.error:
                runner.notify_empty(self.cfg, res.skipped_reason)
            self.on_tick(res)
        except Exception as exc:
            log.log(f"· 本轮出错：{type(exc).__name__} {exc}")
        self._schedule_next()

    def _schedule_next(self) -> None:
        if self._stop.is_set() or not self._running:
            self._next_run = None
            self.on_state("已停止")
            return
        nxt = dt.datetime.now() + dt.timedelta(seconds=self._interval_seconds())
        self._next_run = nxt
        self.on_state(f"运行中 · 下次 {nxt:%H:%M}")
