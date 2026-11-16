"""一次 run 的全部状态。

设计约束（来自 docs/FOUNDATION.md）：
- **状态只有一个**：`RunState` 是唯一可序列化真源，节点不许在闭包里藏东西。
- **能用 JSON 存**：字段只用 str/int/bool/list/dict/None，恢复时不依赖 Python 对象。
- **门控是状态不是异常**：`gate` 非空 = 停在等人，恢复时从同一个 step 重入。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

StepName = Literal[
    "normalize", "coverage", "retrieve", "judge", "gate_judgement",
    "compose", "verify", "gate_action", "act", "finalize", "done",
]

STEPS: list[str] = [
    "normalize", "coverage", "retrieve", "judge", "gate_judgement",
    "compose", "verify", "gate_action", "act", "finalize", "done",
]


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class RunState:
    run_id: str
    question: str
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    step: str = "normalize"
    status: str = "running"          # running | waiting | done | failed
    # --- 输入 ---
    city: str | None = None
    year: int | None = None
    profile: dict = field(default_factory=dict)
    # --- 中间产物 ---
    coverage: dict | None = None
    hits: list[dict] = field(default_factory=list)
    judgement: dict | None = None
    answer: dict | None = None
    gate: dict | None = None
    action: dict | None = None
    error: str | None = None
    # --- 恢复用 ---
    done_steps: list[str] = field(default_factory=list)

    # ---- 序列化 ----
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "RunState":
        return cls(**json.loads(text))

    # ---- 便捷 ----
    def advance_to(self, step: str) -> None:
        self.step = step
        self.updated_at = now()

    def finish(self) -> None:
        self.status = "done"
        self.step = "done"
        self.updated_at = now()

    def fail(self, msg: str) -> None:
        self.status = "failed"
        self.error = msg
        self.updated_at = now()

    def snapshot(self) -> dict[str, Any]:
        """给审计页/接口用的只读视图。"""
        return {
            "run_id": self.run_id, "status": self.status, "step": self.step,
            "question": self.question, "city": self.city, "year": self.year,
            "coverage": self.coverage, "judgement": self.judgement,
            "answer": self.answer, "gate": self.gate, "action": self.action,
            "hits": [{k: h.get(k) for k in ("doc_no", "title", "citation", "score", "stale", "scope")}
                     for h in self.hits],
            "error": self.error, "updated_at": self.updated_at,
        }
