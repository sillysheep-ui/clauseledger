"""全流程体检：一次跑完 S1→S8，并指出**错误最早发生在哪一步**。

    python -m tools.pipeline            # 体检 + 归因
    python -m tools.pipeline --run "硕士毕业落户能加多少分"

输出三段：
1. 每段的"存活判据"（不是"有没有代码"，是"产出物对不对"）；
2. 抽查一条 run，看它在哪一步停；
3. 归因：把评测集里的失败按最早出错阶段分类，并给出"先修哪里"。
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.domain.coverage import CoverageIndex
from app.retrieval.search import index_meta

PROCESSED = Path("data/processed")


@dataclass
class Check:
    stage: str
    name: str
    ok: bool
    detail: str


def _docs() -> list[dict]:
    return [json.loads(Path(p).read_text("utf-8")) for p in glob.glob(str(PROCESSED / "*.json"))]


def s1() -> list[Check]:
    docs = _docs()
    cov = CoverageIndex.load()
    t = cov.totals
    from tools.ingest.orphans import scan
    orphans = sum(1 for r in scan() if r.get("kind") != "probe")
    return [
        Check("S1", "语料在盘", bool(docs), f"{len(docs)} 份记录"),
        Check("S1", "城市登记", t.get("cities_total", 0) > 0,
              f"{t.get('cities_reachable')}/{t.get('cities_total')} 可达，{t.get('cities_blocked')} 未接入"),
        Check("S1", "孤儿可控", orphans <= 10, f"{orphans} 个孤儿（有字节无记录）"),
    ]


def s2() -> list[Check]:
    docs = _docs()
    empty = [d for d in docs if (d.get("stats") or {}).get("chars", 0) < 40]
    zero_blocks = [d for d in docs if not d.get("blocks")]
    extract = {}
    for d in docs:
        k = d.get("extract") or "?"
        extract[k] = extract.get(k, 0) + 1
    return [
        Check("S2", "每份都有字", not empty, f"{len(empty)} 份 <40 字"),
        Check("S2", "每份都有块", not zero_blocks, f"{len(zero_blocks)} 份 0 块"),
        Check("S2", "载体已标注", True, "，".join(f"{k} {v}" for k, v in sorted(extract.items()))),
    ]


def s3() -> list[Check]:
    docs = _docs()
    no_no = [d for d in docs if not d.get("doc_no")]
    guessed = [d for d in docs if d.get("doc_no_source") == "text"]
    no_date = [d for d in docs if not (d.get("issued_at") or d.get("effective_from"))]
    return [
        Check("S3", "文号齐全", not no_no, f"{len(no_no)} 份无文号"),
        Check("S3", "文号非猜", not guessed, f"{len(guessed)} 份文号来自正文猜测"),
        Check("S3", "至少一个日期", len(no_date) / max(len(docs), 1) < 0.3,
              f"{len(no_date)}/{len(docs)} 份没有任何日期"),
    ]


def s4() -> list[Check]:
    docs = _docs()
    zero = [d for d in docs if not d.get("clauses")]
    total = sum(len(d.get("clauses") or []) for d in docs)
    dup = 0
    for d in docs:
        seen = set()
        for c in d.get("clauses") or []:
            k = (tuple(c.get("path") or []), c.get("sec", 0))
            if k in seen:
                dup += 1
            seen.add(k)
    return [
        Check("S4", "条款已切", total > 0, f"{total:,} 条（{len(docs)-len(zero)} 份有条款，{len(zero)} 份没有）"),
        Check("S4", "路径唯一", dup == 0, f"{dup} 条重复 (path, sec)"),
    ]


def s5() -> list[Check]:
    from tools.eval_v2 import run as eval_run
    meta = index_meta()
    from tools.eval_v2 import summarize
    res = eval_run("dev")
    off = summarize(res["rows"]).get("official") or {}
    return [
        Check("S5", "索引已建", bool(meta.get("index_version")),
              f"{meta.get('clauses', '?')} 条款 / {meta.get('children', '?')} 子块（{meta.get('index_version')}）"),
        Check("S5", "dev 可测", (off.get("n") or 0) > 50,
              f"official n={off.get('n')}，hit@1 {off.get('hit1', 0):.0%}"),
    ]


def s7() -> list[Check]:
    from tools.diagnose.attribute import run as attr_run
    res = attr_run("dev")
    stages = res["by_stage"]
    bad = {k: len(v) for k, v in stages.items() if k not in ("OK",)}
    names = {"S1": "语料", "S2": "解析", "S3": "抽取", "S4": "结构化",
             "S5": "检索", "S6": "判定", "S7": "评测集"}
    detail = "，".join(f"{names.get(k, k)} {v}" for k, v in sorted(bad.items())) or "无失败"
    return [Check("S7", "归因可算", True, detail)]


def s8(question: str | None) -> list[Check]:
    from app.runtime import Engine, Store
    if not question:
        return [Check("S8", "跳过（未给 --run）", True, "用 --run \"问题\" 跑一条")]
    st = Engine(Store()).run(question)
    return [
        Check("S8", "能推进", st.status in ("done", "waiting"), f"{st.status} @ {st.step}"),
        Check("S8", "有事件流", bool(Store().events(st.run_id)), f"{len(Store().events(st.run_id))} 条事件"),
        Check("S8", "有引用", bool(st.hits), f"{len(st.hits)} 条候选"),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="跑一条真实提问")
    ap.add_argument("--skip-eval", action="store_true", help="跳过评测（省 20 秒）")
    args = ap.parse_args(argv)

    groups = [s1(), s2(), s3(), s4()]
    if not args.skip_eval:
        groups += [s5(), s7()]
    groups += [s8(args.run)]

    print(f"{'阶段':<4}{'判据':<18}{'结果':<6}说明")
    fails = 0
    for g in groups:
        for c in g:
            mark = "✓" if c.ok else "✗"
            fails += 0 if c.ok else 1
            print(f"{c.stage:<4}{c.name:<18}{mark:<6}{c.detail}")

    if not args.skip_eval:
        from tools.diagnose.attribute import run as attr_run
        res = attr_run("dev")
        order = ["S1", "S2", "S3", "S4", "S5"]   # S7 是评测集卫生问题，不算系统缺陷
        first = next((s for s in order if res["by_stage"].get(s)), None)
        print()
        print(f"归因结论：先修 {first}" if first else "归因结论：无失败")
        if res["by_stage"].get("S7"):
            print(f"（另有 {len(res['by_stage']['S7'])} 条金标本身过期，属于评测集卫生，先摘掉再谈修复）")
    print(f"\n{'通过' if fails == 0 else f'{fails} 项不达标'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
