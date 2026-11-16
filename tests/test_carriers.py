"""载体选择规则的单元测试：不碰磁盘，直接构造 Carrier。"""
from __future__ import annotations

from tools.ingest.carriers import Carrier, choose_main, plan
from tools.ingest.clause import Clause


def _cl(path: str, text: str = "正文内容足够长，用来占位。") -> Clause:
    return Clause(path=path, level=1, label=path, text=text, block_index=0,
                  char_offset=0, page=None, start=0, end=len(text))


def _carrier(role: str, idx: int, kind: str, chars: int, extract: str = "native",
             ocr: float | None = None, paths: list[str] | None = None) -> Carrier:
    c = Carrier(role=role, idx=idx, sha256=f"{role}{idx}", path=None, url="",
                kind=kind, extract=extract, ocr_conf=ocr)
    n = max(1, chars // 100)
    c.blocks = [type("B", (), {"text": "字" * 100, "page": 1, "kind": "para"})() for _ in range(n)]
    c.clauses = [_cl(p) for p in (paths or ["一、"])]
    return c


def test_prefers_native_pdf_for_page_numbers():
    page = _carrier("page", 0, "html", 1200, paths=["一、", "二、"])
    pdf = _carrier("attachment", 1, "pdf", 1100, paths=["一、", "二、"])
    main, why = choose_main([page, pdf])
    assert main is pdf and "PDF" in why


def test_drops_short_shell_carrier():
    page = _carrier("page", 0, "html", 200, paths=["一、"])
    pdf = _carrier("attachment", 1, "pdf", 2000, paths=["一、", "二、", "三、"])
    main, why = choose_main([page, pdf])
    assert main is pdf and "明显不全" in why


def test_scan_needs_confidence():
    scan = _carrier("attachment", 1, "pdf", 1500, extract="ocr", ocr=0.9, paths=["一、"])
    scan2 = _carrier("attachment", 2, "pdf", 1500, extract="ocr", ocr=0.5, paths=["二、"])
    main, _ = choose_main([scan, scan2])
    assert main is scan


def test_duplicate_carrier_only_contributes_new_paths():
    page = _carrier("page", 0, "html", 1000, paths=["一、", "二、", "三、"])
    pdf = _carrier("attachment", 1, "pdf", 1000, paths=["一、", "二、", "三、"])
    p = plan({}, [page, pdf])
    assert p.main is pdf
    # 完全重合、没有新增路径 → 不进补充
    assert [c for c, _ in p.supplements] == []


def test_missing_paths_from_duplicate_become_partial_supplement():
    page = _carrier("page", 0, "html", 2000, paths=["一、", "二、", "三、", "四、"])
    pdf = _carrier("attachment", 1, "pdf", 2000, paths=["一、", "二、", "三、"])
    p = plan({}, [page, pdf])
    assert p.main is pdf
    assert [(c.human, partial) for c, partial in p.supplements] == [("页面", True)]
