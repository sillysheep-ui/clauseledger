"""测试默认不启用稠密召回：跑单测不该依赖 95 MB 模型与 MPS。

默认模式在 `app/retrieval/search.py` 里是 hybrid（真实使用场景），
但测试要的是确定、快速、离线——所以这里统一压回 bm25。
混合召回的行为由 `tests/test_hybrid.py` 用 monkeypatch 单独覆盖。
"""
import os

os.environ.setdefault("CLAUSELEDGER_RECALL", "bm25")

# 单测不加载语义模型：置信度退回旧判据（BM25 + 分差）
os.environ.setdefault("CLAUSELEDGER_CONF", "0")

import tempfile
from pathlib import Path

import pytest

_DB_DIR = Path(tempfile.mkdtemp(prefix="cl-test-db-"))


@pytest.fixture(autouse=True)
def _isolated_runtime_db(tmp_path, monkeypatch):
    """每个测试一个库：否则 run/events/actions 会跨测试累积。"""
    monkeypatch.setenv("CLAUSELEDGER_DB", str(tmp_path / "runtime.db"))

# ---- 干净检出（CI）上的处理 ----
# data/processed 与 data/index 不入库（可从原始字节重建），所以 clone 下来没有语料。
# 依赖真实语料的用例在那种环境下**跳过并写明原因**，而不是伪造一个假索引让它变绿。

HAS_CORPUS = Path("data/index/clauses.jsonl").exists()
_NEEDS_CORPUS = {"test_runtime.py", "test_search_evidence.py", "test_search_thresholds.py",
                 "test_tracing.py", "test_hybrid.py", "test_api.py", "test_confidence.py",
                 "test_answertype.py"}


def pytest_collection_modifyitems(config, items):
    if HAS_CORPUS:
        return
    skip = pytest.mark.skip(reason="需要 data/index（语料不入库，先跑 tools.ingest + app.retrieval.index）")
    for item in items:
        if Path(item.fspath).name in _NEEDS_CORPUS:
            item.add_marker(skip)
