"""行为套件：C17–C22 六条终态断言。

判分全在代码里，不调模型；每条只问"世界变成什么样了"，不问模型说了什么。

用法：
    python -m tools.eval_behavior          # 打印表，全绿退出码 0
    python -m tools.eval_behavior --json   # 给 CI 用
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _isolate(tmp: Path) -> None:
    os.environ["CLAUSELEDGER_DB"] = str(tmp / "runtime.db")
    os.environ["CLAUSELEDGER_WORLD"] = str(tmp / "world.json")


def _count(run_id: str) -> int:
    from app.runtime import db
    with db.connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM actions WHERE run_id=?", (run_id,)).fetchone()
    return int(row["n"])


def _events(run_id: str) -> list[dict]:
    from app.runtime.store import Store
    return Store().events(run_id)


def _run(engine, question: str, profile: dict | None = None):
    st = engine.start(question, profile)
    return engine.advance(st)


def check_coverage_gate(engine) -> tuple[bool, str]:
    st = _run(engine, "北京落户需要几年社保")
    acts = _count(st.run_id)
    applied = (st.action or {}).get("applied", 0)
    kind = (st.answer or {}).get("kind")
    ok = st.status == "done" and kind in ("refusal", "not_covered", "no_policy", "out_of_domain", "insufficient") and acts == 0 and applied == 0
    return ok, f"status={st.status} step={st.step} kind={(st.answer or {}).get('kind')} actions={acts} applied={applied}"


def check_judgement_gate(engine) -> tuple[bool, str]:
    st = _run(engine, "硕士毕业落户能加多少分", {"degree": "master"})
    has_card = bool(((st.answer or {}).get("card") or {}).get("kind"))
    ok = st.status == "waiting" and st.step == "gate_judgement" and has_card
    return ok, f"status={st.status} step={st.step} card={has_card}"


def check_approve_exactly_three(engine) -> tuple[bool, str]:
    st = _run(engine, "博士毕业落户能加多少分", {"degree": "phd"})
    if st.step != "gate_action":
        return False, f"没停在 gate_action：status={st.status} step={st.step}"
    st = engine.resume(st.run_id, {"action": "approve", "by": "test"})
    n1 = _count(st.run_id)
    if st.step == "act":
        engine.resume(st.run_id, {"action": "approve", "by": "test"})
    n2 = _count(st.run_id)
    return (n1 == 3 and n2 == 3), f"first={n1} second={n2} step={st.step}"


def check_reject_writes_nothing(engine) -> tuple[bool, str]:
    st = _run(engine, "博士毕业落户能加多少分", {"degree": "phd"})
    if st.step != "gate_action":
        return False, f"没停在 gate_action：{st.step}"
    st = engine.resume(st.run_id, {"action": "reject", "by": "test"})
    return _count(st.run_id) == 0, f"actions={_count(st.run_id)} status={st.status}"


def check_no_cross_city_judge(engine) -> tuple[bool, str]:
    st = _run(engine, "北京落户需要几年社保")
    j = st.judgement or {}
    return j.get("source") == "none", f"source={j.get('source')} verdict={j.get('verdict')}"


def check_resume_after_crash(engine) -> tuple[bool, str]:
    st = _run(engine, "博士毕业落户能加多少分", {"degree": "phd"})
    if st.step != "gate_action":
        return False, f"没停在 gate_action：{st.step}"
    st = engine.resume(st.run_id, {"action": "approve", "by": "test"})
    n0 = _count(st.run_id)
    # 模拟"进程死在 act 里"：step 回退、状态 running、已完成标记不变
    st.status, st.step = "running", "act"
    engine.store.save(st)
    st = engine.resume(st.run_id)
    n1 = _count(st.run_id)
    rec = any(e.get("kind") == "run_recovered" for e in _events(st.run_id))
    return (n0 == 3 and n1 == 3 and rec), f"before={n0} after={n1} recovered_event={rec}"


CHECKS = [
    ("C17 未接入城市：拒答且零动作", check_coverage_gate),
    ("C18 unknown 必停判定门且卡已生成", check_judgement_gate),
    ("C19 批准恰好写 3 条、重复批准不新增", check_approve_exactly_three),
    ("C20 拒绝写 0 条", check_reject_writes_nothing),
    ("C21 非上海不进入上海判定", check_no_cross_city_judge),
    ("C22 崩在 act 里恢复后不重复", check_resume_after_crash),
]


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    as_json = "--json" in args
    from app.runtime.engine import Engine
    from app.runtime.store import Store

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        _isolate(Path(tmp))
        engine = Engine(Store(Path(tmp) / "runs"))
        for name, fn in CHECKS:
            try:
                ok, detail = fn(engine)
            except Exception as e:  # noqa: BLE001 - 断言失败要报告，不要让整个套件挂掉
                ok, detail = False, f"{type(e).__name__}: {e}"
            results.append({"check": name, "ok": ok, "detail": detail})

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']}  — {r['detail']}")
        passed = sum(1 for r in results if r["ok"])
        print(f"\n{passed}/{len(results)} 通过")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
