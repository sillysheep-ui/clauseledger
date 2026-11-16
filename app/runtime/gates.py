"""门控：到点停住等人，等的是一个**决定**，不是一次点击。

三个中断点（docs/ARCHITECTURE.md:240）：
- clarify        资料不全，需要补字段
- gate_judgement 判定为 unknown / 有冲突，需要人确认
- gate_action   有副作用，需要人批准

语义：`raise PendingGate(gate)` 立刻停止推进，状态落盘；恢复时**从同一个 step 重入**，
由 `decision` 决定往哪走——这与 LangGraph 的 `interrupt()` 一致：节点会从头跑，
所以 interrupt 之前不能有写操作。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .state import now


@dataclass
class PendingGate(Exception):
    kind: str
    payload: dict = field(default_factory=dict)
    question: str = ""      # 给人看的一句话：为什么停下来

    def as_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload, "question": self.question,
                "opened_at": now()}


def decide(decision: dict | None) -> str:
    """把外部决定归一成动作名；没给决定就是空串（表示还没人批）。"""
    if not decision:
        return ""
    return str(decision.get("action") or "")
