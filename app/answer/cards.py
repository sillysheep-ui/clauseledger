"""三张卡：判定卡 / 标准卡 / 拒答卡。

产品形态是「给判定，不给文章」。用户看到的不是一个答案、五条候选，
而是**一个结论 + 逐条可核对的依据**；判定不了的时候，给标准和缺什么。

硬规则（这个模块存在的理由）：

    卡里每一句话，要么是某条条款的**逐字片段**（带页码），
    要么被明确标注为「推断」或「需要你确认」。
    做不到就丢掉，不允许"大概是这样"。

三种卡对应三种确定性：

    verdict   —— 有规则、有你的信息：给结论 + 分数/缺口 + 依据
    standard  —— 有条款、缺你的信息：给标准 + 原文 + 待确认清单
    refusal   —— 没接入 / 没政策 / 找不到直接条款：给"缺什么"

为什么不生成一段话：生成式的错误是**无声**的（"不超过"写成"不低于"），
而这里的每个字段都能被下游核验、被界面高亮、被审计回放。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

QUOTE_LIMIT = 240
QUOTE_SHORT = 400      # 条款本身就这么长，整条给，不切
DIGIT = re.compile(r"\d")
SENT = re.compile(r"[^。；！？\n]{1,400}[。；！？]?")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def quote(text: str, limit: int = QUOTE_LIMIT) -> str:
    """取一段**完整句子**，不切断词。

    短条款（<= QUOTE_SHORT）整条给——引文只有 240 字时，答案常被切在
    "……调整到每人每月" 后面，用户看到半句、我们却以为"给了原文"。

    长条款优先从**带数字的那一句**开始取：金额、期限、比例都在数字句里，
    而条款开头往往是"根据……制定本办法"这类没有信息量的引语。
    """
    text = (text or "").strip()
    if len(text) <= QUOTE_SHORT:
        return text
    sents = [m.group(0) for m in SENT.finditer(text)]
    start = 0
    for i, s in enumerate(sents):
        if DIGIT.search(s) and i < len(sents) - 1:
            start = i
            break
    cut, used = "", 0
    for s in sents[start:]:
        if used and used + len(s) > limit:
            break
        cut += s
        used += len(s)
    return (cut or text[:limit]).strip()


def _related_citation(h: dict, r: dict) -> str:
    path = " / ".join(r.get("path") or [])
    page = r.get("page")
    tail = f"（第 {page} 页）" if page else ""
    return f"《{h.get('doc_no')}》{path}{tail}"


def _evidence(hits: Iterable[dict], limit: int = 8) -> list[dict]:
    out = []
    for h in list(hits):
        # 同文件的补充条款先展开：数字/日期常在"另一条"上，
        # 只给命中的那一条会让用户看到"调整到 130 元"却看不到"每人 320 元"。
        for r in (h.get("related") or [])[:2]:
            if len(out) >= limit:
                break
            out.append({"doc_no": h.get("doc_no"), "title": h.get("title"),
                        "citation": _related_citation(h, r), "page": r.get("page"),
                        "path": list(r.get("path") or []),
                        "quote": quote(r.get("text") or ""), "score": h.get("score"),
                        "stale": h.get("stale") or "", "verified": None, "role": "support"})
        if len(out) >= limit:
            break
        text = h.get("text") or ""
        out.append({
            "doc_no": h.get("doc_no"),
            "title": h.get("title"),
            "citation": h.get("citation"),
            "page": h.get("page"),
            "path": h.get("path") or [],
            "quote": quote(text),
            "score": h.get("score"),
            "stale": h.get("stale") or "",
            "verified": None,      # 由 verify 节点回填
            "role": h.get("role") or "",
        })
    return out


def _fmt(x: Any) -> str:
    return "" if x is None else str(x)


# ---------------- 判定卡 ----------------
def verdict_card(judgement: dict, hits: list[dict], as_of: str | None = None) -> dict:
    v = judgement or {}
    known = v.get("total_known")
    upper = v.get("total_upper")
    std = v.get("standard")
    verdict = v.get("verdict")

    gap = None
    if verdict == "unsatisfied" and upper is not None and std is not None:
        gap = std - upper          # 上限都够不到，差多少是确定的
    elif verdict == "unknown" and known is not None and std is not None and known < std:
        gap = std - known          # 已知分还不够，至少差这么多

    items = []
    missing, confirmed = [], []
    for it in v.get("items") or []:
        row = {"label": it.get("label"), "state": it.get("state"),
               "points": it.get("points"), "page": it.get("page")}
        items.append(row)
        if it.get("state") == "unknown":
            missing.append(it.get("label"))
        elif it.get("points"):
            confirmed.append(row)

    if verdict == "satisfied":
        headline = f"满足：已知 {known} 分，标准 {std} 分"
    elif verdict == "unsatisfied":
        headline = f"不满足：上限 {upper} 分，低于标准 {std} 分"
    elif verdict == "direct":
        headline = f"符合直落条件：{_fmt(v.get('direct'))}"
    elif gap and gap > 0:
        # 已知分都不够，至少差这么多——这句话比"无法判定"有用得多
        headline = (f"还不能确定：已知 {known} 分，标准 {std} 分，"
                    f"至少还差 {gap} 分" + (f"；另有 {len(missing)} 项待确认" if missing else ""))
    else:
        headline = f"还不能确定：还有 {len(missing)} 项待确认" if missing else "还不能确定"

    return {
        "kind": "verdict",
        "verdict": verdict,
        "headline": headline,
        "score": {"known": known, "upper": upper, "standard": std, "gap": gap},
        "direct": v.get("direct"),
        "basis": confirmed[:12],
        "missing": missing,
        "evidence": _evidence(hits),
        "as_of": as_of,
        "source": v.get("source"),
        # 明确说"这卡没覆盖什么"，避免用户把判定当全面结论
        "not_claimed": ["单位资质", "申报材料真实性", "当年政策是否另有调整"],
    }


# ---------------- 标准卡 ----------------
def standard_card(answer: dict, hits: list[dict], as_of: str | None = None) -> dict:
    """有条款、缺你的信息：给原文 + 引用，并列出"要你确认什么"。

    没有生成，只有引用；`depends_on` 是给人看的、不是给机器判的。
    """
    a = answer or {}
    ev = _evidence(hits)
    return {
        "kind": "standard",
        "headline": "找到了相关规定，但结论取决于你的具体情况",
        "items": [{"citation": e["citation"], "quote": e["quote"],
                   "page": e["page"], "doc_no": e["doc_no"]} for e in ev],
        "depends_on": ["你的具体身份/单位类型", "发生时间（政策有版本）",
                       "是否已享受过同类待遇"],
        "reason": a.get("reason") or "",
        "confidence": a.get("confidence") or "high",
        "evidence": ev,
        "as_of": as_of,
        "not_claimed": ["没有替你判断是否符合条件"],
    }


# ---------------- 拒答卡 ----------------
def refusal_card(answer: dict, as_of: str | None = None) -> dict:
    a = answer or {}
    kind = a.get("kind") or "insufficient"
    reason = a.get("refusal") or a.get("reason") or ""
    searched = a.get("searched") or {}
    if kind == "not_covered":
        headline = "没有接入这块政策，所以不能回答"
    elif kind == "out_of_domain":
        headline = "这个问题不在本系统覆盖范围内"
    elif kind == "human_reject":
        headline = "人工复核认为证据不足，不出结论"
    else:
        headline = "没有找到能直接回答的条款"
    return {
        "kind": "refusal",
        "reason_kind": kind,
        "headline": headline,
        "reason": reason,
        "searched": searched,
        # 检索痕迹要能自证"我们查过"，否则等于没查
        "evidence": _evidence(a.get("hits") or []),
        "as_of": as_of,
        "next": "换一种问法，或补充：城市 / 时间 / 你的身份",
    }


# ---------------- 分派 ----------------
def build(state) -> dict:
    """按"能不能判定"分派，而不是按模板。"""
    answer = getattr(state, "answer", None) or {}
    judgement = getattr(state, "judgement", None) or {}
    hits = list(getattr(state, "hits", None) or [])
    as_of = answer.get("as_of") or (getattr(state, "year", None) and f"{state.year}-12-31")

    kind = answer.get("kind")
    if answer.get("refusal") or kind in ("not_covered", "out_of_domain",
                                         "insufficient", "human_reject"):
        return refusal_card({**answer, "hits": hits}, as_of)

    verdict = judgement.get("verdict")
    src = judgement.get("source")
    if src and src != "none" and verdict in ("satisfied", "unsatisfied", "direct", "unknown"):
        # 有判定器就出判定卡：哪怕是 unknown，也要给"已知多少、还差多少"
        return verdict_card(judgement, hits, as_of)

    if hits:
        return standard_card(answer, hits, as_of)
    return refusal_card({**answer, "kind": kind or "insufficient", "hits": []}, as_of)


def headline(card: dict) -> str:
    """CLI/日志用的一行摘要。"""
    return card.get("headline") or ""
