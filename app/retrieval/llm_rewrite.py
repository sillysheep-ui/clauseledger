"""用大模型做口语→政策词的改写（可选层，默认关闭）。

为什么要这一层：手写规则表能覆盖的说法有限（21 组只能改好 4/64 题），而"口语问题
换回书面问法"的上界实验显示有 +17pp 的空间。剩下的靠枚举补不上，只能让模型来做。

但模型改写非常容易把检索带偏，所以这里有四道守卫，缺一条都会出事：

1. **df 闸**：扩展词必须真的在语料里出现过。模型很爱编"育儿假""生育津贴"这类
   我们库里没有的词，加上去只会稀释信号。
2. **只加不减**：原句照旧参与分词与覆盖率，扩展词只进检索。
3. **冲突闸**：原句有否定（不/未/没/没有/不得）时，丢弃可能翻转语义的扩展词。
4. **降级**：无 key、超时、JSON 坏、返回空 —— 一律退回规则表（`rewrite`）。

结果缓存到 `data/cache/rewrite_llm.json`：同一句话不重复计费，评测可复现。

开关：`CLAUSELEDGER_REWRITE=llm`（默认 `rules`）；模型可用性另由 `available()` 判断。
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
MAX_TERMS = 5
NEGATION = re.compile(r"[不未没]|没有|不得|禁止")
NUMERIC = re.compile(r"\d")

SYSTEM = """你是政策检索的查询改写器。把用户的口语问题改写成几个"上海人社政策文件里会出现的名词短语"。

规则：
1. 只输出名词短语，最多 5 个；不要输出整句、不要解释。
2. 不要添加年份、数字、金额、条件（如"连续缴纳""满一年""外地户口"）。
3. 用户原句里有"不/未/没/没有"时，不要输出与其相反的意思。
4. 只输出 JSON：{"terms": ["..."]}。没有把握就输出 {"terms": []}。

示例：
"失业金怎么领" → {"terms": ["失业保险金", "申领"]}
"生完孩子能休多久" → {"terms": ["生育", "产假"]}
"被公司开除了有补偿吗" → {"terms": ["解除劳动合同", "经济补偿"]}
"我今年62了还能补交社保吗" → {"terms": ["社会保险", "补缴"]}"""


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
        _texts = [json.loads(l).get("text", "") for l in path.read_text().splitlines()]
    return _texts


def _df(term: str) -> int:
    """扩展词在语料里出现过多少次。0 次直接丢——模型编的词加了只会变差。"""
    if term not in _df_cache:
        _df_cache[term] = sum(1 for t in _all_texts() if term in t)
    return _df_cache[term]


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


def _ask(question: str) -> list[str]:
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": question}],
        "temperature": 0,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_key()}"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    raw = data["choices"][0]["message"]["content"]
    obj = json.loads(raw)
    terms = obj.get("terms") or []
    return [str(t).strip() for t in terms if str(t).strip()][:MAX_TERMS]


def _guard(question: str, terms: list[str]) -> list[str]:
    """守卫：去重、去数字、去否定冲突、df=0 丢弃。"""
    out: list[str] = []
    neg = bool(NEGATION.search(question))
    for t in terms:
        if len(t) < 2 or len(t) > 12 or NUMERIC.search(t):
            continue
        if t in question or t in out:
            continue
        if neg and ("补" in t or "享受" in t or "可以" in t):
            # 原句是否定式（"没交过社保能补吗"），别扩出正向动作词
            continue
        if _df(t) <= 0:
            continue
        out.append(t)
        if len(out) >= MAX_TERMS:
            break
    return out


def apply(question: str) -> Rewrite:
    """返回扩展词。任何异常都返回空——调用方会退回规则表。"""
    if not question or not available():
        return Rewrite([], [], backend="unavailable")
    cache = _load_cache()
    key = hashlib.sha256(question.encode()).hexdigest()[:16]
    if key in cache:
        terms = cache[key]
        return Rewrite(terms, [{"from": question, "to": terms, "cached": True}], backend="llm")
    t0 = time.time()
    try:
        terms = _guard(question, _ask(question))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
        return Rewrite([], [{"error": type(e).__name__}], backend="llm-failed")
    cache[key] = terms
    _save_cache(cache)
    hits = [{"from": question, "to": terms, "ms": int((time.time() - t0) * 1000)}]
    return Rewrite(terms, hits, backend="llm")
