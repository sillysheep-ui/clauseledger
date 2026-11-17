"""条款记录：检索的最小单元，也是引用的最小单元。

一条 Clause 必须能回答三个问题：出自哪份文件、在原文的哪个字符区间、现在还有效吗。
前两个决定"可不可信"，第三个决定"能不能拿来下结论"。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

TODAY = date.today().isoformat()


@dataclass
class Clause:
    id: str
    doc_no: str
    title: str
    path: list[str] = field(default_factory=list)
    level: int = 0
    label: str = ""
    text: str = ""
    page: int = 0
    block_index: int = 0
    char_offset: int = 0
    start: int = 0
    end: int = 0
    status: str = "unknown"
    effective_from: str | None = None
    effective_to: str | None = None
    url: str = ""
    parser_version: str = ""
    carrier: str = "page"
    part: int = 0
    extract: str = "native"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def citation(self) -> str:
        """人读的引用串：文号 + 条款路径 + 页码。"""
        p = " / ".join(self.path) if self.path else "正文"
        return f"《{self.doc_no}》{p}" + self.locate

    @property
    def locate(self) -> str:
        """坐标必须跟载体一致：PDF 说页码，网页说第几段，扫描件还要交代识别风险。"""
        if self.page:
            note = "扫描件 OCR" if self.extract == "ocr" else "PDF 原件"
            return f"（第 {self.page} 页 · {note}）"
        if self.block_index:
            return f"（第 {self.block_index} 段 · 网页原文）"
        return ""


def status_of(effective_to: str | None, effective_from: str | None, *, today: str = TODAY) -> str:
    """效力状态。只做机械判定：能确定的说确定，不能确定的就说不知道。

    注意 unknown 与 expired 不同：unknown 是"没查到效力"，不是"已失效"，
    检索时保留、回答时提示，而不是当作废止处理。
    """
    if effective_to and effective_to < today:
        return "expired"
    if effective_from and effective_from > today:
        return "pending"          # 尚未生效，同样不能用来下结论
    return "unknown" if not effective_to else "effective"


RE_NUM_HEAD = re.compile(r"^[（(]?[一二三四五六七八九十百]+[)）、.]")


def is_numbered(text: str) -> bool:
    return bool(RE_NUM_HEAD.match(text.strip()[:8]))
