import json
from pathlib import Path

import pytest

from app.domain.registry import DocEntry, DocRegistry
from tools.ingest import registry as reg


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")


@pytest.fixture()
def tree(tmp_path: Path) -> dict[str, Path]:
    processed = tmp_path / "processed"
    watch = tmp_path / "watch"
    manifests = tmp_path / "manifests"
    _write(processed / "a.json", {"doc_no": "沪人社规〔2020〕1号", "title": "甲文件",
                                  "stats": {"chars": 100}, "attachments": [],
                                  "url": "u1", "page_sha256": "aaa", "retrieved_via": "crawl"})
    _write(processed / "b.json", {"doc_no": "沪人社规〔2020〕3号", "title": "丙文件",
                                  "stats": {"chars": 0}, "attachments": [],
                                  "url": "u3", "page_sha256": "ccc", "retrieved_via": "manual:official"})
    (watch / "col").mkdir(parents=True, exist_ok=True)
    _write(watch / "col" / "2026-01-01T000000+0000.json",
           {"column": "col", "fetched_at": "2026-01-01T00:00:00+00:00", "items": [
               {"doc_no": "沪人社规〔2020〕1号", "title": "甲文件", "url": "u1"},
               {"doc_no": "沪人社规〔2020〕2号", "title": "乙文件", "url": "u2"}]})
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "ingest-1.jsonl").write_text(
        json.dumps({"doc_no": "沪人社规〔2020〕1号", "ok": False}, ensure_ascii=False) + "\n"
        + json.dumps({"doc_no": "沪人社规〔2020〕4号", "ok": False, "http_status": 403},
                     ensure_ascii=False) + "\n", "utf-8")
    return {"processed": processed, "watch": watch, "manifests": manifests}


def test_status_resolution(tree):
    entries, sources = reg.build(tree["processed"], tree["watch"], tree["manifests"])
    by = {e["doc_no"]: e for e in entries}
    assert by["沪人社规〔2020〕1号"]["status"] == "ingested"      # 失败日志不能降级已入库
    assert by["沪人社规〔2020〕3号"]["status"] == "parse_failed"  # 字节在、字为 0
    assert by["沪人社规〔2020〕2号"]["status"] == "listed_only"   # 只在列表里
    assert by["沪人社规〔2020〕4号"]["status"] == "fetch_failed"  # 抓失败过
    assert by["沪人社规〔2020〕4号"]["http_status"] == 403
    assert any(s["role"] == "page" for s in sources)


def test_gap_numbers_are_not_documents():
    entries = {"沪人社规〔2020〕1号": {}, "沪人社规〔2020〕3号": {}}
    assert reg.missing_gfx_numbers(entries) == ["沪人社规〔2020〕2号"]


def test_registry_load_and_gap(tmp_path: Path):
    p = tmp_path / "docs.jsonl"
    rows = [
        {"doc_no": "沪人社规〔2020〕4号", "title": "关于失业保险金申领的通知",
         "status": "fetch_failed", "http_status": 403, "attempts": 2,
         "last_tried": "2026-11-11"},
        {"doc_no": "沪人社规〔2020〕1号", "title": "甲文件", "status": "ingested", "chars": 100},
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    r = DocRegistry.load(p)
    assert r.summary()["ingested"] == 1
    gap = r.best_gap({"失业保险金", "申领"})
    assert gap and gap.doc_no == "沪人社规〔2020〕4号"
    assert "抓取失败" in gap.explain() and "不代表没有这份政策" in gap.explain()


def test_explain_never_says_no_data():
    entries = {s: DocEntry(doc_no=f"X{i}", title="某文件", status=s)
               for i, s in enumerate(("fetch_failed", "parse_failed", "listed_only",
                                      "probably_exists", "ingested"))}
    for e in entries.values():
        text = e.explain()
        assert "暂无数据" not in text and "没有相关政策" not in text
