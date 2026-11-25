"""文号归一化体检：多少种写法、键有没有冲突、派生表能不能查到。

    python -m tools.scan.docno_audit
"""
from __future__ import annotations

import collections
import glob
import json
import re

from app.domain import validity as V
from app.domain.docno import key


def main() -> None:
    docs = [json.load(open(f, encoding="utf-8")) for f in glob.glob("data/processed/*.json")]
    nos = [d.get("doc_no") or "" for d in docs]
    odd = [n for n in nos if re.search(r"[（(]|\s|〔\s*\d{2}\s*〕", n)]
    dup = {k: v for k, v in collections.Counter(key(n) for n in nos).items() if v > 1}

    periods = V._derived().get("periods") or {}
    alias = V._derived().get("alias") or {}
    raw = sum(1 for n in nos if n in periods)
    via_key = sum(1 for n in nos if n not in periods and key(n) in periods)
    via_alias = sum(1 for n in nos if n not in periods and key(n) not in periods
                    and alias.get(n) in periods)
    missing = [n for n in nos if not V.periods_of(n)]

    print(f"文档 {len(nos)}｜非标准写法 {len(odd)}｜键冲突 {len(dup)}")
    print(f"派生表命中：原文 {raw}｜键 {via_key}｜别名 {via_alias}｜查不到 {len(missing)}")
    print("查不到的里面，有到期日/施行日的：",
          sum(1 for d in docs if d.get("doc_no") in missing
              and (d.get("effective_to") or d.get("valid_until") or d.get("effective_from")
                   or d.get("issued_at"))))
    if dup:
        print("冲突样例：", list(dup.items())[:5])


if __name__ == "__main__":
    main()
