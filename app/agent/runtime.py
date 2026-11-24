"""AgentScope ReAct 运行时（模型决定下一步，工具仍是我们的内核）。

设计约束（和 pipeline 同一套红线）：
  - 模型只能**选工具**，不能改判定：判定只读槽位，写操作必须过审批；
  - 文号只能抄工具返回值，抄不到就留空；
  - 工具返回值自带引用（文号/路径/页码/所在节），模型没有机会"自己拼"。

依赖 `agentscope` 只在函数内部导入：没装的时候这个模块仍然可 import，
`available()` 返回 False，调用方据此回退到 pipeline。
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
6. 判分类问题（落户够不够分）调用 judge_luohu，并把它的结论原样转述，不要自己算。
7. 不要调用上面没列出的工具。"""

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
    """把 service 包成 AgentScope 工具；只读工具一律 ALLOW，不打断流程。"""
    from agentscope.message import TextBlock
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import FunctionTool, ToolChunk

    allow = PermissionDecision(PermissionBehavior.ALLOW, "只读工具，无需确认")

    def search_policy(query: str) -> ToolChunk:
        """在上海市人社政策库里检索条款。输入一句自然语言问题，返回装配后的证据
        （文号/路径/页码/所在节 + 条款原文），不是裸的 top-5 片段。"""
        t0 = time.time()
        d = T.evidence(query)
        CALLS.append({"tool": "search_policy", "arg": query,
                      "sec": round(time.time() - t0, 3),
                      "refused": d["refused"], "n": len(d["evidence"])})
        if d["refused"]:
            s = d["searched"]
            return ToolChunk(content=[TextBlock(
                text=f"拒答（{d['kind']}）：{d['reason']}\n"
                     f"检索范围：{s['n_docs']} 份文件 / {s['n_candidates']} 条候选条款")],
                is_last=True)
        lines = [f"检索到 {len(d['evidence'])} 条证据（基准日 {d['as_of']}）："]
        for e in d["evidence"]:
            lines.append(f"[{e['eid']}] {e['citation']}\n上下文：{e['context']}\n{e['text'][:800]}")
        return ToolChunk(content=[TextBlock(text="\n\n".join(lines))], is_last=True)

    def check_coverage(question: str, city: str = "") -> ToolChunk:
        """查一个问题在哪个城市、哪个状态：covered / partial / blocked / out_of_scope。
        覆盖不到的城市不要继续检索，直接告诉用户没接入。"""
        d = T.coverage(question, city or None)
        text = (f"城市：{d.get('city')}｜状态：{d.get('status')}｜"
                f"可检索：{d.get('allows_retrieval')}｜说明：{d.get('message')}")
        return ToolChunk(content=[TextBlock(text=text)], is_last=True)

    def get_clause(doc_no: str, path: str = "", page: int = 0) -> ToolChunk:
        """按文号 + 路径（如 "一、/（一）"）取一条条款原文；一次只取一条，用于核对细节。"""
        p = [x for x in path.split("/") if x] if path else None
        d = T.get_clause(doc_no, p, page or None)
        if not d.get("found"):
            return ToolChunk(content=[TextBlock(text=f"没找到：{doc_no} {path}")], is_last=True)
        c = d["clause"]
        return ToolChunk(content=[TextBlock(
            text=f"{c['citation']}\n{c['text'][:1200]}")], is_last=True)

    def judge_luohu(profile: str) -> ToolChunk:
        """落户评分判定。profile 是 JSON 字符串（degree/school_tier/gpa_rank/foreign/computer 等）。
        返回 verdict（satisfied/unsatisfied/unknown）、已知分、上限、差多少、待确认项。"""
        import json
        try:
            data = json.loads(profile) if profile.strip() else {}
        except json.JSONDecodeError:
            return ToolChunk(content=[TextBlock(text="profile 不是合法 JSON")], is_last=True)
        d = T.judge_luohu(data)
        lines = [f"结论：{d.get('verdict')}｜{d.get('headline', '')}"]
        for it in d.get("basis", [])[:6]:
            lines.append(f"- {it}")
        return ToolChunk(content=[TextBlock(text="\n".join(lines))], is_last=True)

    return [
        FunctionTool(search_policy, name="search_policy",
                     description=search_policy.__doc__, permission=allow),
        FunctionTool(check_coverage, name="check_coverage",
                     description=check_coverage.__doc__, permission=allow),
        FunctionTool(get_clause, name="get_clause",
                     description=get_clause.__doc__, permission=allow),
        FunctionTool(judge_luohu, name="judge_luohu",
                     description=judge_luohu.__doc__, permission=allow),
    ]


async def answer(question: str, *, model: str = "deepseek-chat") -> dict:
    """跑一次 ReAct，返回 {answer, calls, elapsed, model}。异常向上抛，由调用方决定回退。"""
    from agentscope.agent import Agent
    from agentscope.credential import DeepSeekCredential
    from agentscope.message import UserMsg
    from agentscope.model import DeepSeekChatModel
    from agentscope.tool import Toolkit

    CALLS.clear()
    toolkit = Toolkit(tools=_tools())
    agent = Agent(
        name="ClauseLedger", system_prompt=SYSTEM, model=DeepSeekChatModel(
            credential=DeepSeekCredential(api_key=api_key()), model=model, stream=False),
        toolkit=toolkit,
    )
    t0 = time.time()
    resp = await agent.reply(UserMsg(name="user", content=question))
    text = resp.get_text_content() if hasattr(resp, "get_text_content") else str(resp.content)
    return {"answer": text, "calls": list(CALLS), "elapsed": round(time.time() - t0, 2),
            "model": model}


def run(question: str, *, model: str = "deepseek-chat") -> dict:
    """同步入口（CLI / 测试用）。"""
    return asyncio.run(answer(question, model=model))
