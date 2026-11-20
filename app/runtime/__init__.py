"""运行时骨架：状态 / 事件 / 门控 / 副作用。

对外只暴露四样：`Engine`、`RunState`、`Store`、`PendingGate`。
"""
from .engine import Engine
from .gates import PendingGate
from .state import STEPS, RunState
from .store import Store

__all__ = ["Engine", "RunState", "STEPS", "Store", "PendingGate"]
