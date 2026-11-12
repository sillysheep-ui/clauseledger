import json
from pathlib import Path

import pytest

from tools.ingest.manual import EntryError, load, validate

OK = {
    "doc_no": "沪人社规〔2019〕17号",
    "title": "关于本市企业职工基本养老保险若干问题的通知",
    "url": "https://rsj.sh.gov.cn/tgwgfx_17726/20190710/t0035_1392245.html",
    "issued_at": "2019-07-10",
    "valid_until": "2031-08-15",
    "source": "official",
}


def test_validate_ok():
    e = validate(OK)
    assert e["doc_no"] == "沪人社规〔2019〕17号"
    assert e["valid_until"] == "2031-08-15"


@pytest.mark.parametrize(
    "patch,msg",
    [
        ({"doc_no": "国发〔2021〕7号"}, "文号不合规"),
        ({"doc_no": "沪人社规[2019]17号"}, None),          # 半角括号也接受
        ({"title": "通知"}, "标题过短"),
        ({"url": ""}, "缺少证据 URL"),
        ({"issued_at": "2019/07/10"}, "发文日期"),
        ({"valid_until": "长期"}, "有效期"),
        ({"source": "weibo"}, "source"),
    ],
)
def test_validate_rejects(patch, msg):
    raw = {**OK, **patch}
    if msg is None:
        assert validate(raw)["doc_no"].startswith("沪人社规")
        return
    with pytest.raises(EntryError) as ei:
        validate(raw)
    assert msg in str(ei.value)


def test_load_rejects_duplicates(tmp_path: Path):
    p = tmp_path / "adds.json"
    p.write_text(json.dumps([OK, {**OK, "title": "另一个标题但同文号"}]), "utf-8")
    with pytest.raises(EntryError) as ei:
        load(p)
    assert "重复" in str(ei.value)


def test_load_missing_file_returns_empty(tmp_path: Path):
    assert load(tmp_path / "nope.json") == []


def test_load_reports_index(tmp_path: Path):
    p = tmp_path / "adds.json"
    p.write_text(json.dumps([OK, {**OK, "doc_no": "坏号"}]), "utf-8")
    with pytest.raises(EntryError) as ei:
        load(p)
    assert "第 2 条" in str(ei.value)
