"""检索：问题 → 条款 + 出处。不做判定，只负责"找到并证明它在哪"。

两个刻意的设计：
1. **返回空是合法结果**。最高分低于阈值时返回 insufficient，而不是硬凑 top-1——
   下游据此给出"证据不足"，而不是拿一段不相干的条文假装回答。
2. **效力状态参与过滤但不隐藏**。判定只允许用 effective；检索可以召回 expired/unknown，
   但必须把状态带回给调用方，由它决定怎么说。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from math import exp as _pow_e
from pathlib import Path

import bm25s

from app.domain.registry import DocRegistry
from app.retrieval import confidence as _conf
from app.retrieval import lexicon, rewrite
from app.retrieval import rerank as _rerank
from app.retrieval.index import INDEX, tokenize
from app.retrieval.records import Clause

MIN_SCORE = 3.0      # BM25 绝对分；低于它基本是"只共享了两个字"
MIN_COVERAGE = 0.5   # 只用于展示分档，不再参与拒答（见 MIN_ANSWER）
# 词重叠地板：**已证伪，默认关闭**。实测（dev 105 题）top-1 正确/错误的覆盖率中位
# 0.71 / 0.60，错答里 22/31 也 ≥0.5，AUC 0.483（不如随机）；在"域内但无政策"的题上
# 方向还是反的。关掉它只丢 2 条正确拒答，换来不再误伤长题干。详见 docs/ABSTENTION.md §10。
MIN_ANSWER = 0.0
# 候选覆盖率过滤：**已证伪，默认关闭**。保留参数只为了能复现当时的实验。
MIN_CAND_COV = 0.0
MIN_SCORE_STRONG = 5.3   # 分数地板：低于它**且**领先第二名不多，才不敢下结论
MIN_MARGIN_STRONG = 1.25  # 与第二名的分差；领先明显时，绝对分低也敢答
# 为什么改成"两个条件同时成立"才怯：绝对分会随问句变长、随改写扩展词而整体抬高或压低，
# 单独用它当判据，等于把"问句怎么写"当成"答案有多可靠"。分差对这类整体平移不敏感。
# 标定过程：在 dev（95 条）上网格搜索 (a,b)，约束是"域内无政策 12 条最多错 1 条、
# 域外 30 条错 0 条"；a=5.3/b=1.25 时 dev hit@1 56.8%→61.1%、口语 31.4%→35.3%，
# 负例只多错 0 条（唯一的 MISS-005 在旧规则下也错）。

# 召回模式：bm25（默认）/ dense / hybrid（BM25 + 稠密，RRF 融合）。
# 默认不开混合：dev 上 +2~5pp 且配对检验不显著（p=0.267），代价是单查询 30–70ms
# （BM25 是 2ms）与 95 MB 模型。开关存在是为了让"换召回方式"可复现，
# 而不是假装它一定更好——见 docs/HYBRID.md。
RECALL = os.environ.get("CLAUSELEDGER_RECALL", "hybrid").lower()

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
    # 2026-11-15：dev 上有 8 条"人事人才"类问题被这道闸误拦（问的是职称评审、
    # 学徒制、企业划型），词表里一个都不沾。补的是业务词，不是同义词——
    # 同义词（生娃→生育）属于召回层的事，这里只回答"这问题归不归我们管"。
    " 评审 申报 学徒 划型 核定 备案 经办 总量 调控 大中型"
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
# 词表在 data/fixtures/lexicon.json（见 app/retrieval/lexicon.py）
QUESTION_WORDS = lexicon.question_words()


@dataclass
class Answer:
    """一次检索的完整结果。拒答也是结果，必须说清"查了什么、查了多少"。"""
    hits: list[Hit]
    reason: str = ""          # 非空表示拒答
    kind: str = "ok"          # ok | not_covered | out_of_domain | insufficient | no_direct_clause
    n_docs: int = 0           # 本次实际检索覆盖的文档数
    n_candidates: int = 0     # 闸门之前召回并通过分数线的候选数
    # 拒答必须能自证"我查过"：没查就说"没有这条政策"，是本系统最不能犯的错。
    searched: dict = field(default_factory=dict)
    # 已知存在、但我们没拿到的文件——用它把"查不到"和"没有"分开说。
    gap: dict | None = None
    as_of: str = ""           # 效力判定的基准日（可能来自提问里的年份）
    # high / low：low 表示"没有把握说这条就是答案"。no_direct_clause 时恒为 low。
    confidence: str = "high"
    # 置信度原值（0–1）。只用于评测与调参，不对外展示。
    conf_score: float | None = None

    @property
    def refused(self) -> bool:
        return bool(self.reason)

    @property
    def outcome(self) -> str:
        """结局分类。报告按类别分开计数，不合并成一个"拒答率"。

        no_direct_clause 是"查到了候选、但没有一条能当答案"——它既不是拒绝检索，
        也不是回答，所以单列。强制把它归进 answer 会高估准确率，归进 refused
        又会掩盖"我们其实找到了可能相关的文件"。
        """
        return "answer" if self.kind == "ok" else self.kind


@dataclass
class Hit:
    clause: Clause
    score: float
    coverage: float = 0.0   # 查询词有多少比例真的出现在这条条款里
    versions: int = 1       # 同名文件的历史版本数（含本条）
    child: str = ""         # 父子索引里命中的那个子块（生成时优先用它，省上下文）
    stale: str = ""         # 非空 = 该文件在 as_of 那天已被废止（引用要带上这句话）
    _in_year: bool = False  # 仅排序用：能否确认在该年份有效
    related: list[dict] = field(default_factory=list)
    # 同一份文件里、按"回答类型 + 词重合"挑出的补充条款（最多 2 条）。
    # 用户看到的是"一个答案 + 这个文件里相关的几句"，不是 5 个文件各一句。
    scope: str = ""         # 带年份提问时的效力口径（"2020 年内有效" / "2020 年不在有效期内"）
    _rank: float = 0.0      # 排序用：分数 × 效力加成


_REPEALED: dict[str, str] = {}
_REPEAL_SRC: set[str] = set()
_BY_DAY: dict[str, dict[str, str]] = {}
_DOCS_BY_NO: dict[str, dict] = {}


def _repealed_as_of(as_of: str, rels: dict[str, list[dict]]) -> dict[str, str]:
    """在 as_of 那天已生效的废止关系：doc_no -> 理由。"""
    out: dict[str, str] = {}
    for no, edges in rels.items():
        for e in edges:
            since = e.get("effective_date")
            if since and since <= as_of:
                out[no] = f"被《{e['source']}》废止（{since} 起）"
                break
    return out


def docs_meta() -> dict[str, dict]:
    """doc_no -> {effective_from, effective_to}：判"那一年是否有"用，不读全文。"""
    global _DOCS_BY_NO
    if _DOCS_BY_NO:
        return _DOCS_BY_NO
    p = INDEX / "docs_meta.json"
    if p.exists():
        _DOCS_BY_NO = json.loads(p.read_text("utf-8"))
    return _DOCS_BY_NO
_META: dict | None = None
_REG: DocRegistry | None = None
REGISTRY = Path("data/registry/docs.jsonl")


def _registry() -> DocRegistry:
    global _REG
    if _REG is None:
        _REG = DocRegistry.load(REGISTRY) if REGISTRY.exists() else DocRegistry.empty()
    return _REG


def _gap(qt: list[str]) -> dict | None:
    e = _registry().best_gap(set(qt))
    return {"doc_no": e.doc_no, "title": e.title, "status": e.status,
            "text": e.explain()} if e else None


def index_meta() -> dict:
    """索引身份：报告里每个数字都要能对上"哪一版索引、哪一版语料"。"""
    global _META
    if _META is None:
        stats = json.loads((INDEX / "stats.json").read_text("utf-8"))
        h = hashlib.sha256((INDEX / "clauses.jsonl").read_bytes()).hexdigest()[:12]
        _META = {
            "index_version": h,
            "docs": stats["docs"], "clauses": stats["clauses"],
            "children": stats.get("children", 0),
            "corpus_snapshot": f"docs={stats['docs']},clauses={stats['clauses']},children={stats.get('children', 0)}",
        }
    return _META


# 提问里的"水分词"：几乎每份文件都出现，出现了也不代表它回答了这个问题。
# 覆盖率的分母如果把它们算进去，问题问得越长、覆盖率越低——**闸门实际上在惩罚"问得具体"**。
# 所以分母只算"有区分度"的词。阈值不动（仍是 0.5），改的是分母。
WATER = lexicon.water()


def content_terms(qs: set[str]) -> set[str]:
    """去掉水分词与单字词后剩下的"实词"。全被滤掉时退回原集合，避免除零。"""
    keep = {t for t in qs if t not in WATER and len(t) > 1}
    return keep or qs


_MODE = "none"
_AGREE: bool | None = None
_DOC_ALPHA = float(os.environ.get("CLAUSELEDGER_DOCAGG", "0") or 0)
_TITLE_W = float(os.environ.get("CLAUSELEDGER_TITLE_W", "0.6") or 0)
_COV_W = float(os.environ.get("CLAUSELEDGER_COV_W", "0") or 0)   # 本次实际用的召回方式；"没检索"就是 none，不许写成 bm25
_RW = None       # 本次命中过的口语改写（写进 searched，供答案展示）


def _searched(n_docs: int, n_queries: int) -> dict:
    d = {**index_meta(), "n_docs": n_docs, "n_queries": n_queries, "recall": _MODE}
    if _AGREE is not None:
        d["agree"] = _AGREE
    if _RW is not None and _RW.hits:
        d["rewrites"] = _RW.hits
    return d



_LOADED: tuple | None = None
_LOADED_KEY: tuple | None = None


def _load() -> tuple[bm25s.BM25, list[Clause], list[dict]]:
    """加载索引。带缓存：BM25 + 6 千条款 + 6 千子块每次重读约 1 秒，
    而检索本身只要 2 毫秒——不缓存会让"混合召回慢"的结论失真。
    键是索引文件的 mtime，重建索引自动失效。"""
    global _LOADED, _LOADED_KEY
    key = (str(INDEX),
           (INDEX / "clauses.jsonl").stat().st_mtime_ns,
           (INDEX / "children.jsonl").stat().st_mtime_ns,
           (INDEX / "bm25").stat().st_mtime_ns)
    if _LOADED is not None and _LOADED_KEY == key:
        return _LOADED
    retriever = bm25s.BM25.load(str(INDEX / "bm25"))
    clauses = [Clause(**json.loads(l)) for l in
               (INDEX / "clauses.jsonl").read_text("utf-8").splitlines() if l.strip()]
    children = [json.loads(l) for l in
                (INDEX / "children.jsonl").read_text("utf-8").splitlines() if l.strip()]
    _LOADED, _LOADED_KEY = (retriever, clauses, children), key
    return _LOADED


def search(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
           min_score: float = MIN_SCORE, min_coverage: float = MIN_COVERAGE,
           min_cand_cov: float = MIN_CAND_COV, min_answer: float = MIN_ANSWER,
           min_score_strong: float = MIN_SCORE_STRONG,
           min_margin_strong: float = MIN_MARGIN_STRONG) -> list[Hit]:
    return search_result(query, k=k, status=status, min_score=min_score,
                         min_answer=min_answer,
                         min_coverage=min_coverage, min_cand_cov=min_cand_cov,
                         min_score_strong=min_score_strong,
                         min_margin_strong=min_margin_strong).hits


def _doc_aggregate(out: list, alpha: float) -> list:
    """按"文件"聚合分数后再排：max + alpha × 其余前三条之和。

    为什么要：同一个问题的答案常常散在一份文件的几条里，每条都只沾一点，
    单条分数打不过别家"恰好全中"的一条。但我们给用户的本来就是一份文件
    （卡片按文件组织），所以应该按文件比，而不是按单条比。
    alpha=0 等于原来的单条排序。
    """
    if alpha <= 0 or len(out) < 2:
        return out
    groups: dict[str, list] = {}
    order: list[str] = []
    for h in out:
        d = h.clause.doc_no
        if d not in groups:
            groups[d] = []
            order.append(d)
        groups[d].append(h)
    scored = []
    for d in order:
        hs = groups[d]
        rs = sorted((h._rank for h in hs), reverse=True)
        s = rs[0] + alpha * sum(rs[1:4])
        scored.append((s, order.index(d), d, hs))
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    flat = []
    for _, __, ___, hs in scored:
        flat.extend(hs)
    return flat


def _answer_type_boost(query: str, out: list[Hit], top_docs: int = 5) -> list[Hit]:
    from app.retrieval import answertype as _at
    atype = _at.detect(query)
    if atype == "default" or len(out) < 2:
        return out
    best: dict[str, float] = {}
    for h in out:
        d = h.clause.doc_no
        best[d] = max(best.get(d, 0.0), h._rank)
    keep = {d for d, _ in sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:top_docs]}
    for h in out:
        if h.clause.doc_no in keep:
            h._rank += _at.bonus(h.clause.text, atype)
    return out



def recall_mode() -> str:
    """实际生效的召回模式。模型/依赖缺失时如实降级，不假装。"""
    if RECALL not in ("bm25", "dense", "hybrid"):
        return "bm25"
    if RECALL == "bm25":
        return "bm25"
    from app.retrieval import dense
    return RECALL if dense.available() else "bm25"


def _candidates(query: str, qt: list[str], retriever, children: list[dict],
                pool: int) -> tuple[dict[int, tuple[float, str]], str, dict]:
    """一路或两路召回，返回 (pid -> (分数, 命中子块正文), 实际模式)。

    分数口径：
    * bm25：原来就是 BM25 分，阈值（3.0 / 6.0）是按它定的。
    * hybrid：RRF 只给名次，分数**按当次最高的 BM25 分线性映射**——
      这样下游阈值语义不变，顺序由融合决定。映射是刻意的：
      两路分数量纲不同（BM25 能到 10+，余弦 0–1），直接相加没有意义。
    * dense：余弦 ×10 粗映射到同一量级，仅作对照用。
    """
    from app.retrieval import dense

    mode = recall_mode()
    idx, scores = retriever.retrieve([qt], k=pool)
    bm = [(int(i), float(s)) for i, s in zip(idx[0], scores[0])]
    paths: dict = {"bm25": [c for c, _ in bm], "dense": None}
    if mode == "bm25":
        ranked = [(cid, s) for cid, s in bm]
    else:
        try:
            dr = [(cid, s) for cid, s in dense.rank(query, children, k=pool)]
        except Exception:
            # 模型没下下来、MPS 不可用、缓存损坏——一律退回 BM25，并把模式如实改回去。
            # 宁可慢一点降级，也不能让一次检索抛异常。
            mode = "bm25"
            ranked = [(cid, s) for cid, s in bm]
            return _to_pids(ranked, children), mode, paths
        paths["dense"] = [c for c, _ in dr]
        if mode == "dense":
            ranked = [(cid, 10.0 * s) for cid, s in dr]
        else:
            fused = dense.rrf([c for c, _ in bm], [c for c, _ in dr])
            top = max((s for _, s in bm), default=0.0) or 1.0
            ceil = fused[0][1] if fused else 1.0
            ranked = [(cid, top * (r / ceil)) for cid, r in fused]

    return _to_pids(ranked, children), mode, paths


def _to_pids(ranked: list[tuple[int, float]],
             children: list[dict]) -> dict[int, tuple[float, str]]:
    """子块 → 父条款：同一 pid 取最高分子块，并记住命中那段的原文。"""
    best: dict[int, tuple[float, str]] = {}
    for cid, s in ranked:
        ch = children[cid]
        pid = int(ch["pid"])
        if pid not in best or s > best[pid][0]:
            best[pid] = (s, ch["text"])
    return best

@dataclass
class Cand:
    """召回层产物：只有检索分和从条款静态算出的量，**没有排序加权**。"""

    clause: Clause
    score: float          # 检索器原始分（BM25，或 RRF 映射后的分）
    child: str            # 命中的子块原文
    coverage: float       # 提问实词在该条款里的覆盖（未四舍五入）
    title_hits: int       # 标题里命中的提问词数


@dataclass
class Pool:
    query: str
    qt: list[str]
    qset: set[str]
    cands: list[Cand]
    clauses: list[Clause]
    mode: str
    paths: dict
    agree: bool | None


def pool(query: str, k: int = 8, *, pool_size: int | None = None) -> Pool:
    """召回层：只回答"有哪些候选"，不负责谁该排前面。

    这里可以换检索器（bm25 / dense / hybrid），也可以带口语改写——
    它们改的都是"拿什么去检索"。但**不加标题权重、不按年份删、不合并版本**，
    否则召回层和排序层就分不开了（指标再也解释不清）。
    """
    global _MODE, _RW, _AGREE
    _MODE = "none"
    _RW = None
    _AGREE = None
    retriever, clauses, children = _load()
    qt = [t for t in tokenize(query) if t not in QUESTION_WORDS]
    if not qt:
        return Pool(query, qt, set(), [], clauses, "none", {}, None)
    # 口语→政策词：只用于检索，不参与覆盖率（否则分母被撑大，闸门反而更严）。
    rw = rewrite.apply(query)
    _RW = rw
    qt_ret = list(qt) + [t for term in rw.terms for t in tokenize(term)
                         if t not in qt and t not in QUESTION_WORDS]
    # 索引建在子块上（父子索引），取回的是子块，用 pid 回填父条款。
    size = pool_size or min(len(children), max(k * 8, 60))
    best, mode, paths = _candidates(query, qt_ret, retriever, children, size)
    _MODE = mode
    # 两路 top-1 是不是同一份文件：这不是分数，是两套视图有没有指向同一个答案。
    agree = None
    try:
        if paths.get("dense"):
            a = clauses[paths["bm25"][0]].doc_no if paths["bm25"] else None
            b = clauses[paths["dense"][0]].doc_no if paths["dense"] else None
            if a and b:
                agree = (a == b)
                _AGREE = agree
    except (IndexError, KeyError):
        agree = None
    qset = set(qt)
    _qt = content_terms(qset)
    cands: list[Cand] = []
    for pid, (score, child_text) in best.items():
        c = clauses[pid]
        cov = len(_qt & set(tokenize(c.text))) / max(1, len(_qt))
        cands.append(Cand(c, score, child_text, cov, len(qset & set(tokenize(c.title)))))
    return Pool(query, qt, qset, cands, clauses, mode, paths, agree)


def filter_candidates(cands: list[Cand], *, status: tuple[str, ...] | None = None,
                      min_score: float = MIN_SCORE,
                      min_cand_cov: float = MIN_CAND_COV) -> list[Cand]:
    """候选过滤：确定性筛子，只删不加权。"""
    out: list[Cand] = []
    for cd in cands:
        if status is not None and cd.clause.status not in status:
            continue
        if cd.score < min_score:
            continue
        # 两级阈值：候选过滤可以松（保住召回），拒答判定必须严（挡住瞎答）
        if cd.coverage < min_cand_cov:
            continue
        out.append(cd)
    return out


def rank_candidates(cands: list[Cand], p: Pool, *, year: int | None,
                    rels: dict) -> list[Hit]:
    """排序层：唯一给候选加权、分层、合并的地方。"""
    from app.domain.validity import in_force

    out: list[Hit] = []
    for cd in cands:
        c = cd.clause
        # 现行有效优先：同年份文件文本高度相似、分数接近，靠状态打破平手
        boost = 1.08 if c.status == "effective" else 1.0
        # 已废止：只标注、不改分——我们还没有可靠的"替代关系"，不该拿分数猜。
        stale = _REPEALED.get(c.doc_no, "")
        scope = ""
        # 带年份提问时先筛后排：那一年明确不在有效期内，直接不进候选。
        # 日期未知的文件不筛（不知道 ≠ 无效），但分层时算低等级（不知道 ≠ 有效）。
        _in_year = False
        if year:
            _m = _DOCS_BY_NO.get(c.doc_no) or {}
            _known = bool(_m.get("effective_from") or _m.get("effective_to"))
            if _known and not in_force(_m, year, rels):
                continue
            _in_year = _known and in_force(_m, year, rels)
            meta = _DOCS_BY_NO.get(c.doc_no)
            if meta is not None:
                if in_force(meta, year, rels):
                    boost *= 1.2
                    stale, scope = "", f"{year} 年内有效"
                else:
                    boost *= 0.8
                    stale = f"{year} 年不在有效期内"
                    scope = f"{year} 年不在有效期内"
        h = Hit(cd.clause, cd.score, round(cd.coverage, 2),
                _rank=cd.score * boost * (1 + _COV_W * cd.coverage)
                + _TITLE_W * cd.title_hits + (0.5 if _in_year else 0.0))
        h.child = cd.child
        h.stale = stale
        h.scope = scope
        h._in_year = _in_year
        out.append(h)
    # 按"回答类型"挑条款：只在已排进前 5 的文件内部加分，不改"是哪个文件"。
    out = _answer_type_boost(p.query, out)
    out.sort(key=lambda h: h._rank, reverse=True)
    # 可选重排是顺序的最终裁决者：重排完把顺序写回 `_rank`（量级不变，只做微小递减），
    # 这样后面的年份分层仍然生效。
    if _rerank.enabled() and len(out) > 1:
        out = _rerank.rerank(p.query, out, top=_rerank.top_n())
        top = max((h._rank for h in out), default=0.0)
        for i, h in enumerate(out):
            h._rank = top - i * 1e-3
    if year:
        out.sort(key=lambda h: (h._in_year, h._rank), reverse=True)
    else:
        out.sort(key=lambda h: h._rank, reverse=True)
    # 文档级聚合必须在最后一次排序之后做，否则会被下一次 sort 覆盖。
    if _DOC_ALPHA > 0:
        out = _doc_aggregate(out, _DOC_ALPHA)
    return out


def decide(p: Pool, out: list[Hit], *, k: int, as_of: str,
           min_answer: float = MIN_ANSWER,
           min_score_strong: float = MIN_SCORE_STRONG,
           min_margin_strong: float = MIN_MARGIN_STRONG) -> Answer:
    """决策层：只回答"敢不敢说"，不改顺序。"""
    n_docs = len({h.clause.doc_no for h in out})
    if not out:
        return Answer([], "没有条款达到相关性下限", "insufficient", n_docs, 0,
                      searched=_searched(n_docs, len(p.qt)), gap=_gap(p.qt), as_of=as_of)
    if min_answer > 0 and out[0].coverage < min_answer:
        return Answer([], f"首条条款只覆盖 {out[0].coverage:.0%} 的提问词", "insufficient",
                      n_docs, len(out), searched=_searched(n_docs, len(p.qt)),
                      gap=_gap(p.qt), as_of=as_of)
    # 覆盖率的残余职责只有两个：拒答地板和展示；"敢不敢下结论"看检索分 + 分差 + 语义。
    margin = out[0].score - (out[1].score if len(out) > 1 else 0.0)
    _cos = _conf.cosine(p.query, out[0].child or out[0].clause.text)
    _rr = getattr(out[0], "_rr", None)
    _rr_p = 1.0 / (1.0 + _pow_e(-_rr)) if _rr is not None else None
    _ff = (_conf.score(out[0].score, margin, _cos, rr=_rr_p)
           if _cos is not None else None)
    if _ff is not None:
        weak = _ff < _conf.threshold(rr=_rr_p is not None)
    else:
        weak = (out[0].score < min_score_strong and margin < min_margin_strong) or \
               (_AGREE is False and out[0].score < 5.0)
    hits = _attach_related(p.query, _prefer_effective(_collapse(out))[:k], p.clauses)
    if weak:
        return Answer(hits,
                      "没有找到直接对应的条款；以下文件与问题共享部分关键词，但可能不是你要的规定",
                      "no_direct_clause", n_docs, len(out),
                      searched=_searched(n_docs, len(p.qt)), as_of=as_of,
                      confidence="low", conf_score=_ff)
    return Answer(hits, "", "ok", n_docs, len(out),
                  searched=_searched(n_docs, len(p.qt)), as_of=as_of,
                  confidence="high", conf_score=_ff)


def search_result(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
                  min_score: float = MIN_SCORE, min_coverage: float = MIN_COVERAGE,
                  min_cand_cov: float = MIN_CAND_COV, min_answer: float = MIN_ANSWER,
                  min_score_strong: float = MIN_SCORE_STRONG,
                  min_margin_strong: float = MIN_MARGIN_STRONG) -> Answer:
    from app.domain.validity import year_of as _year_of

    # 每次调用先清空"上次的召回痕迹"：提前返回（未覆盖/不在域内）时必须写 none，
    # 不能把上一次的 hybrid 留在 searched 里——那等于伪造"查过"。
    global _MODE, _RW, _AGREE
    _MODE = "none"
    _RW = None
    _AGREE = None
    reason = refuse_reason(query)
    _y = _year_of(query)
    _as_of = f"{_y}-12-31" if _y else date.today().isoformat()
    if reason:
        kind = "not_covered" if reason.startswith(("未覆盖", "未接入")) else "out_of_domain"
        # 连索引都没进：n_queries=0。这种结局绝不能是 no_policy（那是"查过没有"）。
        return Answer([], reason, kind, n_docs=0, searched=_searched(0, 0), as_of=_as_of)
    from app.domain.validity import load as _load_rels

    _rels = _load_rels()
    year = _y
    global _REPEALED, _REPEAL_SRC
    if _as_of not in _BY_DAY:
        _BY_DAY[_as_of] = _repealed_as_of(_as_of, _rels)
    _REPEALED = _BY_DAY[_as_of]
    _DOCS_BY_NO.update(docs_meta())
    if not _REPEAL_SRC:
        _REPEAL_SRC = {e["source"] for v in _rels.values() for e in v}
    p = pool(query, k)
    if not p.qt:
        return Answer([], "问题里没有可用于检索的词", "insufficient",
                      searched=_searched(0, 0), as_of=_as_of)
    cands = filter_candidates(p.cands, status=status, min_score=min_score,
                              min_cand_cov=min_cand_cov)
    out = rank_candidates(cands, p, year=year, rels=_rels)
    return decide(p, out, k=k, as_of=_as_of, min_answer=min_answer,
                  min_score_strong=min_score_strong,
                  min_margin_strong=min_margin_strong)


def _status_rank(status: str) -> int:
    return {"effective": 3, "pending": 2, "unknown": 1}.get(status, 0)


def _norm_title(t: str) -> str:
    """同一政策不同年份的文件，标题只差前面的发文机关前缀，砍掉再比。"""
    t = "".join(ch for ch in t if ch not in " \u3000")
    i = t.find("关于")
    if i > 0:
        t = t[i:]
    return t.rstrip("。")


def _attach_related(query: str, hits: list[Hit], clauses: list[Clause],
                    limit: int = 2) -> list[Hit]:
    """给第一名补同文件的条款：数字/日期型问题的答案常常在**同文件另一条**上。

    只用规则（回答类型 + 提问词重合），不引模型；按文档内选择，不跨文件，
    所以不会改变"是哪个文件"这个结论。实测答案级事实覆盖 16/64 → 21/64。
    """
    if not hits:
        return hits
    for h in hits[:3]:
        _related_for(query, h, clauses, limit)
    return hits


def _related_for(query: str, h: Hit, clauses: list[Clause], limit: int = 2) -> None:
    from app.retrieval import answertype as _at
    atype = _at.detect(query)
    qset = set(tokenize(query))
    pool = []
    for c in clauses:
        if c.doc_no != h.clause.doc_no or (c.path, c.sec) == (h.clause.path, h.clause.sec):
            continue
        if not (40 <= len(c.text) <= 600):
            continue
        lex = len(qset & set(tokenize(c.text))) / max(1, len(qset))
        pool.append((_at.bonus(c.text, atype) + 0.4 * lex, c))
    pool.sort(key=lambda x: x[0], reverse=True)
    h.related = [{"path": list(c.path), "page": c.page, "text": c.text, "sec": c.sec}
                 for score, c in pool[:limit] if score > 0]


def _collapse(hits: list[Hit]) -> list[Hit]:
    """同一份文件只留最相关的一条；同名文件（逐年延期的"关于调整…的通知"）
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
            h.versions = 1
            by_title[key] = h
            order.append(key)
        else:
            kept = by_title[key]
            kept.versions += 1
            if (_status_rank(h.clause.status), h.clause.effective_from or "") > \
               (_status_rank(kept.clause.status), kept.clause.effective_from or ""):
                h.versions = kept.versions
                by_title[key] = h
    return [by_title[k] for k in order]


def _family_norm(t: str) -> str:
    """把"关于2018年调整…的通知"里的年份/序号抹掉，用于判同族。"""
    t = _norm_title(t or "")
    t = re.sub(r"20\d{2}|19\d{2}|〔\d{4}〕|第?[一二三四五六七八九十]+次", "", t)
    return t


def _prefer_effective(hits: list[Hit]) -> list[Hit]:
    """同一族里既有失效又有现行时，把现行的提到前面——但不删除任何条目，
    否则会把原本排在第 2、第 3 的正确条款一起弄丢（实测掉 13pp）。"""
    import difflib
    fam = [_family_norm(h.clause.title) for h in hits]
    out = list(hits)
    for i in range(len(out)):
        if _status_rank(out[i].clause.status) >= 3:
            continue
        for j in range(len(out)):
            if i == j or _status_rank(out[j].clause.status) < 3:
                continue
            if difflib.SequenceMatcher(None, fam[i], fam[j]).ratio() >= 0.75:
                out.insert(i, out.pop(j))
                fam.insert(i, fam.pop(j))
                break
    return out


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
