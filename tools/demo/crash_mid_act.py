"""故障注入：在 `act` 把待办写进库之后、`step_finished` 落盘之前硬退出。

这是最坏的中断点——副作用已经发生，但系统"不知道自己做到哪"。
用它造一个 status=running / step=act 的 run，再验证恢复时不会写第二遍：

    python -m tools.demo.crash_mid_act
    curl -s localhost:8010/api/runs/<id> | ...      # status=running, step=act
    curl -s -X POST localhost:8010/api/runs/<id>/resume -d '{"action":"recover"}'
    # → applied=0 reused=3（三条都在，一条没多）

只用于演示与测试，不接进正常路径。
"""
from __future__ import annotations

import os
import sys

from app.runtime import Engine, Store, steps


def main() -> int:
    real = steps.STEPS_FN["act"]

    def crashy(state, decision):
        real(state, decision)
        state.gate = None
        print(f"run_id={state.run_id} 待办已写入，现在 os._exit(9)", flush=True)
        sys.stdout.flush()
        os._exit(9)

    steps.STEPS_FN["act"] = crashy
    eng = Engine(Store())
    eng.run("博士毕业落户能加多少分", {"degree": "phd"},
            approvals={"action": {"action": "approve", "by": "crash-demo"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
