"""Spike 2：20 题上跑 AgentScope（覆盖 + 检索两个工具），与 pipeline 对照。

用法：
    spikes/agentscope/.venv/bin/python spikes/agentscope/run20.py [n]
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import TextBlock, UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = ROOT / ".venv" / "bin" / "python"
CLI = ROOT / "spikes" / "agentscope" / "tools_cli.py"

CALLS: list[dict] = []


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    for line in (ROOT / ".env").read_text("utf-8").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _call(args: list[str]) -> dict:
    r = subprocess.run([str(MAIN_PY), str(CLI), *args], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=180)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"tool failed: {r.stderr[-400:]}")
    return json.loads(lines[-1])


def check_coverage(city: str) -> ToolChunk:
    """查这个城市/事项是否已接入。返回 covered/partial/blocked/out_of_scope 与是否允许检索。"""
    d = _call(["coverage", city])
    CALLS.append({"tool": "check_coverage", "city": city, "status": d["status"]})
    return ToolChunk(content=[TextBlock(
        text=f"{d['city']}：{d['status']}，{d['message']}；允许检索={d['allows_retrieval']}")],
        is_last=True)


def search_policy(query: str, k: int = 5) -> ToolChunk:
    """在上海市人社政策库检索条款。输入自然语言问题，返回带引用（文号/路径/页码）的条款原文。"""
    d = _call(["search", query, str(k)])
    CALLS.append({"tool": "search_policy", "query": query, "kind": d["kind"],
                  "docs": [h["doc_no"] for h in d["hits"]]})
    if d["refused"]:
        return ToolChunk(content=[TextBlock(
            text=f"拒答（{d['kind']}）：{d['reason']}\n检索范围：{d['n_docs']} 份文件 / {d['n_candidates']} 条候选")],
            is_last=True)
    blocks = [f"检索到 {len(d['hits'])} 条（{d['n_docs']} 份文件 / {d['n_candidates']} 条候选，基准日 {d['as_of']}）："]
    for h in d["hits"]:
        blocks.append(f"[{h['eid']}] {h['citation']}（{h['status']}）\n{h['text']}")
    return ToolChunk(content=[TextBlock(text="\n\n".join(blocks))], is_last=True)


SYSTEM = """你是 ClauseLedger 政策助手，只回答上海市人社/落户政策。

规则：
1. 问题涉及上海以外城市时，先调用 check_coverage 确认覆盖；覆盖不到就直接说明，不要检索、不要编造。
2. 其余问题先调用 search_policy 检索条款（可多次改写 query），再作答。
3. 只能依据检索返回的条款原文回答，每个结论后用 [E1][E2] 标注依据。
4. 检索返回“拒答”时，直接说明原因，不要用你自己的知识补充。
5. 不调用这两个以外的工具。"""


async def ask(question: str, model, toolkit) -> tuple[str, float]:
    agent = Agent(name="ClauseLedger", system_prompt=SYSTEM, model=model, toolkit=toolkit)
    t0 = time.time()
    resp = await agent.reply(UserMsg(name="user", content=question))
    text = resp.get_text_content() if hasattr(resp, "get_text_content") else str(resp.content)
    return text, time.time() - t0


async def main(n: int) -> None:
    qs = json.load(open(ROOT / "spikes/agentscope/questions20.json", encoding="utf-8"))[:n]
    pipe = {r["id"]: r for r in json.load(open(ROOT / "spikes/agentscope/pipeline20.json", encoding="utf-8"))}
    allow = PermissionDecision(PermissionBehavior.ALLOW, "只读工具")
    toolkit = Toolkit(tools=[
        FunctionTool(check_coverage, name="check_coverage", description=check_coverage.__doc__, permission=allow),
        FunctionTool(search_policy, name="search_policy", description=search_policy.__doc__, permission=allow),
    ])
    model = DeepSeekChatModel(credential=DeepSeekCredential(api_key=_key()), model="deepseek-chat", stream=False)
    rows = []
    for q in qs:
        CALLS.clear()
        text, secs = await ask(q["q"], model, toolkit)
        docs = [d for c in CALLS if c["tool"] == "search_policy" for d in c.get("docs", [])]
        refused = any(c.get("kind") not in (None, "ok", "no_direct_clause") for c in CALLS)
        row = {"id": q["id"], "q": q["q"], "gold": q["gold_doc"],
               "calls": list(CALLS), "n_calls": len(CALLS),
               "gold_in_agent": q["gold_doc"] in docs,
               "gold_in_pipeline": pipe[q["id"]]["hit5"],
               "agent_refused": refused, "secs": round(secs, 1),
               "answer_head": text[:160]}
        rows.append(row)
        print(f"{q['id']} calls={row['n_calls']} gold_agent={row['gold_in_agent']} "
              f"gold_pipe={row['gold_in_pipeline']} refused={refused} {row['secs']}s", flush=True)
    out = ROOT / "spikes/agentscope/results20.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
    a = sum(r["gold_in_agent"] for r in rows)
    p = sum(r["gold_in_pipeline"] for r in rows)
    print(f"\n== gold 出现在工具返回：agent {a}/{len(rows)}  pipeline {p}/{len(rows)} ==")
    print(f"== 平均工具调用：{sum(r['n_calls'] for r in rows)/len(rows):.1f} 次/题 ==")
    print(f"== 总耗时：{sum(r['secs'] for r in rows):.0f}s ==")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20))
