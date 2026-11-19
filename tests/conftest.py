"""测试默认不启用稠密召回：跑单测不该依赖 95 MB 模型与 MPS。

默认模式在 `app/retrieval/search.py` 里是 hybrid（真实使用场景），
但测试要的是确定、快速、离线——所以这里统一压回 bm25。
混合召回的行为由 `tests/test_hybrid.py` 用 monkeypatch 单独覆盖。
"""
import os

os.environ.setdefault("CLAUSELEDGER_RECALL", "bm25")

# 单测不加载语义模型：置信度退回旧判据（BM25 + 分差）
os.environ.setdefault("CLAUSELEDGER_CONF", "0")
