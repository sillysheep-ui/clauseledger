"""有效期是**时间段**，不是单值：延期链决定"某一天有没有效"。

守三件事：
1. 连续延期（通知在到期前生效）拼成一段；
2. 断档延期（通知在到期后才生效）必须留出空档，不能把中间几年算成有效；
3. 查询按 as_of 走区间：同一份文件在 2021 年有效、2023 年已过期。
"""
from __future__ import annotations

import json

from app.domain import validity as D
from tools.ingest import validity as V


def _doc(no, start, own_end, text="x", title=None):
    return {"doc_no": no, "title": title or f"关于印发《{no}》的通知", "effective_from": start,
            "effective_to": own_end, "issued_at": start, "blocks": [{"text": text}]}


def test_continuous_extension_stays_one_period():
    a = _doc("沪人社规〔2018〕46号", "2018-12-29", "2020-12-28")
    n = _doc("沪人社规〔2020〕9号", "2020-11-01", "", "沪人社规〔2018〕46号 有效期至2022年12月31日",
             title="关于延长部分行政规范性文件有效期的通知")
    res = V.build({"沪人社规〔2018〕46号": a, "沪人社规〔2020〕9号": n})
    segs = V.periods(res, {"沪人社规〔2018〕46号": a, "沪人社规〔2020〕9号": n})["沪人社规〔2018〕46号"]
    assert len(segs) == 1
    assert segs[0].end == "2022-12-31"
    assert segs[0].gap is False


def test_extension_after_expiry_leaves_a_gap():
    """2020-12-28 到期、2026 年才发通知延到 2031：中间是断的。"""
    a = _doc("沪人社规〔2019〕1号", "2019-01-01", "2020-12-28")
    n = _doc("沪人社规〔2026〕1号", "2026-01-01", "", "沪人社规〔2019〕1号 有效期至2031年12月31日",
             title="关于延长部分行政规范性文件有效期的通知")
    docs = {"沪人社规〔2019〕1号": a, "沪人社规〔2026〕1号": n}
    res = V.build(docs)
    segs = V.periods(res, docs)["沪人社规〔2019〕1号"]
    assert len(segs) == 2
    assert segs[0].end == "2020-12-28"
    assert segs[1].gap is True
    assert segs[1].start == "2026-01-01"
    assert segs[1].end == "2031-12-31"


def test_status_for_uses_intervals(tmp_path, monkeypatch):
    segs = {"沪人社规〔2019〕1号": [
        {"start": "2019-01-01", "end": "2020-12-28", "by": [], "gap": False},
        {"start": "2026-01-01", "end": "2031-12-31", "by": ["沪人社规〔2026〕1号"], "gap": True},
    ]}
    p = tmp_path / "validity.json"
    p.write_text(json.dumps({"periods": segs}, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(D, "DERIVED", p)
    monkeypatch.setattr(D, "_PERIODS", None)

    assert D.status_for("沪人社规〔2019〕1号", "2018-06-01").status == "not_yet"
    assert D.status_for("沪人社规〔2019〕1号", "2020-06-01").status == "effective"
    gap = D.status_for("沪人社规〔2019〕1号", "2023-06-01")
    assert gap.status == "expired" and "断档" in gap.reason
    assert D.status_for("沪人社规〔2019〕1号", "2027-06-01").status == "effective"
    assert D.status_for("沪人社规〔2019〕1号", "2032-06-01").status == "expired"

    # 年份筛选也走区间：2023 年落在断档里，不算当年有效
    doc = {"doc_no": "沪人社规〔2019〕1号", "effective_from": "2019-01-01",
           "effective_to": "2031-12-31"}
    assert D.in_force(doc, 2020) is True
    assert D.in_force(doc, 2023) is False
    assert D.in_force(doc, 2027) is True


def test_card_shows_validity_note_for_expired_doc(tmp_path, monkeypatch):
    """卡片要明说"我引的这份，在基准日已经失效"。"""
    from app.answer import cards

    segs = {"沪人社规〔2018〕46号": [
        {"start": "2018-12-29", "end": "2022-12-31", "by": [], "gap": False}]}
    p = tmp_path / "validity.json"
    p.write_text(json.dumps({"periods": segs}, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(D, "DERIVED", p)
    monkeypatch.setattr(D, "_PERIODS", None)

    notes = cards._validity_notes([{"doc_no": "沪人社规〔2018〕46号"}], "2023-01-18")
    assert len(notes) == 1 and notes[0]["status"] == "expired"
    assert "2022-12-31" in notes[0]["reason"]
