import json
from pathlib import Path

from tools.ingest.fields import agency_of, published_at_of


def B(*texts):
    return [{"i": i, "text": t} for i, t in enumerate(texts)]


def test_agency_from_title():
    a, src = agency_of("上海市人力资源和社会保障局关于调整工伤保险待遇的通知", B())
    assert a == "上海市人力资源和社会保障局" and src == "title"


def test_agency_from_head_blob():
    head = "上海市人力资源和社会保障局上 海 市 民 政 局沪人社规〔2023〕20 号"
    a, src = agency_of("关于调整补助标准的通知", B(head))
    assert src == "head" and "上海市民政局" in a


def test_agency_from_sign():
    blocks = B(*[f"条款{i}" for i in range(10)],
               "上海市人力资源和社会保障局", "2019年3月1日")
    a, src = agency_of("关于某事的通知", blocks)
    assert a == "上海市人力资源和社会保障局" and src == "sign"


def test_agency_docno_fallback():
    a, src = agency_of("关于某事的通知", B("各委、办、局："), "沪人社养发〔2014〕51号")
    assert a == "上海市人力资源和社会保障局" and src == "docno"


def test_agency_unknown():
    a, src = agency_of("关于某事的通知", B("各委、办、局："), "未知〔2014〕1号")
    assert a is None and src == "unknown"


def test_published_print_wins_over_list():
    d, src = published_at_of(B("上海市人力资源和社会保障局办公室 2024 年 4 月 27 日印发"),
                             {"issued_at": "2024-04-20"})
    assert d == "2024-04-27" and src == "print"


def test_published_page():
    d, src = published_at_of(B("发布时间：2014-09-18"), None)
    assert d == "2014-09-18" and src == "page"


def test_published_list_fallback():
    d, src = published_at_of(B("正文"), {"issued_at": "2020-07-01"})
    assert d == "2020-07-01" and src == "list"


def test_published_unknown():
    d, src = published_at_of(B("正文"), {})
    assert d is None and src == "unknown"


def test_backfill_writes_fields(tmp_path: Path, monkeypatch):
    import tools.ingest.backfill_fields as bf
    rec = {"doc_no": "沪人社规〔2023〕1号",
           "title": "上海市人力资源和社会保障局关于某事的通知",
           "blocks": [{"i": 0, "text": "正文"}], "meta_hint": {"issued_at": "2023-02-01"}}
    (tmp_path / "a.json").write_text(json.dumps(rec), "utf-8")
    monkeypatch.setattr(bf, "PROCESSED", tmp_path)
    monkeypatch.setattr(bf, "REPORT", tmp_path / "r.md")
    bf.main([])
    out = json.loads((tmp_path / "a.json").read_text("utf-8"))
    assert out["agency"] == "上海市人力资源和社会保障局"
    assert out["published_at"] == "2023-02-01"
    assert out["agency_source"] == "title" and out["published_at_source"] == "list"


def test_published_pubdate_from_meta():
    d, src = published_at_of(B("正文"), None, {"pubdate": "2022-06-14 12∶06"})
    assert d == "2022-06-14" and src == "pubdate"


def test_published_print_beats_pubdate():
    d, src = published_at_of(B("上海市人力资源和社会保障局办公室 2024 年 4 月 27 日印发"),
                             None, {"pubdate": "2024-04-29 09:00"})
    assert d == "2024-04-27" and src == "print"
