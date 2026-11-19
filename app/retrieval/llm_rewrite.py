"""用大模型把口语词翻译成政策文件里会用的词（可选层，默认关闭）。

为什么要这一层：手写规则表只能覆盖有限的说法（21 组只改好 4/64 题），而"口语问题
换成书面问法"的上界实验显示有 +17pp 的空间——剩下的靠枚举补不上。

**v2 的关键改动：不重写整句，只翻译"语料里根本不存在的词"。**

v1 让模型"把问题改写成政策用语"，实测确实能在排序层拿到 +12.5pp（26.6%→39.1%），
但它会**另起话题**：`外卖骑手职业伤害预防费怎么用` 被改写成「新就业形态从业人员 /
职业伤害保障」——词都对，指向的却是另一份政策，排名从 11 掉到 42。64 题里 13 题
因此变差。

v2 把任务收窄成"术语翻译"：先用 df 找出原句里语料中不存在的词，只把这些词交给
模型，并要求给出一一对应关系。模型不再有机会重新理解需求，只能回答"这个词在政策
里怎么说"。

四道守卫（缺一条都会出事）：

1. **df 闸**：译法必须在语料里出现过；`from` 必须是原句里的词，且确实在"不认识"
   的清单里。模型编的词加了只会稀释信号。
2. **只加不减**：原句照旧参与分词与覆盖率，译法只进检索。
3. **冲突闸**：原句有否定（不/未/没/没有/不得）时，丢弃"补缴/享受/可以"这类正向动作。
4. **降级**：无 key、超时、JSON 坏、返回空 —— 一律退回规则表（`rewrite`）。

缓存到 `data/cache/rewrite_llm.json`（键含 prompt 版本）：同一句话不重复计费。

开关：`CLAUSELEDGER_REWRITE_BACKEND=llm`（默认 `rules`）；
`CLAUSELEDGER_REWRITE_PROMPT=v1` 可回到整句改写，用于对照。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("CLAUSELEDGER_REWRITE_MODEL", "deepseek-chat")
CACHE = Path(os.environ.get("CLAUSELEDGER_REWRITE_CACHE", "data/cache/rewrite_llm.json"))
TIMEOUT = float(os.environ.get("CLAUSELEDGER_REWRITE_TIMEOUT", "8"))
PROMPT = os.environ.get("CLAUSELEDGER_REWRITE_PROMPT", "v2").lower()
MAX_TERMS = 6
MAX_UNKNOWN = 4
NEGATION = re.compile(r"[不未没]|没有|不得|禁止")
NUMERIC = re.compile(r"\d")
POSITIVE = re.compile(r"补缴|补交|享受|可以|能够|申请")

SYSTEM_V2 = """你是术语翻译器，不做需求理解，也不改写句子。

给你一个用户问题，以及其中几个"政策文件里根本不出现的词"。只把这些词翻译成上海人社政策文件里会用的说法。

要求：
1. 只翻译给出的词，不要翻译别的词，不要补充用户没提到的政策。
2. 每个词最多给 2 个译法；译法必须是政策文件里的名词或动宾短语（如"失业保险金""灵活就业""解除劳动合同"）。
3. 不许改变用户说的范围：不要加年份、数字、金额、条件、对象（如"外地户口""连续缴纳""满一年"）。
4. 译不出来就给空数组，不要硬猜。

只输出 JSON，形如：{"replace": {"生娃": ["生育"], "干零活": ["灵活就业"]}}"""

SYSTEM_V3 = """你是术语翻译器。任务是**把口语词换成政策文件里的说法**，不是理解需求，也不是改写句子。

给你一个用户问题。找出其中"老百姓这么说、但政策文件不这么说"的词，给出政策文件里的对应说法。

要求：
1. `from` 必须是原问题里**一字不差的连续片段**（能直接在原句里找到）。
2. 只翻译"词"，不翻译"概念"：`生娃`→`生育` 可以；`骑手`→`新就业形态从业人员` 不行（这是在换话题，不是在翻译词）。
3. 译法必须是政策文件里的名词或动宾短语；每项最多 2 个。
4. 不要增加用户没说的范围：不加年份、数字、金额、条件、对象。
5. 用户原句是否定句（"没交过""能不能不"）时，不要给出"补缴/享受/可以"这类正向动作。
6. 找不到合适的说法就不要输出这一项。

只输出 JSON：{"replace": {"生娃": ["生育"], "被开除": ["解除劳动合同"]}}"""

SYSTEM_V1 = """你是政策检索的查询改写器。把用户的口语问题改写成几个"上海人社政策文件里会出现的名词短语"。

规则：
1. 只输出名词短语，最多 5 个；不要输出整句、不要解释。
2. 不要添加年份、数字、金额、条件（如"连续缴纳""满一年""外地户口"）。
3. 用户原句里有"不/未/没/没有"时，不要输出与其相反的意思。
4. 只输出 JSON：{"terms": ["..."]}。没有把握就输出 {"terms": []}。"""


class Rewrite(NamedTuple):
    terms: list[str]
    hits: list[dict]
    backend: str = "llm"


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "")
    if k:
        return k
    env = Path(".env")
    if not env.exists():
        return ""
    for line in env.read_text().splitlines():
        if line.strip().startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def available() -> bool:
    return bool(_key())


_texts: list[str] | None = None
_df_cache: dict[str, int] = {}


def _all_texts() -> list[str]:
    global _texts
    if _texts is None:
        path = Path(os.environ.get("CLAUSELEDGER_INDEX", "data/index")) / "clauses.jsonl"
        _texts = [json.loads(line).get("text", "") for line in path.read_text().splitlines()]
    return _texts


def _df(term: str) -> int:
    """这个词在语料里出现过多少条条款。0 次说明政策文件根本不用它。"""
    if term not in _df_cache:
        _df_cache[term] = sum(1 for t in _all_texts() if term in t)
    return _df_cache[term]


def _unknown(question: str) -> list[str]:
    """找出原句里"语料不认识"的词——只翻译这些，避免模型另起话题。"""
    from app.retrieval.index import tokenize
    from app.retrieval.search import QUESTION_WORDS

    seen: list[str] = []
    for t in tokenize(question):
        if len(t) < 2 or t in QUESTION_WORDS or NUMERIC.search(t) or t in seen:
            continue
        if _df(t) <= 1:
            seen.append(t)
    # 长的优先：句子里信息量最大的一般是长词
    seen.sort(key=len, reverse=True)
    return seen[:MAX_UNKNOWN]


def _load_cache() -> dict:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, CACHE)


def _post(system: str, user: str, max_tokens: int = 160) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_key()}"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    return json.loads(data["choices"][0]["message"]["content"])


def _guard_terms(question: str, terms: list[str], unknown: list[str] | None = None) -> list[str]:
    """通用守卫：去重、去数字、去原句已有、去否定冲突、df=0 丢弃。"""
    out: list[str] = []
    neg = bool(NEGATION.search(question))
    for t in terms:
        t = str(t).strip()
        if len(t) < 2 or len(t) > 12 or NUMERIC.search(t) or t in question:
            continue
        if unknown is not None and t in unknown:
            continue
        if neg and POSITIVE.search(t):
            continue
        if _df(t) <= 0 or t in out:
            continue
        out.append(t)
        if len(out) >= MAX_TERMS:
            break
    return out


def _ask_v2(question: str, unknown: list[str]) -> list[tuple[str, list[str]]]:
    obj = _post(SYSTEM_V2, f"问题：{question}\n要翻译的词：{'、'.join(unknown)}")
    mapping = obj.get("replace") or {}
    pairs: list[tuple[str, list[str]]] = []
    for src in unknown:
        dst = mapping.get(src) or []
        if isinstance(dst, str):
            dst = [dst]
        clean = _guard_terms(question, dst, unknown)
        if clean:
            pairs.append((src, clean[:2]))
    return pairs


def _ask_v3(question: str) -> list[tuple[str, list[str]]]:
    """整句交给模型找"口语词"，但要求 from 是原句里的连续片段。

    不同于 v2 的"只翻译语料不认识的词"：像"开除"这种词语料里其实出现过
    （处分语境），df 不为 0，v2 会漏掉；v3 让模型自己判断哪些是口语说法，
    再用"必须是原句子串"这条硬约束防止它整句重写。
    """
    obj = _post(SYSTEM_V3, question)
    mapping = obj.get("replace") or {}
    pairs: list[tuple[str, list[str]]] = []
    for src, dst in mapping.items():
        src = str(src).strip()
        if len(src) < 2 or src not in question:      # 必须是原句子串
            continue
        if isinstance(dst, str):
            dst = [dst]
        clean = _guard_terms(question, [str(x) for x in dst])
        if clean:
            pairs.append((src, clean[:2]))
    return pairs[:MAX_TERMS]


def _ask_v1(question: str) -> list[str]:
    obj = _post(SYSTEM_V1, question, max_tokens=120)
    terms = obj.get("terms") or []
    return _guard_terms(question, terms)


def apply(question: str) -> Rewrite:
    """返回扩展词。任何异常都返回空——调用方会退回规则表。"""
    if not question or not available():
        return Rewrite([], [], backend="unavailable")
    unknown = _unknown(question)
    cache = _load_cache()
    key = hashlib.sha256(f"{PROMPT}|{question}|{'|'.join(unknown)}".encode()).hexdigest()[:16]
    if key in cache:
        entry = cache[key]
        terms, pairs = entry["terms"], entry.get("pairs") or []
        hits = [{"from": src, "to": dst, "cached": True} for src, dst in pairs] or \
               [{"from": question, "to": terms, "cached": True}]
        return Rewrite(terms, hits, backend="llm")
    t0 = time.time()
    try:
        if PROMPT == "v3":
            pairs = _ask_v3(question)
            terms = [t for _, dst in pairs for t in dst]
        elif PROMPT == "v1":
            terms, pairs = _ask_v1(question), []
        elif not unknown:
            # 原句里每个实词语料都认识——没有要翻译的，不花这次调用
            return Rewrite([], [{"skip": "no_unknown"}], backend="llm")
        else:
            pairs = _ask_v2(question, unknown)
            # 锚定：from 必须是"要翻译清单"里的词。模型偶尔会顺手多给几条
            # 映射（比如把原句里已经能匹配的词也改写），一律丢——那正是 v1
            # 另起话题的来源。
            pairs = [(s_, d) for s_, d in pairs if s_ in unknown]
            terms = [t for _, dst in pairs for t in dst]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
        return Rewrite([], [{"error": type(e).__name__}], backend="llm-failed")
    cache[key] = {"terms": terms, "pairs": pairs}
    _save_cache(cache)
    ms = int((time.time() - t0) * 1000)
    hits = [{"from": s, "to": d, "ms": ms} for s, d in pairs] or [{"from": question, "to": terms, "ms": ms}]
    return Rewrite(terms, hits, backend="llm")
