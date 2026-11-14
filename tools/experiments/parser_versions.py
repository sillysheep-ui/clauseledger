"""实验 B：解析器三个版本，跑同一批 dev 题，看评测能不能分辨出差异。

三个版本（git rev）：
    v031   6414759^   表格逐格重复、正文混 HTML、注释里的 JS 都在
    v032   6414759    表格按行取、剥标签、docx 表格救回
    v032c  d9cfda2    + 排除 HTML 注释节点

做法：每次都从 data/raw 原件重解析（不发 HTTP），重建索引，跑 dev 50 题。
除了 hit@1，另记两个能分辨解析质量的量：
    artifact    top-1 条款里含伪影（JS 源码/HTML 标签/面包屑/表格竖线）
    locatable   top-1 条款能定位（有页码，或有字符区间）

用法：
    python -m tools.experiments.parser_versions --run
    python -m tools.experiments.parser_versions --report   # 只看已跑结果
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx  # noqa: F401  (确保 venv 完整，顺便固定导入顺序)

REVS = [("v031", "6414759^"), ("v032", "6414759"), ("v032c", "d9cfda2")]
PARSE = Path("tools/ingest/parse.py")
SEED = Path("data/processed.seed")
PROCESSED = Path("data/processed")
REPORTS = Path("eval/reports")

ARTIFACTS = ("const ", "function(", "encodeURIComponent", "document.writeln",
             "<!--", "您现在的位置", "当前位置", "<div", "<span", "<p>")
QUESTIONS = Path("eval/questions_v2.json")


def sh(cmd: list[str]) -> None:
    if cmd[0] == "python":
        cmd = [sys.executable, *cmd[1:]]          # 必须用 venv 里的解释器
    print(f"  $ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, text=True, capture_output=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:], flush=True)
        raise SystemExit(f"失败：{' '.join(cmd)}")


def git_show(rev: str) -> str:
    return subprocess.run(["git", "show", f"{rev}:{PARSE}"],
                          text=True, capture_output=True, check=True).stdout


def run_variant(name: str, rev: str) -> None:
    print(f"\n=== {name} ({rev})", flush=True)
    if (REPORTS / f"exp-{name}-dev.json").exists() and (REPORTS / f"exp-{name}-artifacts.json").exists():
        print("  已完成，跳过", flush=True)
        return
    PARSE.write_text(git_show(rev), "utf-8")
    if PROCESSED.exists():
        shutil.rmtree(PROCESSED)
    shutil.copytree(SEED, PROCESSED)
    sh(["python", "-m", "tools.ingest.reparse_all", "--workers", "4"])
    sh(["python", "-m", "app.retrieval.index"])
    sh(["python", "-m", "tools.eval_v2", "--split", "dev", "--write"])
    latest = sorted(REPORTS.glob("*-dev.json"))[-1]
    shutil.copy(latest, REPORTS / f"exp-{name}-dev.json")
    sh(["python", "-m", "tools.experiments.parser_versions", "--artifacts", name])


def artifacts(name: str) -> None:
    """对 dev 题逐条检索，数 top-1 条款里的伪影与可定位性。"""
    sys.path.insert(0, str(Path.cwd()))
    from app.retrieval.search import search_result

    data = json.loads(QUESTIONS.read_text("utf-8"))
    items = [q for q in data if q.get("split") == "dev"]
    rows, n_ans, n_art, n_loc = [], 0, 0, 0
    for q in items:
        r = search_result(q["q"])
        if r.outcome != "answer" or not r.hits:
            continue
        n_ans += 1
        top = r.hits[0].clause          # Hit 包着 Clause：文本/页码/区间都在 clause 上
        text = top.text or ""
        art = any(a in text for a in ARTIFACTS)
        loc = bool(top.page) or (top.start >= 0 and top.end > top.start)
        n_art += art
        n_loc += loc
        if art:
            rows.append({"q": q["q"], "doc": top.doc_no,
                         "head": text[:80]})
    out = {"variant": name, "answered": n_ans, "artifact": n_art, "locatable": n_loc,
           "artifact_rate": round(n_art / n_ans, 3) if n_ans else None,
           "locatable_rate": round(n_loc / n_ans, 3) if n_ans else None,
           "examples": rows[:5]}
    (REPORTS / f"exp-{name}-artifacts.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(f"  artifacts: answered={n_ans} artifact={n_art} locatable={n_loc}", flush=True)


def report() -> None:
    print(f"{'版本':<8}{'hit@1':>8}{'hit@3':>8}{'应答率':>8}{'伪影率':>8}{'可定位':>8}")
    for name, _ in REVS:
        d = json.loads((REPORTS / f"exp-{name}-dev.json").read_text("utf-8"))
        a = json.loads((REPORTS / f"exp-{name}-artifacts.json").read_text("utf-8"))
        m = d["summary"]["official"]
        print(f"{name:<8}{m['hit1']:>8.0%}{m['hit3']:>8.0%}"
              f"{m['answered']:>8.0%}{a['artifact_rate']:>8.0%}"
              f"{a['locatable_rate']:>8.0%}")


def compare(base: str = "v031", other: str = "v032c") -> None:
    """配对比较：同一批题在两版下 ok1 的变化。全一致=评测分辨不出解析质量。"""
    def rows(n):
        d = json.loads((REPORTS / f"exp-{n}-dev.json").read_text("utf-8"))
        return {r["id"]: r for r in d["rows"] if r["cat"] == "official"}
    a, b = rows(base), rows(other)
    ids = sorted(set(a) & set(b))
    both = sum(a[i]["ok1"] and b[i]["ok1"] for i in ids)
    only_a = sum(a[i]["ok1"] and not b[i]["ok1"] for i in ids)
    only_b = sum(b[i]["ok1"] and not a[i]["ok1"] for i in ids)
    neither = sum(not a[i]["ok1"] and not b[i]["ok1"] for i in ids)
    print(f"{base} vs {other}: n={len(ids)} 都对={both} 只{base}={only_a} "
          f"只{other}={only_b} 都错={neither}")
    flips = [(i, a[i]["top"], b[i]["top"]) for i in ids
             if a[i]["top"] != b[i]["top"]]
    print(f"top-1 文档发生变化：{len(flips)} 条")
    for i, x, y in flips[:8]:
        print(f"  {i}: {x} -> {y}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--artifacts")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--only", help="只跑这些版本，逗号分隔")
    a = ap.parse_args()
    if a.artifacts:
        artifacts(a.artifacts)
    elif a.run:
        if not SEED.exists():
            print("先建种子：cp -R data/processed data/processed.seed", flush=True)
            raise SystemExit(1)
        only = {x.strip() for x in (a.only or "").split(",") if x.strip()}
        for name, rev in REVS:
            if only and name not in only:
                continue
            run_variant(name, rev)
        report()
    elif a.report:
        report()
    elif a.compare:
        compare()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
