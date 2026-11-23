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


def _hit_dict(h, role: str = "") -> dict:
    c = h.clause
    return {"role": role,
            "doc_no": c.doc_no, "title": c.title, "citation": c.citation,
            "page": c.page, "score": round(h.score, 3), "coverage": round(h.coverage, 3),
            "stale": h.stale, "scope": h.scope, "path": list(c.path),
            "text": (h.child or c.text)[:400],
            # 同文件的补充条款：卡片要"一个答案 + 这个文件里相关的几句"，
            # 只给命中的一条会漏掉写着金额/期限的那条（实测答案级 15/64 → 21/64）。
            "related": [{"path": r.get("path") or [], "page": r.get("page"),
                         "text": (r.get("text") or "")[:400]}
                        for r in (getattr(h, "related", []) or [])[:2]]}


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
    """四态判定。目前只有落户有判定器，其余是 STUB（返回 unknown）。

    覆盖不到的城市**不进入判定**：判定器是上海落户的规则，拿它去判"北京落户"
    会得出"已知 0 分、差 72 分"这种看起来煞有介事、实际毫无意义的结论。
    """
    q = state.question
    if state.coverage and not state.coverage.get("allows_retrieval"):
        state.judgement = {"verdict": "unknown", "source": "none", "blocked": True,
                           "reason": state.coverage.get("message") or "未接入"}
        return
    # 判定器是上海落户规则，别的城市不能拿它算分
    if (state.city or "上海") != "上海":
        state.judgement = {"verdict": "unknown", "source": "none", "blocked": True,
                           "reason": f"{state.city}尚未接入判定器"}
        return
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
    # 没有判定器时不出判定，走标准卡；让"缺规则"去拦人，等于每道题都拦
    if not j.get("source") or j.get("source") == "none":
        return
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
    # 停住之前先把"草稿卡"放进 state：人要在**看到卡**的情况下决定批不批，
    # 否则界面只有一个"待确认"按钮，等于让人盲批。
    from app.answer import cards
    state.answer = {**(state.answer or {}), "card": cards.build(state),
                    "text": "（待人工确认）"}
    raise PendingGate("judgement",
                      {"judgement": j, "hits": state.hits[:3],
                       "card": state.answer["card"]},
                      "判定结果是「未知」，需要人确认是补材料还是按未知返回")


def compose(state, decision):
    """组装答案：**只解释判定，不产生判定**。

    三种卡：能判定 → 判定卡；有条款、缺你的信息 → 标准卡；没接入/没政策 → 拒答卡。
    每句话要么是条款的逐字片段，要么被标成"需要你确认"。
    """
    from app.answer import cards

    card = cards.build(state)
    card = _attach_draft(state, card)
    state.answer = {**(state.answer or {}), "card": card,
                    "text": card.get("headline", ""),
                    "citations": [e["citation"] for e in card.get("evidence", [])]}


def _attach_draft(state, card: dict) -> dict:
    """给卡挂一段"模型解读"，但**绝不改判定**。

    生成在这里的定位是解释器：判定、分数、缺口都是程序算的，模型只负责把
    证据串成人话。所以：

    * 拒答卡不生成——没有证据时让模型说话，它一定会编；
    * 每句必须带 [E#]，数字必须能在证据里找到，否则**逐句丢弃**（verify）；
    * 生成失败一律静默退回截取式卡片，卡本身永远可用。
    """
    import os

    if os.environ.get("CLAUSELEDGER_GENERATE", "1") == "0":
        return card
    if card.get("kind") == "refusal" or not state.hits:
        return card

    from app.answer import generate as G
    from app.answer.context import assemble
    from app.answer.verify import check
    from app.retrieval.search import search_result

    if not G.available():
        return card
    try:
        # 重新取一次召回结果：state.hits 是给卡片用的扁平 dict，没有条款对象；
        # 复用同一个 search_result 保证"卡片看到的"和"模型看到的"是同一批候选。
        again = search_result(state.question, k=5)
        ev = assemble(state.question, again.hits)
        rules = None
        if card.get("kind") == "verdict":
            basis = [(b.get("label", ""), b.get("points"))
                     for b in (card.get("basis") or []) if isinstance(b, dict)]
            rules = f"{card.get('headline', '')}\n" + "\n".join(
                f"- {lab}{'' if pts is None else f' +{pts}'}" for lab, pts in basis)
        draft = G.generate(state.question, ev, rules=rules)
        if draft is None:
            return card
        chk = check(draft, ev)
        if not chk.kept:
            return card
        card = {**card, "draft": {
            "sentences": chk.kept,
            "dropped": len(chk.dropped),
            "issues": chk.issues[:3],
            "not_covered": draft.not_covered,
            "confidence": draft.confidence,
            "model": draft.model,
            "cached": draft.cached,
        }}
    except Exception as e:  # noqa: BLE001
        card = {**card, "draft_error": f"{type(e).__name__}: {e}"}
    return card


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
