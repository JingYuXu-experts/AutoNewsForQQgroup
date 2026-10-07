"""DeepSeek 翻译与编辑。

两件事：
    compose() 把一条外文新闻写成中文简讯（新闻联播口播风格）
    review()  生成后复核：核对事实与数字、译名、语体、结构

只依赖标准库 requests/urllib，接口与 OpenAI 兼容。
"""
from __future__ import annotations

import json
import re

from ..core import log, net

DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

EDIT_PROMPT = """你是中央电视台《新闻联播》国际新闻板块的资深编辑。
依据下面这条外文报道素材，写一条中文国际新闻简讯。

写作要求：
1. 全部译成中文。外文人名、机构名首次出现写成「中文译名(原文)」，例如「叶海亚·萨雷亚(Yahya Saree)」；
   时间区分「当地时间」与「北京时间」；
2. 语体：正式、庄重、客观、第三人称，句子完整、短句为主；
   标题与正文必须符合中文语序，读起来自然，不得生硬直译；
3. 禁用网络用语（炸裂、离谱、翻车、吃瓜等），不用夸张形容词，不替任何一方下定性结论；
4. 【最重要】只使用素材里出现的事实，不得添加、推测、编造任何信息；素材没写的一律不写；
   数字、年份、日期必须与素材一致，素材没写年份就不得补写年份；
5. 素材不足以支撑某一段时，该段可省略，但不得编造；【点评】只写素材能支撑的克制判断，
   不得以中国官方或任何一方口吻表态，实在无话可写就写「目前事态仍在发展，后续影响有待观察」；
6. 引用单一或非主流信源时，写成「据XX报道称」；
7. 专有名词一律使用中国官方媒体的通用译名，不得自行音译生造。常用对照：
   霍尔木兹海峡(Strait of Hormuz)、曼德海峡(Bab al-Mandab)、红海(Red Sea)、阿曼(Oman)、
   胡塞武装(Houthis)、真主党(Hezbollah)、哈马斯(Hamas)、加沙(Gaza)、伊斯兰革命卫队(IRGC)、
   沙特阿拉伯、伊拉克、库尔德斯坦(Kurdistan)、叙利亚、黎巴嫩、也门、亚丁(Aden)、
   荷台达(Hodeidah)、萨那(Sanaa)、摩卡(Mokha)、塔伊兹(Taiz)、
   阿拉比亚电视台(Al Arabiya)、半岛电视台(Al Jazeera)、迈亚丁电视台(Al Mayadeen)、
   鲁道网(Rudaw)、沙法克新闻(Shafaq)、萨布林新闻(Sabereen)；
8. 严格按下面的格式输出，不要输出解释、不要加代码块：
【标题】不超过 25 字的客观陈述式标题
【导语】1-2 句，交代时间、地点、主体、事件
【背景】1-2 句，交代来龙去脉
【进展】1-3 句，交代最新情况与关键数字
【点评】1 句，克制、不下结论
9. 除上面五个标签外，不得出现【开场白】【结束语】等任何其他字段标签。

——素材开始——
媒体：{source}
发布时间：{date}
原文标题：{title}
原文摘要：{summary}
正文片段：{body}
——素材结束——"""

REVIEW_PROMPT = """你是中央电视台《新闻联播》国际新闻板块的审稿人。
下面是一条中文简讯初稿及其外文素材，请逐项审校并给出终稿。

【审校要点】
1. 事实核对：逐句比对素材，凡素材中未出现的事实、数字、年份、人名、机构名、引语一律删除；
   不得保留推测或评论性扩写，严禁编造；素材没写年份就不得补写年份；
   【点评】只能写素材能支撑的克制判断，不得以中国官方或任何一方口吻表态；
2. 译名与中文语序：外文人名、机构名首次出现写「中文译名(原文)」；使用中国官方媒体通用译名，
   不得自行音译生造；不得生硬直译；
3. 语体：正式、庄重、客观、第三人称，短句为主；不得出现网络用语与夸张形容词；
   不替任何一方下定性结论；引用单一或非主流信源时写「据XX报道称」；
4. 结构：【标题】不超过 25 字，【导语】【背景】【进展】【点评】四段齐全，合计 260-380 字；
   不得出现「素材未提供」等说明性文字，素材支撑不足的段落直接省略；
5. 涉港、涉澳、涉台表述须规范，涉中国主权与领土问题与中国官方立场保持一致。

【输出格式】严格按下面的顺序输出，不要输出解释、不要加代码块：
【审核】通过 或 已修订
【问题】一句话说明主要问题；如无问题写「无」
【标题】终稿标题
【导语】……
【背景】……
【进展】……
【点评】……
注意：除以上标签外不得出现其他字段标签。

——外文素材开始——
媒体：{source}
发布时间：{date}
原文标题：{title}
原文摘要：{summary}
——外文素材结束——

——待审初稿开始——
{draft}
——待审初稿结束——"""

# 模型偶发把思考过程 / JSON 一起吐出来，这些特征视为脏数据
JUNK_MARKERS = ('"role"', '"reasoning"', 'reasoning\\":', '{"', '"}',
                "\\n", "character count", "we need", "tokens")


class LLMError(RuntimeError):
    """大模型调用失败，message 可直接展示给用户。"""


# ---------------------------------------------------------------- 调用
def chat(api_key: str, prompt: str, cfg: dict, *, system: str = "",
         timeout: int = 90, json_mode: bool = False) -> str:
    """调用 DeepSeek，返回正文文本。"""
    if not api_key:
        raise LLMError("未填写 DeepSeek API Key")

    endpoint = cfg.get("deepseekEndpoint") or DEEPSEEK_ENDPOINT
    model = cfg.get("deepseekModel") or DEFAULT_MODEL
    messages = []
    messages.append({"role": "system", "content": system or
                     "你是中央电视台《新闻联播》国际新闻板块的资深编辑兼播音员。"
                     "只输出最终成稿，绝对不要输出思考过程、分析、解释或英文。"})
    messages.append({"role": "user", "content": prompt})

    payload = {"model": model, "messages": messages, "temperature": 0.3,
               "max_tokens": 1200, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        body = net.post_json(
            endpoint, payload,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            timeout=timeout, retries=2)
    except Exception as exc:
        raise LLMError(_hint(exc)) from exc

    if body.get("error"):
        err = body["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else ""
        if code in ("invalid_api_key", 401) or "authentication" in str(msg).lower():
            raise LLMError("DeepSeek API Key 无效，请到 platform.deepseek.com 重新生成。")
        if code == "insufficient_quota" or "balance" in str(msg).lower():
            raise LLMError("DeepSeek 账户余额不足，请充值后重试。")
        raise LLMError(f"DeepSeek 返回错误：{msg}")

    try:
        content = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"DeepSeek 返回格式异常：{str(body)[:180]}")
    return clean_output(content)


def _hint(exc: Exception) -> str:
    text = str(exc)
    if "timeout" in text.lower():
        return "调用 DeepSeek 超时：检查本机网络能否访问 api.deepseek.com。"
    if "getaddrinfo" in text or "Name or service" in text:
        return "无法连接 DeepSeek：请检查网络或代理。"
    return f"调用 DeepSeek 失败（{type(exc).__name__}）：{text[:150]}"


def clean_output(content: str) -> str:
    """剥掉被塞进 content 的 JSON / 转义换行。"""
    s = (content or "").strip()
    if not s:
        return ""
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    if '"role"' in s or '"reasoning"' in s or '"content"' in s:
        m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
        if m:
            try:
                s = json.loads('"' + m.group(1) + '"')
            except Exception:
                s = m.group(1)
        elif s.startswith("{"):
            try:
                obj = json.loads(s)
                s = obj.get("content", "") or "" if isinstance(obj, dict) else ""
            except Exception:
                return ""
        else:
            return ""
    return s.replace("\\n", "\n").replace("\\r", "").strip()


# ---------------------------------------------------------------- 质量闸
def is_junk(text: str) -> bool:
    if not text or not text.strip():
        return True
    low = text.lower()
    if any(m in low for m in JUNK_MARKERS):
        return True
    return net.cjk_ratio(text) < 0.45


def parse_brief(raw: str) -> dict:
    """从模型输出里抽出标题与四段正文。"""
    raw = re.sub(r"```[a-zA-Z]*", "", raw or "")
    blocks = re.split(r"【标题】", raw)
    best = None
    for blk in blocks[1:]:
        if re.search(r"【(导语|背景|进展|点评|正文)】", blk):
            best = blk
    if best is None and len(blocks) > 1:
        best = blocks[-1]
    text = best if best is not None else raw

    out = {"title": "", "sections": []}
    m = re.match(r"\s*(.+)", text)
    if m:
        head = m.group(1).split("\n")[0]
        head = re.split(r"【", head)[0]
        out["title"] = head.strip().strip("。\"'“”").strip()[:40]
    for key in ("导语", "背景", "进展", "点评"):
        mm = re.search(rf"【{key}】\s*(.+?)(?=【|$)", text, re.S)
        if mm:
            seg = re.sub(r"\s+", " ", mm.group(1)).strip()
            if seg:
                out["sections"].append((key, seg))
    if not out["sections"]:
        body = re.sub(r"【[^】]*】", " ", text)
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            out["sections"].append(("正文", body[:400]))
    return out


def passes_quality(brief: dict) -> tuple[bool, str]:
    """推送前最后一道闸：标题与正文必须是像样的中文成稿。"""
    title = (brief.get("title") or "").strip()
    secs = brief.get("sections") or []
    if not secs:
        return False, "没有正文段落"
    if not title or is_junk(title):
        return False, "标题不是合格的中文标题"
    body = " ".join(seg for _, seg in secs)
    if is_junk(body):
        return False, "正文含未翻译内容或模型思考残留"
    if len(re.sub(r"\s", "", body)) < 80:
        return False, "正文过短（可能没抓到有效信息）"
    return True, ""


# ---------------------------------------------------------------- 主流程
def compose(item: dict, src_name: str, api_key: str, cfg: dict) -> dict | None:
    """把一条外文新闻写成中文简讯。返回 {title, sections, method} 或 None。"""
    from ..sources import fetch

    body = item.get("summary") or ""
    article = fetch.fetch_article_text(item["url"], int(cfg.get("timeoutSeconds", 20)))
    if article and len(article) > len(body):
        body = article

    prompt = EDIT_PROMPT.format(
        source=src_name,
        date=item.get("pub_display") or "（素材未标注）",
        title=item.get("title", ""),
        summary=(item.get("summary") or "（无）")[:400],
        body=body[:1200] or "（无）",
    )
    try:
        raw = chat(api_key, prompt, cfg)
    except LLMError as exc:
        log.log(f"· 翻译编辑失败：{exc}")
        return None

    brief = parse_brief(raw)
    ok, why = passes_quality(brief)
    if not ok:
        log.log(f"· 译编结果未过质检（{why}）")
        return None
    return {**brief, "method": "DeepSeek"}


def review(item: dict, brief: dict, src_name: str, api_key: str,
           cfg: dict) -> dict:
    """生成后复核。返回 {"brief": 终稿, "verdict": ..., "note": ...}。

    复核不可用或解析失败时退回原稿，绝不因此漏推。
    """
    draft = "\n".join([f"【标题】{brief.get('title','')}"] +
                      [f"【{k}】{v}" for k, v in brief.get("sections", [])])
    prompt = REVIEW_PROMPT.format(
        source=src_name,
        date=item.get("pub_display") or "（素材未标注）",
        title=item.get("title", ""),
        summary=(item.get("summary") or "（无）")[:400],
        draft=draft,
    )
    try:
        raw = chat(api_key, prompt, cfg)
    except LLMError as exc:
        return {"brief": brief, "verdict": "未复核", "note": str(exc)}

    verdict, note = "已修订", ""
    m = re.search(r"【审核】\s*(通过|已修订)", raw)
    if m:
        verdict = m.group(1)
    m = re.search(r"【问题】\s*(.+?)(?=\n|【|$)", raw)
    if m:
        note = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
        if note in ("无", "无。", "没有问题"):
            note = ""

    fixed = parse_brief(raw)
    ok, why = passes_quality(fixed)
    if not ok:
        return {"brief": brief, "verdict": verdict,
                "note": (note + f"（终稿未过质检：{why}，保留初稿）").strip()}
    if not fixed["title"]:
        fixed["title"] = brief.get("title", "")
    return {"brief": fixed, "verdict": verdict, "note": note}
