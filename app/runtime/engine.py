"""引擎：一次 run 从进来到出去，中间可能停很多次。

三条不变量（对应 docs/FOUNDATION.md §8 的验收）：
1. **每个 step 都有开始/结束两条事件**，崩溃后能看出死在哪；
2. **恢复只重入被中断的 step**，之前完成的不再跑；
3. **`act` 幂等**，重复恢复不会产生第二条待办。
"""
from __future__ import annotations

from .gates import PendingGate
from .state import STEPS, RunState
from .steps import STEPS_FN
from .store import Store


def _next(name: str) -> str:
    i = STEPS.index(name)
    return STEPS[i + 1] if i + 1 < len(STEPS) else "done"


class Engine:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()

    # ---- 创建 ----
    def start(self, question: str, profile: dict | None = None) -> RunState:
        state = RunState(run_id=self.store.new_id() if hasattr(self.store, "new_id") else _id(),
                         question=question, profile=profile or {})
        self.store.save(state)
        self.store.event(state, "run", "run_created", {"question": question})
        return state

    # ---- 推进 ----
    def advance(self, state: RunState, decision: dict | None = None) -> RunState:
        import time
        while state.status == "running" and state.step != "done":
            name = state.step
            fn = STEPS_FN[name]
            self.store.event(state, name, "step_started", {"attempt": len(state.done_steps) + 1})
            t0 = time.time()
            try:
                nxt = fn(state, decision)
            except PendingGate as g:
                state.gate = g.as_dict()
                state.status = "waiting"
                state.updated_at = state.gate["opened_at"]
                self.store.event(state, name, "gate_opened", {"kind": g.kind, "question": g.question,
                                                              "payload": g.payload})
                self.store.save(state)
                return state
            except Exception as e:  # noqa: BLE001 - 失败必须落盘，不能静默
                state.fail(f"{type(e).__name__}: {e}")
                self.store.event(state, name, "step_failed", {"error": state.error})
                self.store.save(state)
                return state
            decision = None          # 决定只消费一次
            self.store.event(state, name, "step_finished",
                             {"ms": int((time.time() - t0) * 1000)})
            if name not in state.done_steps:
                state.done_steps.append(name)
            state.advance_to(nxt or _next(name))
            self.store.save(state)
        return state

    # ---- 恢复 ----
    def resume(self, run_id: str, decision: dict | None = None) -> RunState:
        state = self.store.load(run_id)
        if state.status == "done":
            # 已经跑完的 run 不允许再推一次：重复执行 = 重复副作用，宁可直接拒绝。
            raise ValueError(f"{run_id} 已完成，不能重复执行（需要重跑请新建 run）")
        if not state.gate:
            raise ValueError(f"{run_id} 没有待处理的审批（status={state.status}）")
        self.store.event(state, state.step, "gate_resolved", {"decision": decision or {}})
        state.gate = None
        state.status = "running"
        return self.advance(state, decision)

    # ---- 便捷 ----
    def run(self, question: str, profile: dict | None = None,
            approvals: dict[str, dict] | None = None) -> RunState:
        """一路跑到结束；`approvals` 按门槛类型预先给决定，用于批处理/测试。"""
        approvals = approvals or {}
        state = self.start(question, profile)
        state = self.advance(state)
        guard = 0
        while state.status == "waiting" and guard < 10:
            guard += 1
            kind = (state.gate or {}).get("kind", "")
            if kind not in approvals:
                break
            state = self.resume(state.run_id, approvals[kind])
        return state


def _id() -> str:
    import uuid
    return "run_" + uuid.uuid4().hex[:12]
