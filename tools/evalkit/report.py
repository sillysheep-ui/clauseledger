"""报告渲染：一张表 + 失败归类。规则见 docs/EVAL-SPEC.md §1。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.eval.metrics import Rate, rate

CORPUS = Path("data/index/stats.json")


def corpus_version() -> str:
    import json

    try:
        s = json.loads(CORPUS.read_text(encoding="utf-8"))
        return f"docs={s.get('docs', '?')} clauses={s.get('clauses', '?')}"
    except Exception:
        return "unknown"


def pct(r: Rate) -> str:
    return f"{r.successes}/{r.n} = {r.p:.0%}" + (f"  [{r.lo:.0%}, {r.hi:.0%}]" if r.n >= 20 else "")


def render(title: str, res: dict) -> str:
    rows = res["rows"]
    n = len(rows)
    lines = [
        f"# {title}",
        "",
        f"测量时间 {datetime.now():%Y-%m-%d %H:%M} · 语料 {res.get('corpus', corpus_version())} · "
        f"配置 rerank={res.get('rerank')} · n={n}",
        "",
        "## 主表（判据：掉下去会不会让你改代码）",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
    ]
    aec = rate(sum(1 for r in rows if r.get("aec")), n)
    lines.append(f"| AEC 证据覆盖 | {pct(aec)} | 官方答案句出现在给模型的证据里 |")
    live = [r for r in rows if r.get("state") != "stale_gold"]
    for k in (1, 5, 10):
        got = sum(1 for r in live if r.get("rank") and r["rank"] <= k)
        lines.append(f"| EvidenceRecall@{k} | {pct(rate(got, len(live)))} | 前 {k} 条并起来能覆盖答案句；分母=可核对 {len(live)} 题 |")
    neg = res.get("negatives", {})
    if neg:
        lines.append(
            "| 拒答（域内无政策 / 域外） | "
            f"{neg.get('no_policy_refused', 0)}/{neg.get('no_policy_n', 0)} · "
            f"{neg.get('oos_refused', 0)}/{neg.get('oos_n', 0)} | 该拒的都拒了 |"
        )
        lines.append(
            "| 误拒 / 误答 | "
            f"{res.get('false_refused', 0)}/{n} · {neg.get('neg_answered', 0)} | 不该拒的拒了 / 没有政策却答了 |"
        )
    lines += [
        "| Groundedness | 未在本套件内 | 生成层单独跑 |",
        "| 判定准确率 | 未测 | 缺 30 条标注 |",
        "| 行为断言 | 未成套件 | C17–C22 仍是单测 |",
        "",
        "## 失败归类（只报条数）",
        "",
        "| 类别 | 条数 | 含义 |",
        "|---|---|---|",
    ]
    for key, desc in (
        ("stale_gold", "语料里找不到覆盖官方答案句的金标条款（分母外单列）"),
        ("retrieval_miss", "金标条款存在但没进 top-50"),
        ("sentence_missed", "金标条款进了证据，但答案句没被覆盖"),
        ("ok", "AEC 与条款召回都成立"),
    ):
        lines.append(f"| {key} | {sum(1 for r in rows if r.get('taxonomy') == key)} | {desc} |")
    if res.get("rank_debug"):
        lines += ["", "## 诊断（不作结论）", "", "| 指标 | 值 |", "|---|---|"]
        for k, v in res["rank_debug"].items():
            lines.append(f"| {k} | {v} |")
    return "\n".join(lines) + "\n"
