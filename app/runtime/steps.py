"""节点实现：每一步只做一件事，并且必须能被重复执行。

约定：
- 输入是 `RunState`，输出是"下一步的名字"（None = 按顺序走）；
- 不吞异常；要停就 `raise PendingGate`；
- 有 `STUB` 标记的地方是**故意留空**的，不是忘了做——框架先立，内容后补。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import actions
from .gates import PendingGate, decide

PROCESSED = Path("data/processed")
LUOHU_KW = ("落户", "积分", "应届", "72 分", "72分")


# ---------- helpers ----------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _hit_dict(h) -> dict:
    c = h.clause
    return {"doc_no": c.doc_no, "title": c.title, "citation": c.citation,
            "score": round(h.score, 3), "coverage": round(h.coverage, 3),
            "stale": h.stale, "scope": h.scope, "path": list(c.path),
            "text": (h.child or c.text)[:400]}


# ---------- steps ----------
def normalize(state, decision):
    from app.domain.validity import year_of
    from app.retrieval.search import detect_city
    state.city = detect_city(state.question) or "上海"
    state.year = year_of(state.question)
    state.answer = {"cities": [state.city], "as_of": state.year and f"{state.year}-12-31"}


def coverage(state, decision):
    from app.domain.coverage import CoverageIndex
    c = CoverageIndex.load().check(state.city or "上海", None)
    state.coverage = {"status": c.status, "city": c.city, "message": c.message,
                      "allows_retrieval": c.allows_retrieval}
    if not c.allows_retrieval:
        state.answer = {**(state.answer or {}), "refusal": c.message, "kind": "not_covered"}


def retrieve(state, decision):
    from app.retrieval.search import search_result
    if state.coverage and not state.coverage["allows_retrieval"]:
        state.hits = []
        return
    a = search_result(state.question, k=5)
    state.hits = [_hit_dict(h) for h in a.hits]
    state.answer = {**(state.answer or {}), "kind": a.kind, "reason": a.reason,
                    "searched": a.searched, "as_of": a.as_of, "n_candidates": a.n_candidates}


def judge(state, decision):
    """四态判定。目前只有落户有判定器，其余是 STUB（返回 unknown）。"""
    q = state.question
    if any(k in q for k in LUOHU_KW) and state.profile:
        from app.judgement.luohu import evaluate
        v = evaluate(state.profile)
        state.judgement = {"verdict": v.verdict, "total_known": v.total_known,
                           "total_upper": v.total_upper, "standard": v.standard,
                           "gap": v.gap, "direct": v.direct,
                           "items": [{"id": i.id, "label": i.label, "state": i.state,
                                      "points": i.points, "page": i.page} for i in v.items],
                           "source": "luohu-2026"}
        return
    blocked = (state.answer or {}).get("kind") in ("not_covered", "out_of_domain")
    # STUB：没有判定器的事项一律 unknown —— 不知道 ≠ 不满足
    state.judgement = {"verdict": "unknown", "reason": "该事项尚未接入判定器（S6 未完成）",
                       "source": "none", "blocked": blocked,
                       "evidence_docs": [h["doc_no"] for h in state.hits[:3]]}


def gate_judgement(state, decision):
    j = state.judgement or {}
    if j.get("verdict") != "unknown" or j.get("confirmed") or j.get("blocked"):
        return
    act = decide(decision)
    if act == "confirm_unknown":
        j["confirmed"] = True
        j["decided_by"] = (decision or {}).get("by", "")
        return
    if act == "reject":
        state.answer = {**(state.answer or {}), "refusal": "人工判定：证据不足，不出结论",
                        "kind": "human_reject"}
        state.judgement = {**j, "verdict": "unknown", "confirmed": True, "rejected": True}
        return
    raise PendingGate("judgement",
                      {"judgement": j, "hits": state.hits[:3]},
                      "判定结果是「未知」，需要人确认是补材料还是按未知返回")


def compose(state, decision):
    """组装答案：**只解释判定，不产生判定**。"""
    j = state.judgement or {}
    cites = [h["citation"] for h in state.hits[:3]]
    if state.answer and state.answer.get("refusal"):
        text = state.answer["refusal"]
    elif j.get("verdict") == "satisfied":
        text = f"满足。已知得分 {j.get('total_known')}（标准 {j.get('standard')}）。"
    elif j.get("verdict") == "unsatisfied":
        text = f"不满足。上限 {j.get('total_upper')}，低于标准 {j.get('standard')}。"
    elif j.get("verdict") == "direct":
        text = f"符合直落条件：{j.get('direct')}。"
    else:
        text = "无法判定。缺少可结构化的条件，或该事项尚未接入判定器。"
    state.answer = {**(state.answer or {}), "text": text, "citations": cites}


def verify(state, decision):
    """引用核验：条文必须能在原始记录里逐字找到（去空白后包含）。"""
    checked = []
    for h in state.hits[:3]:
        doc = PROCESSED / f"{h['doc_no'].replace('〔', '-').replace('〕', '-')}.json"
        ok = False
        if doc.exists():
            d = json.loads(doc.read_text("utf-8"))
            hay = _norm("".join(b["text"] for b in d.get("blocks", [])))
            needle = _norm(h.get("text", ""))[:60]
            ok = bool(needle) and needle in hay
        checked.append({"citation": h["citation"], "doc": doc.name, "verbatim": ok})
    state.answer = {**(state.answer or {}), "verified": checked,
                    "all_verbatim": all(c["verbatim"] for c in checked) if checked else False}


def gate_action(state, decision):
    """有副作用才停。没有待办就不停——门控不是流程装饰。"""
    j = state.judgement or {}
    if j.get("verdict") not in ("satisfied", "direct") or j.get("rejected"):
        state.action = None
        return
    items = [{"text": f"核对《{h['title']}》相关条款", "doc_no": h["doc_no"]}
             for h in state.hits[:3]]
    state.action = {"kind": "todo", "items": items}
    act = decide(decision)
    if act == "approve":
        state.action["approved_by"] = (decision or {}).get("by", "")
        return
    if act == "deny":
        state.action["denied"] = True
        return
    raise PendingGate("action", {"action": state.action},
                      f"将写入 {len(items)} 条待办，需要批准")


def act(state, decision):
    a = state.action
    if not a or a.get("denied") or not a.get("items"):
        return
    res = actions.apply(state.run_id, a["kind"], {"items": a["items"]})
    state.action = {**a, "result": res}


def finalize(state, decision):
    state.finish()


STEPS_FN = {name: fn for name, fn in [
    ("normalize", normalize), ("coverage", coverage), ("retrieve", retrieve),
    ("judge", judge), ("gate_judgement", gate_judgement), ("compose", compose),
    ("verify", verify), ("gate_action", gate_action), ("act", act),
    ("finalize", finalize)]}
