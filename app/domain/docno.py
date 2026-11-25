"""文号归一化：全库只有这一个实现。

为什么不能只做"去空格 + 括号统一"：
  - 政府文件里同一份文件会写成 `沪人社养发(2011)66号`、`沪人社养发（2011）66号`、
    `沪人社养发〔2011〕66 号`，括号与空格不统一；
  - 2010 年以前的旧文号用两位年份：`沪劳资发〔87〕130号`（=1987）、`沪劳保发〔95〕83号`；
  - 有的表头会带 `第`：`沪人社规〔2021〕第26号`。

所以匹配用的键 = 去全部空白 + 括号统一为〔〕 + 去「第」 + 两位年份补成四位。
展示时仍用原文，归一化只用于**对键**，不写回 `doc_no`。
"""
from __future__ import annotations

import re

_YEAR2 = re.compile(r"〔\s*(\d{2})\s*〕")


def key(no: str) -> str:
    """文号 → 匹配键。幂等：key(key(x)) == key(x)。"""
    t = re.sub(r"\s+", "", no or "")
    t = (t.replace("（", "〔").replace("）", "〕")
          .replace("(", "〔").replace(")", "〕")
          .replace("[", "〔").replace("]", "〕"))
    t = t.replace("第", "")

    def _year(m: re.Match[str]) -> str:
        yy = int(m.group(1))
        # 行政机关旧文号：〔87〕=1987、〔04〕=2004。三十年为界，避免把〔26〕判成 1926。
        return f"〔19{yy:02d}〕" if yy >= 30 else f"〔20{yy:02d}〕"

    return _YEAR2.sub(_year, t)


def same(a: str, b: str) -> bool:
    """两个文号是不是同一份文件。"""
    return bool(a) and bool(b) and key(a) == key(b)
