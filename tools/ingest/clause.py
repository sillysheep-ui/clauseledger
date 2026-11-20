"""条款切分：四套编号体系，由粗到细。

政策文本的编号没有统一标准：规范性文件多用「第一条」，通知类多用「一、（一）1.」。
只认一种就会产生假阴性——M0 用「第X条」扫上海文件得到 0 条，实际有 29 条。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tools.ingest.parse import Block

NUM = "一二三四五六七八九十百零〇两"
LEVELS: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(rf"^第[{NUM}]+章")),
    (2, re.compile(rf"^第[{NUM}]+节")),
    (3, re.compile(rf"^第[{NUM}]+条")),
    (4, re.compile(rf"^[{NUM}]+、")),
    (5, re.compile(rf"^[（(][{NUM}]+[）)]")),
    (6, re.compile(r"^\d{1,2}[.．、](?!\d)")),
    (7, re.compile(r"^[（(]\d{1,2}[）)]")),
    # 2026-11-14 补：⑴⑵⑶ 这类圈码、全角句点「1．」、阿拉伯「第1条」
    # 实测漏切 376 行「1．」+18 行「⑴」+少量「第1条」——它们被并进上一条，条款粒度直接变粗
    (7, re.compile(r"^[⑴-⒇]")),
    (3, re.compile(r"^第\s*\d{1,3}\s*[条章节]")),
]


# 页面上"不是正文"的行：附件下载区、印发说明、公开属性。
# 实测两处伤到边界：
#   沪人社规〔2026〕14号 block 25「下载附件 1.企业裁减…doc 2.…doc」被切成条款
#   沪人社规〔2021〕4号 block 50「2021年2月8日印发」被并进最后一条
NOISE = re.compile(
    r"^(?:下载附件|附件下载|打印本页|关闭窗口|（此件主动公开）)"
    r"|^\d{4}年\d{1,2}月\d{1,2}日(?:印发)?$"
    r"|办公室\d{4}年\d{1,2}月\d{1,2}日印发$"
    r"|^\d{1,2}[.．][^。；]{0,60}\.(?:docx?|pdf|xlsx?)$",
    re.I,
)


# 只出现在文末的落款/成文信息：机关名独立成行、日期独立成行。
# 实测：沪人社规〔2021〕4号 的最后一条把「上海市人力资源和社会保障局办公室」也吞进去了。
TAIL_NOISE = re.compile(
    r"^[\u4e00-\u9fa5（）()]{4,30}(?:局|委员会|办公室|人民政府|厅|部)$"
    r"|^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:印发)?$"
    r"|^[一二三四五六七八九十○〇]{4}\s*年\s*[一二三四五六七八九十]{1,3}\s*月\s*[一二三四五六七八九十]{1,3}\s*日$"
    r"|办公室\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日印发$"
    r"|^抄送[:：]|印发$|（此件主动公开）"
)


def _noise(line: str) -> bool:
    return bool(NOISE.search(line.strip()))


# 落款常常和正文同一行：「…以本通知为准。上海市人力资源和社会保障局2017年3月31日」
# 也可能多机关 + 中文数字日期：「…上海市财政局上海市金融服务办公室二○一○年十二月六日」
SIGN = re.compile(
    r"(?:上海市|上海)?[\u4e00-\u9fa5]{2,20}(?:局|委员会|办公室|联合会)\s*"
    r"(?:\d{4}年\d{1,2}月\d{1,2}日|[一二三四五六七八九十○〇]{4}年"
    r"[一二三四五六七八九十]{1,3}月[一二三四五六七八九十]{1,3}日)"
)
# 抄送、公开属性、文末孤立日期：都不是正文
CUT = re.compile(r"抄送[:：]|（此件主动公开）|^\s*\d{4}年\d{1,2}月\d{1,2}日\s*$", re.M)


def _cut_tail(text: str, doc_no: str = "") -> str:
    """最后一条：把粘在正文里的落款、或"整篇又贴了一遍"的重复头部切掉。"""
    cut = len(text)
    m = CUT.search(text)
    if m and m.start() > len(text) * 0.4:
        cut = min(cut, m.start())
    m = SIGN.search(text)
    if m and m.start() > len(text) * 0.4:
        cut = min(cut, m.start())
    if doc_no:
        i = text.find(doc_no, 1)
        # 整篇又贴一遍时，文号会再次出现，后面紧跟"现将…通知如下"
        if i >= 8 and ("通知如下" in text[i:i + 400] or "现将" in text[i:i + 400]):
            cut = min(cut, i)
    text = text[:cut].rstrip()
    text = re.sub(r"[，、；;]?\s*\d{4}年\d{1,2}月\d{1,2}日\s*$", "", text)
    return text.rstrip()


def _trim_tail(rows: list[str]) -> list[str]:
    """去掉正文末尾的落款行（只影响最后一条，别的地方不动）。"""
    while rows and TAIL_NOISE.search(rows[-1].strip()):
        rows.pop()
    return rows


@dataclass
class Clause:
    path: str
    level: int
    label: str
    text: str
    block_index: int
    char_offset: int
    page: int | None
    start: int
    end: int
    sec: int = 0        # 同一 (part, path) 的第几次出现（附录尤其多）
    carrier: str = "page"  # 来自哪个载体：page / att1 / att2…（引用坐标靠它）
    part: int = 0       # 第几份文档：0=正文，1..=第 N 个附件/附录
    # 一份文件常装着多份文档（通知 + 附件里的《办法》《标准》），各自从「一、」重新编号。
    # 不给它们分家，父条款会认领到另一份文档的子条款（实测 15 份文档 I2/I3 违规）。


# 「本通知第一条第二款…」在 PDF 里换行后行首长得像标题，其实是被引用的条款
CROSSREF = re.compile(rf"^第[{NUM}\d]{{1,4}}\s*[条章节]\s*第")


# 附录/附表/附件：正文之外的一整片名单，没有编号，不切开就会把最后一条撑到上万字
APPENDIX = re.compile(r"^(附\s*录|附\s*表|附\s*件)\s*([0-9一二三四五六七八九十]{1,3})?$")
APP_MAX = 600  # 附录内每块的字数上限


def _is_appendix(txt: str, i: int, total: int) -> bool:
    """独立的"附件N"行且**位于文档后半段**才算附录。

    正文里也会出现独立的"附件3"（附件清单、表格标题），一命中就把后面的正文
    全当成附录块——2026-11 实测把 41 条正文条款错标成「附件3」。
    """
    t = txt.strip()
    return bool(APPENDIX.match(t)) and len(t) <= 12 and i >= int(total * 0.6)


def _match(line: str) -> tuple[int, str] | None:
    s = line.strip()
    if CROSSREF.match(s):
        return None
    for level, pat in LEVELS:
        if pat.match(s):
            m = pat.match(s)
            return level, (m.group(0) if m else s[:8])
    return None


def split_clauses(blocks: list[Block], doc_no: str = "") -> list[Clause]:
    # 1) 行级索引 + 全局偏移（引用定位要能回到原文的字符位置）
    lines: list[tuple[int, int, str, int | None]] = []  # block_idx, offset_in_block, text, page
    for b in blocks:
        for ln in b.text.split("\n"):
            if ln.strip() and not _noise(ln):
                lines.append((b.index, 0, ln, b.page))
    while lines and TAIL_NOISE.search(lines[-1][2].strip()):
        lines.pop()

    # 每行属于第几份文档：独立的「附件N / 附录N」行开新的一份
    part_of, cur = [], 0
    for (_, _, txt, _) in lines:
        t = txt.strip()
        if APPENDIX.match(t) and len(t) <= 12:
            cur += 1
        part_of.append(cur)

    pos, cursor = [], 0
    for _i, (_, _, txt, _) in enumerate(lines):
        pos.append(cursor)
        cursor += len(txt) + 1

    heads: list[tuple[int, int, str, int]] = []  # line_idx, level, label, offset
    for i, (_, _, txt, _) in enumerate(lines):
        am = APPENDIX.match(txt.strip())
        if _is_appendix(txt, i, len(lines)):  # 附录也是一级段，否则整片名单会并进上一条
            heads.append((i, 1, f"{am.group(1).replace(' ', '')}{am.group(2) or ''}", pos[i]))
            continue
        m = _match(txt)
        if m:
            heads.append((i, m[0], m[1], pos[i]))

    out: list[Clause] = []
    stack: list[tuple[int, str]] = []
    app_seen: dict[str, int] = {}
    for k, (i, level, label, off) in enumerate(heads):
        am = APPENDIX.match(lines[i][2].strip())
        if _is_appendix(lines[i][2], i, len(lines)):
            end = len(lines)
            for j, lvl, _, _ in heads[k + 1:]:
                if lvl <= 1:
                    end = j
                    break
            name = f"{am.group(1).replace(' ', '')}{am.group(2) or ''}"
            buf, s0 = [], pos[i]
            for t in range(i, end):
                txt = lines[t][2]
                if buf and sum(len(x) + 1 for x in buf) + len(txt) > APP_MAX:
                    n = app_seen.get(name, 0); app_seen[name] = n + 1
                    out.append(Clause(path=name, level=1, label=name, text="\n".join(buf),
                                      block_index=lines[i][0], char_offset=0, page=lines[t - 1][3],
                                      start=s0, end=pos[t - 1] + len(lines[t - 1][2]), sec=n,
                                      part=part_of[i]))
                    buf, s0 = [], pos[t]
                buf.append(txt)
            if buf:
                n = app_seen.get(name, 0); app_seen[name] = n + 1
                out.append(Clause(path=name, level=1, label=name, text="\n".join(buf),
                                  block_index=lines[i][0], char_offset=0, page=lines[end - 1][3],
                                  start=s0, end=pos[end - 1] + len(lines[end - 1][2]), sec=n,
                                  part=part_of[i]))
            continue
        end_line = len(lines)
        for j, lvl, _, _ in heads[k + 1:]:
            if lvl <= level:
                end_line = j
                break
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, label))
        path = "/".join(lb for _, lb in stack)
        seg = [lines[t][2] for t in range(i, end_line)]
        if end_line == len(lines):  # 最后一条：剥掉落款 / 重复头部
            body = _cut_tail("\n".join(_trim_tail(seg)), doc_no)
        else:
            body = "\n".join(seg)
        bidx, _, _, page = lines[i]
        out.append(Clause(path=path, level=level, label=label, text=body,
                          block_index=bidx, char_offset=lines[i][1], page=page,
                          start=off, end=pos[end_line - 1] + len(lines[end_line - 1][2]),
                          part=part_of[i]))
    seen: dict[tuple[int, str], int] = {}
    for c in out:  # 只在同一份文档内计数：正文的「一、」与附件《办法》的「一、」是两回事
        key = (c.part, c.path)
        n = seen.get(key, 0)
        c.sec = n
        seen[key] = n + 1
    return out
