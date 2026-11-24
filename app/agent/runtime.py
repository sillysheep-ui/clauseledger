"""AgentScope ReAct 运行时（模型决定下一步，工具仍是我们的内核）。

设计约束（和 pipeline 同一套红线）：
  - 工具只给"需要模型临场决定"的能力：搜什么、要不要读全、要不要追问；
  - 必须发生的阶段（判定、核验、门控）留在代码里，**不做成工具**——否则等于让模型
    有权决定这些事发不发生；
  - 证据"少而整"：按条数与总字数控制，不按每条截断；截断必须显式标 truncated；
  - 文号只能抄工具返回值，抄不到就留空；
  - 写操作（draft_plan）不在工具面里，等 gate_action 接好再放。

依赖 `agentscope` 只在函数内部导入：没装时这个模块仍可 import，`available()` 返回 False。
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from app.tools import service as T

ROOT = Path(__file__).resolve().parents[2]

SYSTEM = """你是 ClauseLedger 的政策助手，只回答上海市人社、落户相关政策。

工作规则：
1. 回答任何政策问题前，必须先调用 search_policy 检索条款；可以多次调用。
2. 只能依据 search_policy 返回的条款原文回答，不得使用你自己的知识补充事实。
3. 每个结论后面用 [E1]、[E2] 标注依据的是哪一条。
4. 引用文号时只能用返回值里出现过的文号，不要自己拼；写不准就留空并说明。
5. 如果检索返回“拒答”，就直接说明原因（覆盖不到 / 不在范围内 / 证据不足），不要编造。
6. 只有证据里标了“（原文 N 字，这里只给前 1200 字）”时，才用 read_clause 把这条读全；
   没标就不要重复取。
7. 如果问题里带有【系统判定】段落，那是代码算出来的结论（分数、差多少、待确认项），
   原样解释它，不要自己重算，也不要改结论。
8. 不要调用上面没列出的工具。"""

CALLS: list[dict] = []


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def available() -> bool:
    try:
        import agentscope  # noqa: F401
    except Exception:
        return False
    return bool(api_key())


def _tools():
    """三个只读工具。判定/核验/门控刻意不在里面。"""
    from agentscope.message import TextBlock
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import FunctionTool, ToolChunk

    allow = PermissionDecision(PermissionBehavior.ALLOW, "只读工具，无需确认")

    def search_policy(query: str) -> ToolChunk:
        """在上海市人社政策库里检索条款。返回少而整的证据（每条完整条款 + 文号/路径/
        页码/所在节），覆盖不到时直接返回拒答原因与检索范围。"""
        t0 = time.time()
        d = T.evidence_compact(query)
        CALLS.append({"tool": "search_policy", "arg": query,
                      "sec": round(time.time() - t0, 3),
                      "refused": d["refused"], "n": d["n"]})
        if d["refused"]:
            s = d["searched"]
            return ToolChunk(content=[TextBlock(
                text=f"拒答（{d['kind']}）：{d['reason']}\n"
                     f"检索范围：{s['n_docs']} 份文件 / {s['n_candidates']} 条候选条款")],
                is_last=True)
        head = (f"检索到 {d['n']} 条证据（另有 {d['n_available'] - d['n']} 条未装入，"
                f"总 {d['budget_used']} 字，基准日 {d['as_of']}）：")
        lines = [head]
        for e in d["evidence"]:
            mark = (f"（原文 {e['chars']} 字，这里只给前 {len(e['text'])} 字，"
                    f"需要时用 read_clause 读全）") if e["truncated"] else ""
            lines.append(f"[{e['eid']}] {e['citation']}\n上下文：{e['context']}\n"
                         f"{e['text']}{mark}")
        return ToolChunk(content=[TextBlock(text="\n\n".join(lines))], is_last=True)

    def check_coverage(question: str, city: str = "") -> ToolChunk:
        """查一个问题在哪个城市、哪个状态：covered / partial / blocked / out_of_scope。
        覆盖不到的城市不要继续检索，直接告诉用户没接入。"""
        d = T.coverage(question, city or None)
        text = (f"城市：{d.get('city')}｜状态：{d.get('status')}｜"
                f"可检索：{d.get('allows_retrieval')}｜说明：{d.get('message')}")
        return ToolChunk(content=[TextBlock(text=text)], is_last=True)

    def read_clause(doc_no: str, path: str = "", page: int = 0) -> ToolChunk:
        """把某一条证据读全（只用于证据里标了 truncated 的条款）。
        doc_no/path/page 三个参数从证据块里原样复制，不要自己拼。"""
        p = [x for x in path.split("/") if x] if path else None
        d = T.get_clause(doc_no, p, page or None)
        if not d.get("found"):
            return ToolChunk(content=[TextBlock(text=f"没找到：{doc_no} {path}")], is_last=True)
        c = d["clause"] if "clause" in d else d
        return ToolChunk(content=[TextBlock(
            text=f"{c['citation']}\n{c['text']}")], is_last=True)

    return [
        FunctionTool(search_policy, name="search_policy",
                     description=search_policy.__doc__, permission=allow),
        FunctionTool(check_coverage, name="check_coverage",
                     description=check_coverage.__doc__, permission=allow),
        FunctionTool(read_clause, name="read_clause",
                     description=read_clause.__doc__, permission=allow),
    ]


def _verdict_text(v: dict) -> str:
    lines = [f"结论：{v.get('verdict')}",
             f"已知 {v.get('total_known')} / 上限 {v.get('total_upper')} / "
             f"标准 {v.get('standard')} / 差 {v.get('gap')}"]
    for it in (v.get("items") or [])[:8]:
        if it.get("state") == "satisfied" and it.get("points"):
            lines.append(f"- {it['label']} +{it['points']}（第 {it.get('page')} 页）")
    return "\n".join(lines)


async def answer(question: str, *, profile: dict | None = None,
                 model: str = "deepseek-chat") -> dict:
    """跑一次 ReAct。

    判定不是工具：调用方给了 profile，就在循环**之前**用代码算好，作为事实交给模型解释。
    返回 {answer, calls, elapsed, model, verdict}；异常向上抛，由调用方决定回退。
    """
    from agentscope.agent import Agent
    from agentscope.credential import DeepSeekCredential
    from agentscope.message import UserMsg
    from agentscope.model import DeepSeekChatModel
    from agentscope.tool import Toolkit

    CALLS.clear()
    verdict = T.judge_luohu(profile) if profile else None
    content = question
    if verdict:
        content = f"{question}\n\n【系统判定（代码算出，不要重算，原样解释）】\n{_verdict_text(verdict)}"

    toolkit = Toolkit(tools=_tools())
    agent = Agent(
        name="ClauseLedger", system_prompt=SYSTEM, model=DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=api_key()), model=model, stream=False),
        toolkit=toolkit,
    )
    t0 = time.time()
    resp = await agent.reply(UserMsg(name="user", content=content))
    text = resp.get_text_content() if hasattr(resp, "get_text_content") else str(resp.content)
    return {"answer": text, "calls": list(CALLS), "elapsed": round(time.time() - t0, 2),
            "model": model, "verdict": verdict}


def run(question: str, *, profile: dict | None = None, model: str = "deepseek-chat") -> dict:
    """同步入口（CLI / 测试用）。"""
    return asyncio.run(answer(question, profile=profile, model=model))
