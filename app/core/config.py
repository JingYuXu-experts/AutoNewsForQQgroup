"""中东要闻推送 · 配置读写。

配置默认存到 %APPDATA%\\中东要闻推送\\config.json，与 exe 位置无关，
这样程序换目录、升级版本，用户填过的密钥都还在。

用户不需要手动编辑这个文件——界面里填好点保存即可。
"""
from __future__ import annotations

import base64
import json
import os
import sys

APP_NAME = "中东要闻推送"

# 密钥类字段：落盘时做一层编码，避免一眼看到明文（本机文件，够用）
_SECRET_KEYS = ("appSecret", "deepseekApiKey")

DEFAULTS: dict = {
    # ---- 必填：QQ 机器人 ----
    "appId": "",
    "appSecret": "",
    "groupOpenid": "",

    # ---- 必填：翻译用的大模型 ----
    "llmProvider": "deepseek",              # deepseek
    "deepseekApiKey": "",
    "deepseekModel": "deepseek-chat",
    "deepseekEndpoint": "https://api.deepseek.com/chat/completions",

    # ---- 抓取与筛选 ----
    "intervalMinutes": 60,                  # 多久跑一轮
    "localDigestMax": 1,                    # 每轮最多推几条
    "minScore": 3,                          # 重要性门槛，越高越挑剔
    "freshHours": 2,                        # 优先窗口：只用 N 小时以内的新闻
    "todayOnly": True,                      # 放宽时只到「当日」，跨日旧闻不要
    "maxAgeHours": 24,                      # 绝对上限
    "probeLimit": 8,                        # 回源确认发布时间的条数上限
    "dedupeHours": 24,                      # 同事件去重窗口
    "dailyLimit": 30,                       # 每日推送上限
    "emptyNotice": True,                    # 没内容可推时，明确说明「暂时没有最新新闻」
    "emptyNoticeHours": 6,                  # 该提示的最短间隔

    # ---- 媒体 ----
    "withMedia": True,
    "maxMedia": 2,
    "maxImageMB": 4,
    "maxVideoMB": 20,

    # ---- 内容源开关 ----
    "sources": {
        "rudaw": True,
        "shafaq": True,
        "iswnews": True,
        "almonitor": True,
        "presstv": True,
        "alsumaria": False,
    },

    # ---- 运行 ----
    "autoStart": False,                     # 开机自启
    "startMinimized": True,                 # 启动后最小化到托盘/任务栏
    "timeoutSeconds": 20,
    "maxChars": 1800,                       # 单条消息字数上限
}


def config_dir() -> str:
    """配置目录：%APPDATA%\\中东要闻推送（打包成 exe 后同样适用）。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def logs_dir() -> str:
    d = os.path.join(config_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def reports_dir() -> str:
    """本程序自己生成的简报存放目录。"""
    d = os.path.join(config_dir(), "reports")
    os.makedirs(d, exist_ok=True)
    return d


def media_dir() -> str:
    d = os.path.join(reports_dir(), "media")
    os.makedirs(d, exist_ok=True)
    return d


def state_path() -> str:
    return os.path.join(config_dir(), "state.json")


# ---------------------------------------------------------------- 编码 / 解码
def _encode(value: str) -> str:
    if not value:
        return ""
    return "enc:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode(value: str) -> str:
    if not value:
        return ""
    if isinstance(value, str) and value.startswith("enc:"):
        try:
            return base64.b64decode(value[4:]).decode("utf-8")
        except Exception:
            return ""
    return value


# ---------------------------------------------------------------- 读写
def load() -> dict:
    """读取配置；文件不存在或损坏时返回默认值。"""
    cfg = json.loads(json.dumps(DEFAULTS))       # 深拷贝
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                disk = json.load(f)
            for k, v in (disk or {}).items():
                if k in _SECRET_KEYS:
                    cfg[k] = _decode(v)
                elif k == "sources" and isinstance(v, dict):
                    cfg["sources"].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass                                  # 坏了就用默认值，不让程序起不来
    return cfg


def save(cfg: dict) -> str:
    """写盘（密钥字段编码后保存）。返回实际写入路径。"""
    data = dict(cfg)
    for k in _SECRET_KEYS:
        if k in data:
            data[k] = _encode(str(data.get(k) or ""))
    path = config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def is_ready(cfg: dict) -> tuple[bool, list[str]]:
    """检查必填项，返回 (是否就绪, 缺失项的中文说明)。"""
    missing = []
    if not str(cfg.get("appId") or "").strip():
        missing.append("QQ 机器人 AppID")
    if not str(cfg.get("appSecret") or "").strip():
        missing.append("QQ 机器人 AppSecret")
    if not str(cfg.get("groupOpenid") or "").strip():
        missing.append("群 ID（需点「获取群 ID」抓取）")
    if not str(cfg.get("deepseekApiKey") or "").strip():
        missing.append("DeepSeek API Key")
    return (not missing), missing


def runtime_dir() -> str:
    """exe 自身所在目录（用于定位随包资源）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """定位随程序分发的资源（图标等）。

    PyInstaller 单文件模式会把资源解到 sys._MEIPASS，打包后必须去那里找；
    源码运行时则相对项目根目录找。
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    return os.path.join(base, *parts)
