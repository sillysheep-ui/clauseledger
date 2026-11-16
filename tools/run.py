"""命令行入口：跑一次 run、看事件、恢复。

    python -m tools.run "硕士毕业落户能加多少分" --profile profile.json
    python -m tools.run --list
    python -m tools.run --show run_xxx
    python -m tools.run --resume run_xxx --action confirm_unknown --by sheep

设计上这里**不判断业务**，只负责把 Engine 的状态翻译成人话。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.runtime import Engine, Store


def _print_state(s) -> None:
    print(f"run {s.run_id}  {s.status}/{s.step}")
    print(f"问：{s.question}")
    if s.coverage:
        print(f"覆盖：{s.coverage['status']} — {s.coverage['message']}")
    for i, h in enumerate(s.hits[:3], 1):
        flag = f"  ⚠ {h['stale']}" if h.get("stale") else ""
        sc = f"   [{h.get('scope')}]" if h.get("scope") else ""
        print(f"  [{i}] {h['score']:>6.2f} {h['citation']}{sc}{flag}")
    if s.judgement:
        j = s.judgement
        line = f"判定：{j.get('verdict')}"
        if j.get("total_known") is not None:
            line += f"  已知 {j['total_known']} / 上限 {j['total_upper']} / 标准 {j.get('standard')}"
        if j.get("reason"):
            line += f"  （{j['reason']}）"
        print(line)
    if s.answer and s.answer.get("text"):
        print(f"答：{s.answer['text']}")
        for v in (s.answer.get("verified") or []):
            print(f"   引用核验 {'✓' if v['verbatim'] else '✗'} {v['citation']}")
    if s.action:
        a = s.action
        n = len(a.get("items") or [])
        print(f"动作：{a['kind']} × {n}" + (f"  结果 {a.get('result')}" if a.get("result") else ""))
    if s.gate:
        print(f"\n⏸ 等待审批 [{s.gate['kind']}]：{s.gate['question']}")
        print(f"   用 --resume {s.run_id} --action <approve|deny|confirm_unknown|reject> 继续")
    if s.error:
        print(f"错误：{s.error}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--profile", help="画像 JSON 文件")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show")
    ap.add_argument("--events", action="store_true", help="配合 --show 打印事件流")
    ap.add_argument("--resume")
    ap.add_argument("--action")
    ap.add_argument("--by", default="")
    args = ap.parse_args(argv)

    store = Store()
    eng = Engine(store)

    if args.list:
        for r in store.list_runs():
            print(f"{r['run_id']}  {r['status']:<8} {r['step']:<15} {r['question'][:40]}")
        return 0

    if args.show:
        s = store.load(args.show)
        _print_state(s)
        if args.events:
            for e in store.events(args.show):
                print(f"  #{e['seq']:<3} {e['at']} {e['step']:<15} {e['kind']}")
        return 0

    if args.resume:
        decision = {"action": args.action, "by": args.by} if args.action else None
        s = eng.resume(args.resume, decision)
        _print_state(s)
        return 0

    if not args.question:
        ap.print_help()
        return 2

    profile = json.loads(Path(args.profile).read_text("utf-8")) if args.profile else {}
    s = eng.run(args.question, profile, approvals={})
    _print_state(s)
    return 0 if s.status in ("done", "waiting") else 1


if __name__ == "__main__":
    sys.exit(main())
