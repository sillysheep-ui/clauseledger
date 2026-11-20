"""STARA 式"批量标注 + 聚合"的最小复刻。

STARA（Stanford RegLab）与我们的差别不在检索器，在**读法**：
它不是"选一段最好的"，而是把候选条款逐条过一遍，标出哪些真的支持结论，再汇总。

这个脚本只测这一件事，不动生产代码：
对 answer-eval 的 32 道题，取 top-8 候选条款 → 让模型逐条判"原文里是否直接
包含回答所需的具体内容" → 把标过的条款正文拼起来 → 用同一套数字事实核对覆盖率。

对照：我们现在的卡 34.4%（数字事实），候选并集 35.9%。
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from tools.eval_answer import facts, load_official, norm

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("CLAUSELEDGER_STARA_MODEL", "deepseek-chat")
CACHE = Path("data/cache/stara_probe.json")
K = 8

SYSTEM = """你是法律检索的核对员。给你一个问题，以及若干条候选条款原文。
你的任务不是回答问题，而是判断：**哪几条原文里直接写着回答问题所需要的内容**
（具体数字、期限、条件、办理方式）。

规则：
- 只标"原文里有"的，不标"相关但没写"的。
- 一条都不像就返回空数组，不要勉强。
- 输出 JSON：{"support": [编号, ...], "why": "一句话"}。"""

USER = """问题：{q}

候选条款：
{items}

只输出 JSON。"""


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not k and Path(".env").exists():
        for line in Path(".env").read_text("utf-8", "ignore").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        raise RuntimeError("没有 DEEPSEEK_API_KEY")
    return k


def ask(q: str, items: list[dict]) -> list[int]:
    body_text = "\n".join(
        f"[{i}] {it['citation']}\n{it['text'][:400]}" for i, it in enumerate(items, 1))
    body = {"model": MODEL, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER.format(q=q, items=body_text)}],
            "max_tokens": 300, "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_key()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    got = json.loads(data["choices"][0]["message"]["content"])
    out = []
    for x in got.get("support") or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(items):
            out.append(i - 1)
    return out


def main() -> None:
    from app.retrieval.search import search_result

    cache: dict = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text("utf-8"))

    rows = [r for r in load_official() if facts(r["a"])]
    total = got_union = got_stara = 0
    picked = 0
    detail = []
    for r in rows:
        fs = facts(r["a"])
        ans = search_result(r["q"], k=K)
        items = [{"citation": h.clause.citation, "text": (h.child or h.clause.text),
                  "doc_no": h.clause.doc_no} for h in ans.hits]
        key = r["id"]
        if key in cache:
            sel = cache[key]
        else:
            try:
                sel = ask(r["q"], items)
            except Exception as e:
                print(f"  ! {key}: {type(e).__name__} {e}")
                sel = []
            cache[key] = sel
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")
        blob = norm(" ".join(items[i]["text"] for i in sel))
        union = norm(" ".join(it["text"] for it in items))
        total += len(fs)
        got_union += sum(1 for f in fs if f in union)
        got_stara += sum(1 for f in fs if f in blob)
        picked += len(sel)
        detail.append({"id": key, "sel": [items[i]["citation"] for i in sel],
                       "hit": [f for f in fs if f in blob]})

    print(f"题 {len(rows)}｜事实 {total}")
    print(f"候选并集覆盖 {got_union/total:.1%}（{got_union}/{total}）")
    print(f"STARA 式聚合覆盖 {got_stara/total:.1%}（{got_stara}/{total}）")
    print(f"平均标注条款 {picked/len(rows):.1f} 条/题")
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    (Path("data/reports") / "stara-probe.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
