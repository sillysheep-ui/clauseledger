"""外部补录：把栏目列表页拿不到的文号，按人工确认的元数据收进语料。

为什么要单独一条通道：

    栏目列表本身不完备（29 页 565 行里只有 294 个编号），缺的 58 个文号
    不可能靠"翻页"补回来，只能从外部（市政府公报、上级网站、转载页）找到。
    这类页面没有列表页的 hint，正文里的文号又常常是"引用别的文件"——
    旧事故：三份文件被误判成「国发〔2021〕7号」，因为正则抓到了正文里的引用。

所以补录必须满足两条：

    1. 元数据（文号 / 标题 / 发文日期）由人确认，不交给正则猜；
    2. 必须留下证据 URL，`retrieved_via` 记成 `manual`，与爬虫产物区分开。

用法：

    python -m tools.ingest.manual --list data/fixtures/manual_adds.json --dry-run
    python -m tools.ingest.manual --list data/fixtures/manual_adds.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .pipeline import OUT, ingest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIST = ROOT / "data" / "fixtures" / "manual_adds.json"

# 只接受上海市人社局的规范性文件编号；别的体系（沪府办、沪教委）走各自通道，
# 免得把不同发文机关的编号混进同一个命名空间。
RE_SHB_GFX = re.compile(r"^沪人社规[〔（(\[]\s*(\d{4})\s*[〕）)\]]\s*第?\s*(\d+)\s*号$")
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EntryError(ValueError):
    """单条补录记录不合格。"""


def validate(raw: dict) -> dict:
    """校验一条补录记录，返回规范化后的 dict。

    宁可在这里报错，也不要把没有出处的文件混进语料——语料的每一份
    都要能在审计里回答"它从哪来、谁确认的"。
    """
    doc_no = (raw.get("doc_no") or "").strip()
    if not RE_SHB_GFX.match(doc_no):
        raise EntryError(f"文号不合规（需 沪人社规〔YYYY〕N号）：{doc_no!r}")

    title = (raw.get("title") or "").strip()
    if len(title) < 4:
        raise EntryError(f"标题过短：{title!r}")

    url = (raw.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise EntryError(f"缺少证据 URL：{url!r}")

    issued_at = (raw.get("issued_at") or "").strip()
    if not RE_DATE.match(issued_at):
        raise EntryError(f"发文日期需 YYYY-MM-DD：{issued_at!r}")

    valid_until = (raw.get("valid_until") or "").strip() or None
    if valid_until is not None and not RE_DATE.match(valid_until):
        raise EntryError(f"有效期需 YYYY-MM-DD：{valid_until!r}")

    source = (raw.get("source") or "").strip()
    if source not in {"official", "mirror"}:
        raise EntryError(f"source 只能是 official/mirror：{source!r}")

    return {
        "doc_no": doc_no,
        "title": title,
        "url": url,
        "issued_at": issued_at,
        "valid_until": valid_until,
        "source": source,
        "note": (raw.get("note") or "").strip(),
    }


def load(path: Path) -> list[dict]:
    """读补录清单，逐条校验；任何一条不合格就整体失败。"""
    if not path.exists():
        return []
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, list):
        raise EntryError("补录清单必须是 JSON 数组")
    out, seen = [], set()
    for i, raw in enumerate(data):
        try:
            e = validate(raw)
        except EntryError as exc:
            raise EntryError(f"第 {i + 1} 条：{exc}") from exc
        if e["doc_no"] in seen:
            raise EntryError(f"第 {i + 1} 条：文号重复 {e['doc_no']}")
        seen.add(e["doc_no"])
        out.append(e)
    return out


def already_have(doc_no: str) -> bool:
    """语料里是不是已经有这个文号（不管是爬来的还是补录的）。"""
    for p in OUT.glob("*.json"):
        try:
            rec = json.loads(p.read_text("utf-8"))
        except Exception:
            continue
        if rec.get("doc_no") == doc_no:
            return True
    return False


def run(path: Path, dry_run: bool = False) -> int:
    entries = load(path)
    if not entries:
        print(f"补录清单为空或不存在：{path}")
        return 0

    todo = [e for e in entries if not already_have(e["doc_no"])]
    print(f"清单 {len(entries)} 条，已有 {len(entries) - len(todo)} 条，待补 {len(todo)} 条")
    if dry_run:
        for e in todo:
            print(f"  [dry] {e['doc_no']} {e['title'][:40]} ← {e['url']}")
        return 0

    ok = 0
    for e in todo:
        hint = {k: e[k] for k in ("doc_no", "title", "issued_at", "valid_until")}
        try:
            rec = ingest(e["url"], slug=None, hint=hint)
        except Exception as exc:  # 单条失败不拖垮整批
            print(f"  [fail] {e['doc_no']}: {type(exc).__name__}: {exc}")
            continue
        # 补录的出处与人工确认必须写进记录，否则分不清它和爬虫产物
        rec["retrieved_via"] = f"manual:{e['source']}"
        rec["doc_no_source"] = "manual"
        rec["manual_note"] = e["note"]
        name = re.sub(r"[^\w\u4e00-\u9fff]+", "-", e["doc_no"])[:60]
        (OUT / f"{name}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), "utf-8"
        )
        ok += 1
        print(f"  [ok] {e['doc_no']} {rec['stats']['chars']} 字 / {rec['stats']['clauses']} 条")
    print(f"完成：{ok}/{len(todo)}")
    return 0 if ok == len(todo) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="外部补录：按人工确认的元数据收录文件")
    ap.add_argument("--list", type=Path, default=DEFAULT_LIST)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        return run(args.list, args.dry_run)
    except EntryError as exc:
        print(f"清单校验失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
