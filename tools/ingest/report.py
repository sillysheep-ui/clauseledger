"""把 data/processed 读一遍，出采集质量报告：格式分布、失败率、可疑样本。

用法：python -m tools.ingest.report > docs/scan/上海人社-采集报告.md
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

PROCESSED = Path("data/processed")


def ext_of(url: str | None) -> str | None:
    if not url:
        return None
    base = url.split("?")[0].rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower()[:5] if "." in base else None


def main() -> None:
    docs = [json.loads(p.read_text("utf-8")) for p in sorted(PROCESSED.glob("*.json"))]
    n = len(docs)
    print("# 上海人社采集质量报告\n")
    print(f"样本：{n} 份（`data/processed/`）\n")

    print("## 载体与附件\n")
    print("| 指标 | 值 |")
    print("|---|---|")
    print(f"| 详情页类型 | {dict(Counter(d['page_kind'] for d in docs))} |")
    att_ext = Counter(e for d in docs for e in (ext_of(a.get('url')) for a in d['attachments']) if e)
    print(f"| 附件后缀 | {dict(att_ext)} |")
    print(f"| 无附件 | {sum(1 for d in docs if not d['attachments'])} |")
    att_err = Counter(a['error'] for d in docs for a in d['attachments'] if a.get('error'))
    print(f"| 附件解析失败 | {sum(att_err.values())} {dict(att_err)} |")
    print(f"| 平均附件数 | {sum(len(d['attachments']) for d in docs) / n:.2f} |")

    print("\n## 抽取路径（文本是怎么来的）\n")
    atts = [a for d in docs for a in d['attachments']]
    print("| 路径 | 附件数 | 平均字符 | 说明 |")
    print("|---|---|---|---|")
    for path, note in (("native", "原生文本层"), ("ocr", "渲染 + Vision OCR"),
                       ("textutil", ".doc 转文本"), ("unsupported", "读不出来，已标记")):
        sub = [a for a in atts if a.get('extract') == path]
        avg = (sum(a.get('chars') or 0 for a in sub) / len(sub)) if sub else 0
        print(f"| {path} | {len(sub)} | {avg:.0f} | {note} |")
    ocr = [a for a in atts if a.get('extract') == 'ocr' and a.get('ocr_conf')]
    if ocr:
        cs = sorted(a['ocr_conf'] for a in ocr)
        print(f"\nOCR 置信度：{cs[0]:.2f} ~ {cs[-1]:.2f}（中位 {cs[len(cs)//2]:.2f}），低于 0.5 的块已丢弃。")

    print("\n## 正文与条款\n")
    chars = [d['stats']['chars'] for d in docs]
    clauses = [d['stats']['clauses'] for d in docs]
    print("| 指标 | 最小 | 中位 | 最大 | 合计 |")
    print("|---|---|---|---|---|")
    print(f"| 字数 | {min(chars)} | {statistics.median(chars):.0f} | {max(chars)} | {sum(chars):,} |")
    print(f"| 条款 | {min(clauses)} | {statistics.median(clauses):.0f} | {max(clauses)} | {sum(clauses):,} |")
    lv = Counter(l for d in docs for l in d['stats']['by_level'])
    print(f"\n层级分布（文件数计）：{dict(lv)}")

    print("\n## 元数据完整度\n")
    print("| 字段 | 有值 | 占比 |")
    print("|---|---|---|")
    for k in ("doc_no", "effective_from", "effective_to"):
        have = sum(1 for d in docs if d.get(k))
        print(f"| {k} | {have} | {have / n:.0%} |")
    src = Counter(d.get('doc_no_source') for d in docs)
    print(f"| doc_no 来源 | {dict(src)} |")

    print("\n## 可疑样本（条款数 ≤2）\n")
    bad = sorted((d for d in docs if d['stats']['clauses'] <= 2), key=lambda d: d['stats']['chars'])
    if not bad:
        print("无。\n")
    else:
        print("| 文号 | 字数 | 条款 | 附件 |")
        print("|---|---|---|---|")
        for d in bad[:15]:
            atts = ",".join(e or "?" for e in (ext_of(a.get('url')) for a in d['attachments']) if e)
            print(f"| {d.get('doc_no') or d['title'][:20]} | {d['stats']['chars']} | "
                  f"{d['stats']['clauses']} | {atts} |")

    print("\n## 最大 5 份\n")
    print("| 文号 | 字数 | 条款 |")
    print("|---|---|---|")
    for d in sorted(docs, key=lambda d: -d['stats']['chars'])[:5]:
        print(f"| {d.get('doc_no')} | {d['stats']['chars']:,} | {d['stats']['clauses']} |")


if __name__ == "__main__":
    main()
