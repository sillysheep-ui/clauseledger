"""评测门禁：把报告里的数字变成退出码。

硬规则（任何一条不过就直接失败）：
  * 误答必须为 0（没有政策却作答）
  * 域内无政策 / 域外的拒答率必须 100%
  * AEC 不得比基线低 5pp 以上（基线在 eval/baseline.json，用 --update-baseline 写）

用法：
    python -m tools.eval_gate                    # 取最新报告
    python -m tools.eval_gate eval/reports/x.json
    python -m tools.eval_gate --update-baseline
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASELINE = Path("eval/baseline.json")
TOL = 0.05


def latest() -> Path:
    files = sorted(Path("eval/reports").glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit("没有报告，先跑 python -m tools.evalkit.run")
    return files[-1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.path) if args.path else latest()
    res = json.loads(path.read_text(encoding="utf-8"))
    rows, neg = res["rows"], res.get("negatives", {})
    n = len(rows)
    aec = sum(1 for r in rows if r.get("aec")) / n if n else 0.0

    if args.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"aec": round(aec, 4), "n": n, "source": path.name}, indent=2),
                            encoding="utf-8")
        print(f"基线已更新：AEC {aec:.0%}（n={n}）")
        return 0

    fails: list[str] = []
    if neg.get("neg_answered", 0):
        fails.append(f"误答 {neg['neg_answered']} 条（必须 0）")
    if neg.get("no_policy_refused", 0) != neg.get("no_policy_n", 0):
        fails.append("域内无政策拒答未达 100%")
    if neg.get("oos_refused", 0) != neg.get("oos_n", 0):
        fails.append("域外拒答未达 100%")

    base = {}
    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        if aec < base.get("aec", 0) - TOL:
            fails.append(f"AEC {aec:.0%} 比基线 {base['aec']:.0%} 低超过 {TOL:.0%}")

    print(f"报告 {path.name} · n={n} · AEC {aec:.0%}" + (f" · 基线 {base['aec']:.0%}" if base else ""))
    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
