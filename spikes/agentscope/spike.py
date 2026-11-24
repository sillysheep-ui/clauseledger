"""AgentScope spike：把 ClauseLedger 的检索包成一个工具，交给 ReAct 循环。

跑法：
    spikes/agentscope/.venv/bin/python spikes/agentscope/spike.py "失业保险金能领多久"
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import TextBlock, UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = ROOT / ".venv" / "bin" / "python"
CLI = ROOT / "spikes" / "agentscope" / "retrieval_cli.py"


def _key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def search_policy(query: str, k: int = 5) -> ToolChunk:
    """在上海市人社政策库里检索条款。输入一句自然语言问题，返回带引用（文号/路径/页码）的条款原文。"""
    r = subprocess.run(
        [str(MAIN_PY), str(CLI), query, str(k)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        return ToolChunk(content=[TextBlock(text=f"检索失败：{r.stderr[-500:]}")],
                             is_last=True)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("{\"kind\"")]
    if not line:
        return ToolChunk(content=[TextBlock(text="检索失败：没有解析到 JSON")], is_last=True)
    data = json.loads(line[-1])
    print(f"  [tool] search_policy({query!r}) -> kind={data['kind']} "
          f"refused={data['refused']} hits={len(data['hits'])}", flush=True)
    if data["refused"]:
        return ToolChunk(
            content=[TextBlock(
                text=f"拒答（{data['kind']}）：{data['reason']}\n"
                     f"检索范围：{data['n_docs']} 份文件 / {data['n_candidates']} 条候选条款")],
            is_last=True,
        )
    blocks = [f"检索到 {len(data['hits'])} 条（检索范围 {data['n_docs']} 份文件 / "
              f"{data['n_candidates']} 条候选，基准日 {data['as_of']}）："]
    for h in data["hits"]:
        blocks.append(f"[{h['eid']}] {h['citation']}（{h['status']}）\n{h['text']}")
    return ToolChunk(content=[TextBlock(text="\n\n".join(blocks))], is_last=True)


SYSTEM = """你是 ClauseLedger 的政策助手，只回答上海市人社/落户相关政策。

工作规则：
1. 回答任何政策问题前，必须先调用 search_policy 检索条款；可以多次调用。
2. 只能依据 search_policy 返回的条款原文回答，不得使用你自己的知识补充事实。
3. 每个结论后面用 [E1]、[E2] 标注依据的是哪一条。
4. 如果检索返回“拒答”，就直接说明原因（覆盖不到 / 不在范围内 / 证据不足），不要编造。
5. 不要调用 search_policy 之外的工具。"""


async def run(question: str) -> None:
    model = DeepSeekChatModel(
        credential=DeepSeekCredential(api_key=_key()),
        model="deepseek-chat",
        stream=False,
    )
    allow = PermissionDecision(PermissionBehavior.ALLOW, "只读检索工具，无需确认")
    toolkit = Toolkit(tools=[FunctionTool(search_policy, name="search_policy",
                                          description=search_policy.__doc__,
                                          permission=allow)])
    agent = Agent(name="ClauseLedger", system_prompt=SYSTEM, model=model, toolkit=toolkit)
    print(f"\n=== 问：{question} ===", flush=True)
    resp = await agent.reply(UserMsg(name="user", content=question))
    print("\n--- 最终回答 ---")
    print(resp.get_text_content() if hasattr(resp, "get_text_content") else resp.content)


if __name__ == "__main__":
    qs = sys.argv[1:] or ["失业保险金能领多久", "硕士毕业落户能加多少分"]
    for q in qs:
        asyncio.run(run(q))
