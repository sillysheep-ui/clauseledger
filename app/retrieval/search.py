"""检索：问题 → 条款 + 出处。不做判定，只负责"找到并证明它在哪"。

两个刻意的设计：
1. **返回空是合法结果**。最高分低于阈值时返回 insufficient，而不是硬凑 top-1——
   下游据此给出"证据不足"，而不是拿一段不相干的条文假装回答。
2. **效力状态参与过滤但不隐藏**。判定只允许用 effective；检索可以召回 expired/unknown，
   但必须把状态带回给调用方，由它决定怎么说。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import bm25s

from app.retrieval.index import INDEX, tokenize
from app.retrieval.records import Clause

MIN_SCORE = 3.0      # BM25 绝对分；低于它基本是"只共享了两个字"
MIN_COVERAGE = 0.5   # 首条条款至少覆盖一半提问词，否则算"没找到"

# 覆盖范围：只做上海。问题里出现别的城市，直接说没覆盖，不许拿上海条款凑。
SUPPORTED_CITIES = {"上海"}
OTHER_CITIES = (
    "北京 天津 重庆 河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 河南"
    " 湖北 湖南 广东 海南 四川 贵州 云南 陕西 甘肃 青海 台湾 内蒙古 广西 西藏 宁夏 新疆"
    " 香港 澳门 深圳 广州 杭州 南京 苏州 成都 武汉 西安 长沙 郑州 青岛 宁波 厦门 合肥"
    " 济南 福州 昆明 贵阳 南昌 太原 石家庄 沈阳 哈尔滨 长春 兰州 西宁 银川 乌鲁木齐"
).split()

# 政策域：问题里一个都不沾，就不该在本库里找答案。
DOMAIN_TERMS = (
    "社保 社会保险 养老 退休 工伤 失业 生育 医疗 劳动 工资 加班 休假 请假 丧假 产假"
    " 职称 培训 补贴 津贴 派遣 合同 档案 落户 户口 就业 招聘 缴费 参保 年金 待遇"
    " 伤残 抚恤 认定 鉴定 技师 技能 职业 退休金 岗位 收入 补助 奖金 工时 休息"
    " 假期 病假 婚假 产假 陪产假 职业病 公益性 探亲假 育儿假 护理假 年休假 事假 调休"
    " 工时 加班费 经济补偿 解除 辞退 试用期 劳动合同 社保卡 养老金 失业金 丧葬费"
    " 抚恤金 遗属 供养 技能 等级 证书 补贴 落户 积分"
).split()


# 语料只接入上海市人社局发布的文件。问教委/公安/税务的事，不能说"查过没有政策"——
# 那是把"我们没接入"说成"世界上没有"，比答错更严重。
NOT_COVERED_DEPTS = {
    "教委": "市教委", "教育局": "市教委", "公安": "市公安局", "出入境": "市公安局",
    "税务": "市税务局", "税务局": "市税务局", "公积金": "市公积金中心",
    "住建委": "市住建委", "民政": "市民政局", "卫健委": "市卫健委",
    "市场监管局": "市市场监管局", "医保局": "市医保局",
}


def detect_city(query: str) -> str | None:
    for c in OTHER_CITIES:
        if c in query:
            return c
    for c in SUPPORTED_CITIES:
        if c in query:
            return c
    return None


def refuse_reason(query: str) -> str | None:
    """返回拒答理由；None 表示这个问题在本系统覆盖范围内。"""
    city = detect_city(query)
    if city and city not in SUPPORTED_CITIES:
        return f"未覆盖{city}的政策（当前只接入上海）"
    if not any(t in query for t in DOMAIN_TERMS):
        return "不在覆盖范围内：本系统只回答劳动就业、社会保险、人事人才、工资福利类政策"
    for k, dept in NOT_COVERED_DEPTS.items():
        if k in query:
            return f"未接入{dept}发布的政策（当前只接入上海市人社局的文件）"
    return None
QUESTION_WORDS = {"哪些", "什么", "怎么", "如何", "多少", "是否", "能否", "可以",
                  "需要", "请问", "一下", "几天", "多久", "有没有"}


@dataclass
class Answer:
    """一次检索的完整结果。拒答也是结果，必须说清"查了什么、查了多少"。"""
    hits: list["Hit"]
    reason: str = ""          # 非空表示拒答
    kind: str = "ok"          # ok | not_covered | out_of_domain | insufficient
    n_docs: int = 0           # 本次实际检索覆盖的文档数
    n_candidates: int = 0     # 闸门之前召回并通过分数线的候选数

    @property
    def refused(self) -> bool:
        return bool(self.reason)


@dataclass
class Hit:
    clause: Clause
    score: float
    coverage: float = 0.0   # 查询词有多少比例真的出现在这条条款里
    versions: int = 1       # 同名文件的历史版本数（含本条）
    _rank: float = 0.0      # 排序用：分数 × 效力加成


def _load() -> tuple[bm25s.BM25, list[Clause]]:
    retriever = bm25s.BM25.load(str(INDEX / "bm25"))
    clauses = [Clause(**json.loads(l)) for l in
               (INDEX / "clauses.jsonl").read_text("utf-8").splitlines() if l.strip()]
    return retriever, clauses


def search(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
           min_score: float = MIN_SCORE, min_coverage: float = 0.0) -> list[Hit]:
    return search_result(query, k=k, status=status, min_score=min_score,
                         min_coverage=min_coverage).hits


def search_result(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
                  min_score: float = MIN_SCORE, min_coverage: float = 0.0) -> Answer:
    reason = refuse_reason(query)
    if reason:
        kind = "not_covered" if reason.startswith(("未覆盖", "未接入")) else "out_of_domain"
        return Answer([], reason, kind, n_docs=0)
    retriever, clauses = _load()
    qt = [t for t in tokenize(query) if t not in QUESTION_WORDS]
    if not qt:
        return Answer([], "问题里没有可用于检索的词", "insufficient")
    idx, scores = retriever.retrieve([qt], k=min(len(clauses), max(k * 4, 40)))
    out: list[Hit] = []
    for i, s in zip(idx[0], scores[0]):
        c = clauses[int(i)]
        if status is not None and c.status not in status:
            continue
        if float(s) < min_score:
            continue
        ctext = set(tokenize(c.text))
        cov = len(set(qt) & ctext) / max(1, len(set(qt)))
        if cov < min_coverage:
            continue
        # 现行有效优先：同年份文件文本高度相似、分数接近，靠状态打破平手
        boost = 1.08 if c.status == "effective" else 1.0
        # 标题命中加权：用户问的是"这件事"，文件标题写着这件事，比正文里顺带提一句更有分量
        title_hits = len(set(qt) & set(tokenize(c.title)))
        out.append(Hit(c, float(s), round(cov, 2),
                       _rank=float(s) * boost + 0.6 * title_hits))
    out.sort(key=lambda h: h._rank, reverse=True)
    n_docs = len({h.clause.doc_no for h in out})
    if not out:
        return Answer([], "没有条款达到相关性下限", "insufficient", n_docs, 0)
    if out[0].coverage < MIN_COVERAGE:
        return Answer([], f"首条条款只覆盖 {out[0].coverage:.0%} 的提问词", "insufficient",
                      n_docs, len(out))
    return Answer(_collapse(out)[:k], "", "ok", n_docs, len(out))


def _status_rank(status: str) -> int:
    return {"effective": 3, "pending": 2, "unknown": 1}.get(status, 0)


def _norm_title(t: str) -> str:
    """同一政策不同年份的文件，标题只差前面的发文机关前缀，砍掉再比。"""
    t = "".join(ch for ch in t if ch not in " \u3000")
    i = t.find("关于")
    if i > 0:
        t = t[i:]
    return t.rstrip("。")


def _collapse(hits: list[Hit]) -> list[Hit]:
    """两份事：同一份文件只留最相关的一条；同名文件（逐年延期的"关于调整…的通知"）
    合并成一条并记下版本数，否则 top-5 会被 2016/2017/2018 三个版本占满。"""
    seen_doc: set[str] = set()
    by_title: dict[str, Hit] = {}
    order: list[str] = []
    for h in hits:
        c = h.clause
        if c.doc_no in seen_doc:
            continue
        seen_doc.add(c.doc_no)
        key = _norm_title(c.title) or c.doc_no
        if key not in by_title:
            by_title[key] = h
            order.append(key)
        else:
            # 同名文件 = 同一个政策的历年版本，留现行的那份，而不是分数最高的那份
            kept = by_title[key]
            kept.versions += 1
            if (_status_rank(h.clause.status), h.clause.effective_from or "") > \
               (_status_rank(kept.clause.status), kept.clause.effective_from or ""):
                h.versions = kept.versions
                by_title[key] = h
        h.versions = 1
    return [by_title[k] for k in order]


def evidence_gap(query: str, hits: list[Hit]) -> str | None:
    """回答"为什么答不了"，而不是只说答不了。"""
    if not hits:
        return "没有检索到相关条款；可能是该事项不在已接入语料范围内，或问法与条文用词差异过大。"
    top = hits[0]
    if top.coverage < 0.5:
        return (f"最相关条款只覆盖了 {top.coverage:.0%} 的提问词"
                f"（《{top.clause.doc_no}》{top.clause.citation.split('》')[-1]}），"
                "大概率答非所问，不给结论。")
    return None
