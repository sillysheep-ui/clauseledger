"""生成文档状态登记表：每条已知文件的四种状态之一。

输入（都是已有产物，不重新抓取）：
  data/processed/*.json      已入库正文
  data/watch/*/*.json        栏目列表快照（"列表里出现过"的证据）
  data/manifests/*.jsonl     批处理日志（"试过但失败"的唯一痕迹）

输出：
  data/registry/docs.jsonl   一行一份文件
  data/registry/sources.jsonl 一行一次来源（url → 字节）
  data/reports/doc-registry.md
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(".")
PROCESSED = ROOT / "data/processed"
WATCH = ROOT / "data/watch"
MANIFESTS = ROOT / "data/manifests"
OUT = ROOT / "data/registry"
REPORT = ROOT / "data/reports/doc-registry.md"

# 规范文件是有连续编号的，缺号才可枚举；其余文号体系（沪人社就发…）没有这个性质。
RE_GFX = re.compile(r"沪人社规[〔（(\[](\d{4})[〕）)\]]\s*第?\s*(\d+)\s*号")

# 状态优先级：有正文 > 抓取失败 > 只见过列表
RANK = {"ingested": 3, "parse_failed": 3, "fetch_failed": 2, "listed_only": 1}


def latest_snapshots(watch_dir: Path = WATCH) -> list[dict]:
    """每个栏目只取最新一份快照，避免历史快照把同一文号重复计。"""
    snapshots: list[dict] = []
    if not watch_dir.exists():
        return snapshots
    for d in sorted(p for p in watch_dir.iterdir() if p.is_dir()):
        files = sorted(d.glob("*.json"))
        if not files:
            continue
        try:
            snapshots.append(json.loads(files[-1].read_text("utf-8")))
        except json.JSONDecodeError:
            continue
    return snapshots


def manifest_rows(manifest_dir: Path = MANIFESTS) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(manifest_dir.glob("*.jsonl")):
        for line in f.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["_log"] = f.name
            rows.append(row)
    return rows


def build(processed_dir: Path = PROCESSED, watch_dir: Path = WATCH,
          manifest_dir: Path = MANIFESTS) -> tuple[list[dict], list[dict]]:
    entries: dict[str, dict] = {}

    def upsert(doc_no: str, **fields) -> None:
        cur = entries.setdefault(doc_no, {"doc_no": doc_no, "title": "", "status": "listed_only",
                                          "url": "", "chars": 0, "http_status": None,
                                          "error": "", "attempts": 0, "last_tried": "",
                                          "source": ""})
        for k, v in fields.items():
            if v not in (None, ""):
                cur[k] = v

    sources: list[dict] = []

    for snap in latest_snapshots(watch_dir):
        for it in snap.get("items", []):
            upsert(it["doc_no"], title=it.get("title", ""), url=it.get("url", ""),
                   listed_at=snap.get("fetched_at", ""))

    for p in sorted(processed_dir.glob("*.json")):
        rec = json.loads(p.read_text("utf-8"))
        chars = int(rec.get("stats", {}).get("chars", 0))
        status = "ingested" if chars > 0 else "parse_failed"
        atts = rec.get("attachments", [])
        upsert(rec["doc_no"], title=rec.get("title", ""), url=rec.get("url", ""),
               status=status, chars=chars, source=rec.get("retrieved_via", ""))
        sources.append({"doc_no": rec["doc_no"], "url": rec.get("url", ""),
                        "sha256": rec.get("page_sha256", ""), "kind": rec.get("page_kind", ""),
                        "fetched_at": rec.get("fetched_at", ""), "via": rec.get("retrieved_via", ""),
                        "role": "page"})
        for a in atts:
            sources.append({"doc_no": rec["doc_no"], "url": a.get("url", ""),
                            "sha256": a.get("sha256", ""), "kind": a.get("kind", ""),
                            "fetched_at": rec.get("fetched_at", ""),
                            "via": rec.get("retrieved_via", ""), "role": "attachment"})

    fails: dict[str, list[dict]] = defaultdict(list)
    for row in manifest_rows(manifest_dir):
        doc_no = row.get("doc_no")
        if not doc_no:
            continue
        ok = row.get("ok")
        if ok is False or str(ok).lower() == "false":
            fails[doc_no].append(row)

    for doc_no, rows in fails.items():
        cur = entries.get(doc_no)
        # 抓取失败过、但后来成功入库的，状态仍是 ingested——失败记录不能反过来降级证据
        if cur and cur["status"] in ("ingested", "parse_failed"):
            continue
        last = rows[-1]
        http_status = last.get("http_status") or last.get("status_code")
        upsert(doc_no, status="fetch_failed", attempts=len(rows),
               http_status=int(http_status) if http_status else None,
               error=str(last.get("error", ""))[:200],
               last_tried=last.get("last_tried") or last.get("_log", "")[-24:])

    for doc_no in missing_gfx_numbers(entries):
        upsert(doc_no, status="probably_exists",
               error="该文号落在编号区间内，但官方栏目没有列出",
               source="gap")

    ordered = sorted(entries.values(), key=lambda e: e["doc_no"])
    return ordered, sources


def missing_gfx_numbers(entries: dict[str, dict]) -> list[str]:
    """沪人社规的缺号：某年最大号以下、我们没有的那些。

    只是"编号上有缺口"，不等于"这份文件一定存在"——2017 年的实测显示
    实际只发了 23 个号，最大号却是 42。所以状态叫 probably_exists，不叫 missing。
    """
    have: dict[str, set[int]] = defaultdict(set)
    for doc_no in entries:
        m = RE_GFX.match(doc_no)
        if m:
            have[m.group(1)].add(int(m.group(2)))
    out: list[str] = []
    for year, nums in sorted(have.items()):
        for n in range(1, max(nums) + 1):
            if n not in nums:
                out.append(f"沪人社规〔{year}〕{n}号")
    return out


FIELDS = ("doc_no", "title", "status", "url", "chars", "http_status", "error",
          "attempts", "last_tried", "source")


def write(entries: list[dict], sources: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "docs.jsonl").write_text(
        "\n".join(json.dumps({k: e.get(k, "") for k in FIELDS}, ensure_ascii=False)
                  for e in entries) + "\n", "utf-8")
    (OUT / "sources.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in sources) + "\n", "utf-8")

    counts = Counter(e["status"] for e in entries)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 文档状态登记", "",
             f"共 {len(entries)} 份已知文件；来源记录 {len(sources)} 条。", "",
             "| 状态 | 数量 | 含义 |", "|---|---:|---|"]
    meaning = {"ingested": "已入库，有正文", "listed_only": "列表里出现过，正文未取到",
               "fetch_failed": "试过抓取，没拿到字节", "parse_failed": "字节在，抽不出文字"}
    for k in ("ingested", "listed_only", "fetch_failed", "parse_failed"):
        lines.append(f"| `{k}` | {counts.get(k, 0)} | {meaning[k]} |")
    gaps = [e for e in entries if e["status"] != "ingested"]
    if gaps:
        lines += ["", "## 已知但拿不到的文件", "", "| 文号 | 标题 | 状态 | 原因 |", "|---|---|---|---|"]
        for e in gaps:
            reason = (f"HTTP {e['http_status']}" if e.get("http_status")
                      else (e.get("error") or "未抓取"))
            lines.append(f"| {e['doc_no']} | {(e.get('title') or '')[:36]} | `{e['status']}` | {reason} |")
    REPORT.write_text("\n".join(lines) + "\n", "utf-8")


def main() -> None:
    entries, sources = build()
    write(entries, sources)
    counts = Counter(e["status"] for e in entries)
    print(f"登记 {len(entries)} 份：" + " / ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"来源 {len(sources)} 条 → {OUT}/docs.jsonl, sources.jsonl")


if __name__ == "__main__":
    main()
