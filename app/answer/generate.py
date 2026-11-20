"""标准 RAG 的"生成"这一段：把证据交给模型，让它写成答案。

三条纪律（都写进 prompt，并且在 verify 里再查一遍）：
1. **闭卷**：只能用证据里的内容，没有就说没有；
2. **逐句引用**：每句必须带 [E#]，编号由我们分配，模型不能自己造；
3. **不改数字、不改情态**：可以/应当、不超过/不低于，照抄。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from .context import Evidence, render

API = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("CLAUSELEDGER_LLM", "deepseek-chat")
CACHE = Path(os.environ.get("CLAUSELEDGER_LLM_CACHE", "data/cache/generate.json"))
TIMEOUT = float(os.environ.get("CLAUSELEDGER_LLM_TIMEOUT", "60"))

SYSTEM = """你是上海人社政策的解读助手。你只能依据【证据】回答，不得使用证据之外的任何知识。

规则：
1. 每条结论后必须标注证据编号，格式 [E1]；一句可以引多条。
2. 数字、期限、分数只能照抄证据，不得改写、换算、四舍五入。
3. 不得改变情态动词：证据写"可以"就不能写"应当"，写"不超过"就不能写"不低于"。
4. 证据不足以回答时，answer 里只写一句"资料中没有足够依据"，并在 not_covered 说明缺什么。
5. 每句不超过 80 字；不要 Markdown，不要复述整段原文。

只输出 JSON，不要解释：
{"answer":[{"text":"...","cites":["E1"]}],"not_covered":["..."],"confidence":"high|medium|low"}"""


@dataclass
class Draft:
    sentences: list[dict] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)
    confidence: str = "medium"
    model: str = MODEL
    cached: bool = False
    usage: dict = field(default_factory=dict)
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def text(self) -> str:
        return "".join(s.get("text", "") for s in self.sentences)


def _key(question: str, ev: list[Evidence]) -> str:
    h = hashlib.sha256()
    h.update(question.encode())
    for e in ev:
        h.update((e.eid + e.clause_id + e.text[:120]).encode())
    return h.hexdigest()[:24]


def _cache_get(k: str) -> dict | None:
    if not CACHE.exists():
        return None
    try:
        return json.loads(CACHE.read_text("utf-8")).get(k)
    except Exception:  # noqa: BLE001
        return None


def _cache_put(k: str, v: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if CACHE.exists():
        try:
            data = json.loads(CACHE.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    data[k] = v
    CACHE.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def api_key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    for p in (Path(".env"), Path("../.env")):
        if p.exists():
            for line in p.read_text("utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def available() -> bool:
    return bool(api_key())


def build_messages(question: str, ev: list[Evidence], rules: str | None = None) -> list[dict]:
    body = [f"【问题】{question}", "", "【证据】", render(ev)]
    if rules:
        body += ["", "【规则计算结果】（这是程序算的，只能引用，不得修改）", rules]
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "\n".join(body)}]


def parse(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1] if "```" in t[2:] else t.strip("`")
        t = t.lstrip("json").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None


def generate(question: str, ev: list[Evidence], *, rules: str | None = None,
             cache: bool = True) -> Draft | None:
    """返回 None 表示"没生成"（没 key / 调用失败 / 解析失败），调用方应退回截取式卡片。"""
    if not ev or not available():
        return None
    k = _key(question, ev) + ("" if not rules else "#" + str(len(rules)))
    if cache:
        hit = _cache_get(k)
        if hit:
            return Draft(**hit, cached=True)

    try:
        r = httpx.post(API, timeout=TIMEOUT,
                       headers={"Authorization": f"Bearer {api_key()}"},
                       json={"model": MODEL, "temperature": 0.2, "max_tokens": 900,
                             "response_format": {"type": "json_object"},
                             "messages": build_messages(question, ev, rules)})
        r.raise_for_status()
        j = r.json()
        content = j["choices"][0]["message"]["content"]
        usage = j.get("usage") or {}
    except Exception:  # noqa: BLE001 - 网络/限流/超时都退回截取式
        return None

    data = parse(content)
    if not data or "answer" not in data:
        return None
    d = Draft(sentences=[s for s in data.get("answer", []) if isinstance(s, dict) and s.get("text")],
              not_covered=[str(x) for x in data.get("not_covered", []) if str(x).strip()],
              confidence=str(data.get("confidence", "medium")),
              usage=usage, raw=content)
    if cache:
        _cache_put(k, {kk: vv for kk, vv in d.to_dict().items() if kk != "cached"})
    return d
