"""信源定义。

只保留能直连、且有稳定列表结构的站点。每个信源声明：
    name    中文名（展示用）
    url     列表页
    kind    解析器类型（rudaw / shafaq / rss / generic）
    weight  重要性权重（同一事件多源印证时，权重高的优先）
"""
from __future__ import annotations

SOURCES: dict[str, dict] = {
    "rudaw": {
        "name": "鲁道网",
        "url": "https://www.rudaw.net/english/middleeast",
        "kind": "rudaw",
        "weight": 0,
    },
    "shafaq": {
        "name": "沙法克新闻",
        "url": "https://shafaq.com/en/World",
        "kind": "shafaq",
        "weight": 0,
    },
    "iswnews": {
        "name": "ISW News",
        "url": "https://english.iswnews.com/feed/",
        "kind": "rss",
        "weight": 1,
    },
    "almonitor": {
        "name": "Al-Monitor",
        "url": "https://www.al-monitor.com/rss",
        "kind": "rss",
        "weight": 1,
    },
    "presstv": {
        "name": "Press TV",
        "url": "https://www.presstv.ir/rss.xml",
        "kind": "rss",
        "weight": 1,
    },
    "alsumaria": {
        "name": "苏美利亚",
        "url": "https://www.alsumaria.tv/",
        "kind": "generic",
        "weight": 2,
    },
}

# 重要性关键词加权（命中即加分，用于 minScore 门槛）
# 注意：这里是「子串包含」匹配，所以只放不容易误伤的词
# （例如不放 "war"，因为会命中 warning / toward / forward）。
KEYWORDS: dict[str, int] = {
    # 军事冲突
    "airstrike": 3, "air strike": 3, "missile": 3, "drone attack": 3, "shelling": 3,
    "offensive": 2, "clashes": 2, "fighting": 2, "bombard": 3, "artillery": 2,
    "attack": 3, "strike": 2, "struck": 3, "blast": 3, "explosion": 3, "explode": 3,
    "shot down": 3, "intercept": 2, "militant": 2, "terror": 2,
    "空袭": 3, "导弹": 3, "袭击": 2, "交火": 2, "激战": 2, "爆炸": 3,
    # 伤亡
    "killed": 3, "dead": 3, "casualt": 3, "wounded": 2, "injured": 2, "death toll": 3,
    "死亡": 3, "伤亡": 3, "受伤": 2,
    # 国家层面
    "state of emergency": 3, "martial law": 3, "mobiliz": 2, "ultimatum": 2,
    "sever": 2, "expel": 2, "sanction": 3, "ceasefire": 3, "truce": 2,
    "blockade": 3, "invasion": 3, "invade": 3,
    "hostage": 3, "deploy": 2, "escalat": 2, "threat": 2, "seiz": 2,
    "紧急状态": 3, "制裁": 3, "停火": 3, "断交": 3, "封锁": 3, "入侵": 3,
    # 大国介入
    "pentagon": 2, "white house": 2, "irgc": 2, "centcom": 3, "idf": 2,
    "tehran": 1, "washington": 1, "moscow": 1, "beijing": 2,
    # 地区主体
    "houthi": 2, "hezbollah": 2, "hamas": 2, "gaza": 1,
    # 通道与能源
    "strait of hormuz": 3, "bab al-mandab": 3, "red sea": 2, "suez": 2,
    "tanker": 3, "shipping": 2, "oil price": 2, "brent": 2, "crude": 2,
    "nuclear": 2, "suspend": 2, "evacuat": 2,
    "霍尔木兹": 3, "曼德海峡": 3, "红海": 2, "油轮": 3, "油价": 2,
    # 涉华
    "china": 2, "chinese": 2, "中国": 2, "中方": 2,
    # 政权变动
    "coup": 3, "resign": 2, "assassinat": 3, "election": 1, "parliament": 1,
    "riot": 2, "protest": 1,
    "政变": 3, "辞职": 2, "遇刺": 3,
    # 降权项（软新闻 / 评论解读）
    "sport": -3, "football": -3, "league": -3, "match": -2, "festival": -2,
    "exhibition": -3, "tourism": -2, "concert": -3, "celebrity": -3,
    "analysis": -2, "opinion": -2, "explainer": -2, "in pictures": -2,
    "体育": -3, "足球": -3, "展会": -3, "旅游": -2,
}

# 排除项：命中即整条丢弃（连打分都不必）
EXCLUDE = (
    "sport", "football", "soccer", "basketball", "olympic", "league",
    "podcast", "horoscope", "recipe", "fashion", "celebrity",
    "体育", "足球", "篮球", "奥运",
)

# 中文分类标签，用于给推送加个领域标记
CN_TAGS: list[tuple[tuple[str, ...], str]] = [
    (("strait of hormuz", "bab al-mandab", "red sea", "tanker", "shipping", "霍尔木兹", "红海", "油轮"), "航道安全"),
    (("airstrike", "air strike", "missile", "drone", "shelling", "空袭", "导弹", "袭击"), "军事冲突"),
    (("killed", "dead", "casualt", "wounded", "death toll", "死亡", "伤亡"), "人员伤亡"),
    (("sanction", "制裁"), "制裁动态"),
    (("ceasefire", "truce", "negotiat", "talks", "停火", "谈判"), "停火谈判"),
    (("china", "chinese", "中国", "中方"), "涉华动态"),
    (("oil", "brent", "crude", "energy", "油价", "能源"), "能源市场"),
    (("coup", "resign", "election", "政变", "辞职", "选举"), "政权动态"),
]


def cn_tag(item: dict) -> str:
    blob = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    for keys, label in CN_TAGS:
        if any(k in blob for k in keys):
            return label
    return "中东局势"


def score(item: dict) -> int:
    blob = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return sum(w for kw, w in KEYWORDS.items() if kw in blob)


def is_excluded(item: dict) -> bool:
    blob = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return any(k in blob for k in EXCLUDE)
