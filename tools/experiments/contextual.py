"""给每条条款生成"它在讲什么"的上下文（Anthropic Contextual Retrieval 的直译）。

为什么要单独写一个生成器，而不是复用 `llm_rewrite`：两者任务不同。
改写是"把用户的话翻译成政策词"，上下文是"给条款补它自己没写清楚的定位信息
（讲谁、管什么、在什么范围内）"。后者要读**整份文件**，前者只读一句问题。

三条纪律：

1. **只写定位，不写结论**。上下文里不许出现原文没有的数字、期限、条件；
   模型只能做"这句在讲哪件事、对谁生效"的转述。
2. **按文件批量**。一份文件一次调用，返回 {pid: 上下文}；6,064 个子块按
   5,983 条条款归并，574 次调用封顶（不是 6,064 次）。
3. **可缓存、可复算**。落 `data/cache/ctx.json`，键是 pid；重跑只补缺的。

用法：
    python -m tools.experiments.contextual --limit 3     # 冒烟
    python -m tools.experiments.contextual               # 全量
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("CLAUSELEDGER_CTX_MODEL", "deepseek-chat")
CACHE = Path(os.environ.get("CLAUSELEDGER_CTX_CACHE", "data/cache/ctx.json"))
TIMEOUT = float(os.environ.get("CLAUSELEDGER_CTX_TIMEOUT", "60"))
BATCH = int(os.environ.get("CLAUSELEDGER_CTX_BATCH", "30"))
WORKERS = int(os.environ.get("CLAUSELEDGER_CTX_WORKERS", "6"))

SYSTEM = """你在给一份中国地方政策文件的条款做"定位说明"，供检索使用。

对每一条条款，写一句 30-60 字的中文说明，讲清三件事里适用的部分：
- 这条在讲什么事（主题词，用文件里的说法）
- 对谁适用（人群/单位/情形）
- 在什么范围或时限内

硬约束：
1. 只能转述原文已有的信息，**不许**添加原文没有的数字、金额、期限、条件。
2. 不要复述条款编号，不要写"本条""该条"。
3. 用政策文件里的术语，不要用口语。
4. 输出 JSON：{"<pid>": "<说明>", ...}，覆盖我给你的每一个 pid。"""

USER = """文件：{doc_no}《{title}》

文件开头（用于理解背景）：
{head}

需要写说明的条款：
{items}

只输出 JSON。"""


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not k:
        env = Path(".env")
        if env.exists():
            for line in env.read_text("utf-8", "ignore").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not k:
        raise RuntimeError("没有 DEEPSEEK_API_KEY")
    return k


def _post(system: str, user: str, max_tokens: int = 8000) -> dict:
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


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text("utf-8"))
    except Exception:
        return {}


_LOCK = threading.Lock()


def save_cache(cache: dict) -> None:
    with _LOCK:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")
        os.replace(tmp, CACHE)


def docs() -> dict[str, dict]:
    """doc_no -> {title, head, clauses:[{pid, path, text}]}"""
    rows = [json.loads(l) for l in
            Path("data/index/clauses.jsonl").read_text("utf-8").splitlines() if l.strip()]
    out: dict[str, dict] = {}
    for pid, c in enumerate(rows):
        d = out.setdefault(c["doc_no"], {"title": c["title"], "head": "", "clauses": []})
        d["clauses"].append({"pid": pid, "path": " › ".join(c["path"][-3:]) or c["label"],
                             "text": (c["text"] or "").strip()[:220]})
        if not d["head"] and c["text"]:
            d["head"] = (c["text"] or "").strip()[:400]
    return out


def build_prompt(doc: dict, todo: list[dict]) -> str:
    items = "\n".join(f'[{c["pid"]}] {c["path"]}｜{c["text"]}' for c in todo)
    return USER.format(doc_no=doc.get("doc_no", ""), title=doc["title"][:80],
                       head=doc["head"], items=items)


def clean(v: str) -> str:
    v = re.sub(r"\s+", "", str(v or ""))
    return v[:80]


def one(doc_no: str, doc: dict, todo: list[dict], cache: dict) -> int:
    try:
        got = _post(SYSTEM, build_prompt(doc, todo))
    except Exception as e:  # 网络/超时/JSON 坏：留给下一轮补
        print(f"  ! {doc_no}: {type(e).__name__} {e}")
        return 0
    n = 0
    for c in todo:
        ctx = clean(got.get(str(c["pid"]), ""))
        if len(ctx) >= 8:
            cache[str(c["pid"])] = ctx
            n += 1
    save_cache(cache)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    cache = load_cache()
    all_docs = docs()
    jobs = []
    for doc_no, doc in all_docs.items():
        todo = [c for c in doc["clauses"] if str(c["pid"]) not in cache]
        # 分批：一次只要 30 条。整份文件一次要 100+ 条时，输出会被 max_tokens 截断，
        # JSON 解析失败——慢的不是模型，是失败的调用在等超时。
        for i in range(0, len(todo), BATCH):
            jobs.append((doc_no, doc, todo[i:i + BATCH]))
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"文件 {len(all_docs)}｜待处理 {len(jobs)} 份｜已缓存 {len(cache)} 条")
    t0 = time.time()
    total = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(one, d, doc, todo, cache) for d, doc, todo in jobs]
        for i, f in enumerate(futs, 1):
            total += f.result()
            if i % 20 == 0:
                print(f"  [{i}/{len(jobs)}] 新增 {total} 条｜{time.time()-t0:.0f}s")
    print(f"完成：新增 {total} 条，缓存共 {len(cache)} 条｜{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
