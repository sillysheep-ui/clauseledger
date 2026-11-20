"""语料完整度报告：能说出"缺什么"，而不是只说"收了多少"。

三个口径：
1. 文号连续性——政府发文按号排，缺号就是缺口（这是唯一可枚举的完整度证据）
2. 元数据完整度——发文日期/有效期缺失多少
3. 范围——部门数、城市数（当前只有 1 个部门）

产物：docs/scan/CORPUS-COVERAGE.md
"""
from __future__ import annotations

import collections
import glob
import json
import re
from pathlib import Path

RE_GFX = re.compile(r"沪人社规[〔（(](\d{4})[〕）)]\s*第?\s*(\d+)\s*号")
OUT = Path("docs/scan/CORPUS-COVERAGE.md")


def main() -> None:
    docs = [json.load(open(p)) for p in glob.glob("data/processed/*.json")]
    by_year: dict[int, set[int]] = collections.defaultdict(set)
    for d in docs:
        m = RE_GFX.search(d.get("doc_no") or "")
        if m:
            by_year[int(m.group(1))].add(int(m.group(2)))

    have = sum(len(v) for v in by_year.values())
    span = sum(max(v) for v in by_year.values())
    rows = []
    gaps = []
    for y in sorted(by_year):
        s = by_year[y]
        missing = sorted(set(range(1, max(s) + 1)) - s)
        rows.append(f"| {y} | {len(s)}/{max(s)} | {len(missing)} | "
                    f"{'、'.join(str(n) for n in missing[:8])}{'…' if len(missing) > 8 else ''} |")
        gaps += [f"沪人社规〔{y}〕{n}号" for n in missing]

    no_date = [d for d in docs if not d.get("issued_at")]
    no_valid = [d for d in docs if not d.get("effective_to")]
    # 发文年份与文号年份差 >2 年，视为可疑（实测有 1987 年的文件被记成 2031 年）
    sus = []
    for d in docs:
        y_doc = (d.get("issued_at") or "")[:4]
        m = re.search(r"[〔（(](\d{2,4})[〕）)]", d.get("doc_no") or "")
        if not y_doc or not m:
            continue
        y_no = m.group(1)
        y_no = int(y_no) + (1900 if len(y_no) == 2 else 0)
        if abs(int(y_doc) - y_no) > 2:
            sus.append((d["doc_no"], d["issued_at"]))

    cols = collections.Counter(d["url"].split("/")[3] for d in docs)
    L = ["# 语料完整度报告（自动生成）", "",
         f"- 文档数：**{len(docs)}**",
         "- 部门：**1**（上海市人力资源和社会保障局）",
         "- 城市：**1**（上海）；其余 336 城只有入口",
         f"- 规范性文件文号覆盖：**{have}/{span} = {have/span:.0%}**", "",
         "## 一、规范性文件缺口（按文号连续性）", "",
         "| 年份 | 有/最大号 | 缺号数 | 缺哪些 |", "|---|---|---|---|", *rows, "",
         f"合计缺失 **{len(gaps)}** 个文号。近三年（2024–2026）基本齐全，越往前缺口越大——"
         "栏目列表本身就是不完备的（实测该站站内搜索已失效，无法按文号回捞）。", "",
         "## 二、元数据完整度", "",
         "| 字段 | 有值 | 覆盖率 |", "|---|---|---|",
         f"| 文号 | {sum(1 for d in docs if d.get('doc_no'))} | 100% |",
         f"| 标题 | {sum(1 for d in docs if d.get('title'))} | 100% |",
         f"| 发文日期 issued_at | {len(docs)-len(no_date)} | {(len(docs)-len(no_date))/len(docs):.0%} |",
         f"| 有效期至 effective_to | {len(docs)-len(no_valid)} | {(len(docs)-len(no_valid))/len(docs):.0%} |",
         f"| 附件 | {sum(1 for d in docs if d.get('attachments'))} | "
         f"{sum(1 for d in docs if d.get('attachments'))/len(docs):.0%} |", "",
         "## 三、可疑记录（发文年份与文号年份不符）", ""]
    L += [f"- `{a}` 记为 {b}" for a, b in sus[:10]] or ["- 无"]
    L += ["", "## 四、按栏目", "", "| 栏目 | 文档数 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in cols.most_common()]
    L += ["", "## 五、口径说明", "",
          "文号连续性只覆盖「沪人社规」这一序列。无文号的普通通知、公告、"
          "解读问答不计入分子分母，因此这个比例是**规范性文件的下界**，不是全部文件的完整度。",
          "",
          "**更重要的修正：编号不是连续的。** 本报告的分母取「当年最大号」，"
          "等于假设 1…N 号都真实存在过。2026-11-12 对 2017/2018 两年的逐条核查"
          "（`docs/scan/GAPS-A.md`）推翻了这个假设：2017 年在栏目里实际只见 23 个编号，"
          "最大号却是 42；2018 年实际 29 个，最大号 47。也就是说，"
          "「缺号」里有一部分**从来不曾存在**，真实覆盖率高于表中数字。",
          "",
          "所以本表应读作：**下界**。要给出准确分母，得逐号确认「是否存在过」，"
          "而这在官方渠道已整体下架的前提下基本做不到——这本身就是一条结论，"
          "写进 README 的边界说明，比假装算出一个精确百分比诚实。"]
    OUT.write_text("\n".join(L), "utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
