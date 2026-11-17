"""补两个字段：发文机关（agency）与印发日期（published_at）。

为什么单列一个模块：
- `issued_at` 是**成文日期**（落款），`published_at` 是**印发/发布日期**（版记或列表页），
  两者经常差几天到几周，混用会让"这份文件当时生效了吗"算错。
- `agency` 现在一个都没抽，导致答案里永远说不出"谁发的"。

抽取顺序都是"先严后松"，宁可留空也不猜：
    agency：标题里「…关于」之前 → 版头（红头）→ 落款 → 空
    published_at：「X 年 X 月 X 日印发」→ 详情页「发布时间：」→ 列表页日期（meta_hint）→ 空
"""
from __future__ import annotations

import re

RE_DATE_CN = re.compile(r"(19\d{2}|20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_PRINT = re.compile(
    r"((?:19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*印发")
RE_PUBLISHED = re.compile(r"发布时间[:：]\s*((?:19|20)\d{2}-\d{1,2}-\d{1,2})")
# 机关名：以"局/委/办/会/政府/中心/院/署/部/厅"结尾，且长度 4–40
RE_ORG_TAIL = re.compile(r"^[\u4e00-\u9fa5（）()\s]{4,60}?"
                         r"(?:局|委员会|办公室|人民政府|政府|联合会|管理中心|中心|院|署|部|厅)$")
RE_ORG_ANY = re.compile(r"[\u4e00-\u9fa5]{2,30}(?:局|委员会|办公室|人民政府|联合会|管理中心|中心)")
RE_NOT_ORG = re.compile(r"(关于|通知|办法|规定|意见|方案|细则|目录|名单|附件|解读|设在|同意|转发|印发)")
# 主送机关（"各区人力资源和社会保障局："）长得和发文机关一模一样，必须排除
# 只看开头：主送机关块一定是"各区…""各委…"开头；
# 结尾带冒号不能作为判据（发文机关+文号+标题+主送机关常被压成同一块）
RE_ADDRESSEE = re.compile(r"^(各|市各|区各|县各|有关)")
# 正文块也可能以"经市人民政府同意…"开头，被误当机关名
RE_BODY = re.compile(r"^(经|根据|按照|现|为|对|在|依据|参照|一、|（|\()")
# 版头常常把几个机关名和文号挤成一块（"…局…局…局沪人社规〔2023〕20号"），
# 所以要能**连续**吃掉开头的机关名。
RE_LEAD_ORG = re.compile(
    r"^(?:上海市|上海|国家|中共)?[\u4e00-\u9fa5]{2,18}?"
    r"(?:局|委员会|办公室|人民政府|总工会|联合会|管理中心|中心)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _merge_orgs(orgs: list[str]) -> list[str]:
    """「国家税务总局」+「上海市税务局」应合成一个机关。"""
    out: list[str] = []
    for o in orgs:
        if out and o.startswith("上海市") and out[-1] in ("国家税务总局", "国家统计局", "国家医保局"):
            out[-1] = out[-1] + o
        else:
            out.append(o)
    return out


def _lead_orgs(text: str) -> list[str]:
    """从一块文本的开头，连续取机关名（处理"多个机关+文号挤在一行"的版头）。"""
    rest, out = text, []
    for _ in range(6):
        m = RE_LEAD_ORG.match(rest)
        if not m:
            break
        one = m.group(0)
        if len(one) > 22 or "、" in one:
            break
        out.append(one)
        rest = rest[m.end():]
        rest = re.sub(r"^(?:等[一二三四五六七八九十\d]{0,3}(?:部门|单位))?", "", rest)
    return out


# 文号前缀 → 主管部门。只用于"标题、版头、落款都抽不到"时兜底，
# 且记录来源为 docno（推断），因为它可能是联合发文中的一个。
DOCNO_AGENCY = [
    ("沪人社", "上海市人力资源和社会保障局"),
    ("沪教委", "上海市教育委员会"),
    ("沪府", "上海市人民政府"),
    ("沪医保", "上海市医疗保障局"),
    ("沪住建", "上海市住房和城乡建设管理委员会"),
    ("沪公积金", "上海市公积金管理中心"),
    ("沪公", "上海市公安局"),
    ("沪劳保", "上海市劳动和社会保障局"),
    ("沪劳", "上海市劳动局"),
]


def _from_docno(doc_no: str) -> str | None:
    for pre, org in DOCNO_AGENCY:
        if doc_no.startswith(pre):
            return org
    return None


def agency_of(title: str, blocks: list[dict], doc_no: str = "") -> tuple[str | None, str]:
    """返回 (机关名, 来源)。多个机关用「、」连接；抽不到返回 (None, "unknown")。"""
    t = _norm(title)
    if "关于" in t:
        head = t.split("关于", 1)[0]
        if 6 <= len(head) <= 80 and RE_ORG_TAIL.match(head) and not RE_NOT_ORG.search(head):
            # 联合发文在标题里常以空格分隔："上海市人力资源和社会保障局 上海市财政局关于…"
            parts = [p for p in re.split(r"(?<=局|委|会|部|厅)(?=上海市|上海)", head) if p]
            return "、".join(parts), "title"

    # 版头：前 6 块的开头可能连续排列多个机关（最常见的情况）
    for b in blocks[:6]:
        raw = (b.get("text") or "").strip()
        # 短块 + 以冒号结尾 = 主送机关（"市社会保险事业管理中心，各区…局："）；
        # 长块常是"机关名+文号+标题+主送机关"压成一块，不能整块丢
        if raw.endswith(("：", ":")) and len(raw) <= 60:
            continue
        if RE_ADDRESSEE.search(raw) or RE_BODY.search(raw):
            continue
        orgs = _lead_orgs(_norm(raw))
        if orgs:
            return "、".join(dict.fromkeys(_merge_orgs(orgs))), "head"

    # 版头（红头）：前 8 块里像机关名的整行
    for b in blocks[:8]:
        line = _norm(b.get("text", ""))
        if 6 <= len(line) <= 60 and RE_ORG_TAIL.match(line) and not RE_NOT_ORG.search(line):
            return line, "head"

    # 落款：最后 20 块里像机关名的行（取连续的那几行）
    found: list[str] = []
    for b in blocks[-20:]:
        line = _norm(b.get("text", ""))
        if 6 <= len(line) <= 60 and RE_ORG_TAIL.match(line) and not RE_NOT_ORG.search(line):
            found.append(line)
    if found:
        return "、".join(dict.fromkeys(found[:4])), "sign"

    org = _from_docno(doc_no)
    if org:
        return org, "docno"
    return None, "unknown"


def published_at_of(blocks: list[dict], hint: dict | None,
                    meta: dict | None = None) -> tuple[str | None, str]:
    """印发/发布日期。顺序：版记「印发」> 页面 <meta PubDate> > 页面"发布时间" > 列表页。"""
    text = "\n".join(b.get("text", "") for b in blocks)
    m = RE_PRINT.search(text)
    if m:
        d = RE_DATE_CN.search(m.group(1))
        if d:
            return f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}", "print"
    pubdate = ((meta or {}).get("pubdate") or "").strip()
    md = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", pubdate)
    if md:
        return f"{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}", "pubdate"
    m = RE_PUBLISHED.search(text)
    if m:
        y, mo, dd = m.group(1).split("-")
        return f"{y}-{int(mo):02d}-{int(dd):02d}", "page"
    hint_date = (hint or {}).get("issued_at") or ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", hint_date):
        return hint_date, "list"
    return None, "unknown"
