"""检索：问题 → 条款 + 出处。不做判定，只负责"找到并证明它在哪"。

两个刻意的设计：
1. **返回空是合法结果**。最高分低于阈值时返回 insufficient，而不是硬凑 top-1——
   下游据此给出"证据不足"，而不是拿一段不相干的条文假装回答。
2. **效力状态参与过滤但不隐藏**。判定只允许用 effective；检索可以召回 expired/unknown，
   但必须把状态带回给调用方，由它决定怎么说。
"""
from __future__ import annotations

import hashlib
import re

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import bm25s

from app.domain.registry import DocRegistry
from app.retrieval import rerank as _rerank
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
    # 拒答必须能自证"我查过"：没查就说"没有这条政策"，是本系统最不能犯的错。
    searched: dict = field(default_factory=dict)
    # 已知存在、但我们没拿到的文件——用它把"查不到"和"没有"分开说。
    gap: dict | None = None
    as_of: str = ""           # 效力判定的基准日（可能来自提问里的年份）

    @property
    def refused(self) -> bool:
        return bool(self.reason)

    @property
    def outcome(self) -> str:
        """五类结局之一。报告按这五类分开计数，不合并成一个"拒答率"。"""
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
WATER = set("""
的 了 是 有 在 和 与 或 及 等 为 对 从 到 把 被 让 给 向 于 就 都 也 还 又 很 更 最 不 没 没有
多少 哪些 哪个 怎么 怎样 什么 如何 为什么 是否 可以 能否 需要 应当 应该 请问 一下 一般 通常
本市 上海 上海市 文件 规定 办法 通知 意见 有关 关于 进行 相关 以上 以下 其他 以及 根据 按照
对于 其中 目前 现在 今年 去年 明年 期间 时候 时间 情况 问题 内容 方面 部分 有关
""".split())


def content_terms(qs: set[str]) -> set[str]:
    """去掉水分词与单字词后剩下的"实词"。全被滤掉时退回原集合，避免除零。"""
    keep = {t for t in qs if t not in WATER and len(t) > 1}
    return keep or qs


def _searched(n_docs: int, n_queries: int) -> dict:
    return {**index_meta(), "n_docs": n_docs, "n_queries": n_queries}


def _load() -> tuple[bm25s.BM25, list[Clause], list[dict]]:
    retriever = bm25s.BM25.load(str(INDEX / "bm25"))
    clauses = [Clause(**json.loads(l)) for l in
               (INDEX / "clauses.jsonl").read_text("utf-8").splitlines() if l.strip()]
    children = [json.loads(l) for l in
                (INDEX / "children.jsonl").read_text("utf-8").splitlines() if l.strip()]
    return retriever, clauses, children


def search(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
           min_score: float = MIN_SCORE, min_coverage: float = MIN_COVERAGE,
           min_cand_cov: float = 0.25) -> list[Hit]:
    return search_result(query, k=k, status=status, min_score=min_score,
                         min_coverage=min_coverage, min_cand_cov=min_cand_cov).hits


def search_result(query: str, k: int = 8, *, status: tuple[str, ...] | None = None,
                  min_score: float = MIN_SCORE, min_coverage: float = MIN_COVERAGE,
                  min_cand_cov: float = 0.25) -> Answer:
    from app.domain.validity import year_of as _year_of
    _y = _year_of(query)
    _as_of = f"{_y}-12-31" if _y else date.today().isoformat()
    reason = refuse_reason(query)
    if reason:
        kind = "not_covered" if reason.startswith(("未覆盖", "未接入")) else "out_of_domain"
        # 连索引都没进：n_queries=0。这种结局绝不能是 no_policy（那是"查过没有"）。
        return Answer([], reason, kind, n_docs=0, searched=_searched(0, 0), as_of=_as_of)
    retriever, clauses, children = _load()
    from app.domain.validity import in_force, load as _load_rels
    _rels = _load_rels()
    year, as_of = _y, _as_of
    global _REPEALED, _REPEAL_SRC
    if as_of not in _BY_DAY:
        _BY_DAY[as_of] = _repealed_as_of(as_of, _rels)
    _REPEALED = _BY_DAY[as_of]
    _DOCS_BY_NO.update(docs_meta())
    if not _REPEAL_SRC:
        _REPEAL_SRC = {e["source"] for v in _rels.values() for e in v}
    qt = [t for t in tokenize(query) if t not in QUESTION_WORDS]
    if not qt:
        return Answer([], "问题里没有可用于检索的词", "insufficient",
                      searched=_searched(0, 0), as_of=_as_of)
    # 索引建在子块上（父子索引），取回的是子块，用 pid 回填父条款。
    # 子块数远多于条款数，候选要相对多取一些，否则去重后剩不下几条。
    idx, scores = retriever.retrieve([qt], k=min(len(children), max(k * 8, 60)))
    best: dict[int, tuple[float, str]] = {}   # pid -> (最高子块分, 命中子块正文)
    for i, s in zip(idx[0], scores[0]):
        ch = children[int(i)]
        pid = int(ch["pid"])
        if pid not in best or float(s) > best[pid][0]:
            best[pid] = (float(s), ch["text"])
    out: list[Hit] = []
    qset = set(qt)
    scope = ""
    _out_of_year = 0
    for pid, (score, child_text) in best.items():
        c = clauses[pid]
        if status is not None and c.status not in status:
            continue
        if score < min_score:
            continue
        # 覆盖率算在**父条款**上：回填之后我们看到的是父条款，用子块的覆盖率
        # 会低估（子块只是父条款的一段）。代价是长条款的覆盖率仍然偏乐观——
        # 这是已知偏差，记在 docs/EVAL.md 的局限里。
        _qt = content_terms(qset)
        cov = len(_qt & set(tokenize(c.text))) / max(1, len(_qt))
        # 两级阈值：候选过滤可以松（保住召回），拒答判定必须严（挡住瞎答）
        if cov < min_cand_cov:
            continue
        # 现行有效优先：同年份文件文本高度相似、分数接近，靠状态打破平手
        boost = 1.08 if c.status == "effective" else 1.0
        # 已废止的文件：不是删掉，是降权并留痕——用户仍可能问"当年的规定"，
        # 但系统必须知道它现在不作数。实测 9 道题就是被这份"看起来仍有效"的文件带偏的。
        # 已废止：**只标注、不改分**。实测降权 0.5 让 hit@1 55.3%→54.3%、
        # 提权废止源 1.3 让 hit@3 70%→65%，两种都更差——说明我们还没有可靠的
        # "替代关系"，不该拿分数去猜。标注进引用，让人看见。
        stale = _REPEALED.get(c.doc_no, "")
        # 带年份提问时**先筛后排**：那一年明确不在有效期内的，直接不进候选。
        # 只做加权（1.2/0.8）实测无效——同主题跨年文件的文本几乎逐字相同，
        # BM25 分差远大于 20%，加权改不动顺序；而"问 2019 年却拿 2026 年的文件答"
        # 不是排序问题，是**选错了年份**，只能靠过滤。
        # 日期未知（unknown）的文件不筛：不知道 ≠ 无效。
        _in_year = False
        if year:
            _m = _DOCS_BY_NO.get(c.doc_no) or {}
            # "日期未知"既不能证明有效、也不能证明无效：
            # 过滤时放过（不知道 ≠ 无效），分层时算低等级（不知道 ≠ 有效）。
            _known = bool(_m.get("effective_from") or _m.get("effective_to"))
            if _known and not in_force(_m, year, _rels):
                _out_of_year += 1
                continue
            _in_year = _known and in_force(_m, year, _rels)
        # 提问里带年份时，改判"那一年有没有效"：
        # 问 2020 年的规定，不能拿 2026 年的文件答，也不该把当年有效的旧文件标成"已作废"。
        if year:
            meta = _DOCS_BY_NO.get(c.doc_no)
            if meta is not None:
                if in_force(meta, year, _rels):
                    boost *= 1.2
                    stale, scope = "", f"{year} 年内有效"
                else:
                    boost *= 0.8
                    stale, scope = f"{year} 年不在有效期内", f"{year} 年不在有效期内"
        # 标题命中加权：用户问的是"这件事"，文件标题写着这件事，比正文里顺带提一句更有分量
        title_hits = len(qset & set(tokenize(c.title)))
        h = Hit(c, score, round(cov, 2),
                _rank=score * boost + 0.6 * title_hits + (0.5 if _in_year else 0.0))
        h.child = child_text   # 命中的具体那一段，生成时优先用它
        h.stale = stale
        h.scope = scope
        h._in_year = _in_year
        out.append(h)
    # 可选重排：必须在 _collapse 之前——条款级才有足够信号，
    # 压成"每份文件一条"再重排会把 hit@1 从 58.7% 打到 41.3%（见 docs/RETRIEVAL-LIT.md）。
    if _rerank.enabled() and len(out) > 1:
        out = _rerank.rerank(query, out, top=_rerank.top_n())
    # 带年份时先按"能否确认那一年有效"分层，再按分数排。
    # 理由：日期未知 ≠ 有效。用户既然说了年份，就该先相信能证明自己那年有效的文件；
    # 只在同一层里比相关性。不带年份时这一层不存在，排序与以前完全一致。
    if year:
        out.sort(key=lambda h: (h._in_year, h._rank), reverse=True)
    else:
        out.sort(key=lambda h: h._rank, reverse=True)
    n_docs = len({h.clause.doc_no for h in out})
    if not out:
        return Answer([], "没有条款达到相关性下限", "insufficient", n_docs, 0,
                      searched=_searched(n_docs, len(qt)), gap=_gap(qt), as_of=_as_of)
    if out[0].coverage < min_coverage:  # 2026-11-15：此前这里写死常量，传参等于没传
        return Answer([], f"首条条款只覆盖 {out[0].coverage:.0%} 的提问词", "insufficient",
                      n_docs, len(out), searched=_searched(n_docs, len(qt)), gap=_gap(qt),
                      as_of=_as_of)
    return Answer(_prefer_effective(_collapse(out))[:k], "", "ok", n_docs, len(out),
                  searched=_searched(n_docs, len(qt)), as_of=_as_of)


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
