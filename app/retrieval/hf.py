"""HuggingFace 缓存位置：默认放到仓库内，避免 `~/.cache` 不可写导致**静默降级**。

踩过的坑：沙箱/服务器上 `~/.cache/huggingface` 不可写，`CrossEncoder(...)` 抛
PermissionError，而调用方 try/except 后当作"没装模型"，重排与稠密召回悄悄退回
BM25——指标看起来正常，实际上默认配置根本没生效（2026-11 实测：重排开/关
跑出完全相同的数字才发现）。
"""
from __future__ import annotations

import os
from pathlib import Path

_HOME = Path("data/cache/hf").resolve()


def ensure() -> str:
    """保证 HF 缓存指向一个可写目录，返回该路径。已有环境变量则尊重。

    只设 `HF_HOME` 不够：`huggingface_hub` 在 **import 时**就把缓存路径算成了
    常量（`HF_HUB_CACHE`）。如果它先于本函数被导入（`bm25s` / `transformers`
    都可能带进来），再改环境变量也不生效，模型仍然去 `~/.cache` 找、仍然
    PermissionError。这里把已加载的常量一起改掉。
    """
    home = os.environ.get("HF_HOME")
    if not home:
        home = str(_HOME)
        os.environ["HF_HOME"] = home
    hub = str(Path(home) / "hub")
    os.environ.setdefault("HF_HUB_CACHE", hub)
    Path(hub).mkdir(parents=True, exist_ok=True)
    try:                      # 已经导入过就改常量；没导入过就让环境变量生效
        import huggingface_hub.constants as consts
    except Exception:
        return home
    consts.HF_HUB_CACHE = hub
    consts.HUGGINGFACE_HUB_CACHE = hub
    return home
