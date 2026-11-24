"""Spike 2b：验证 AgentScope 的 ASK 权限能否承接门控（写待办前必须批准），
并检查模型自己起草的待办里，文号是不是编的。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.event import ConfirmResult, RequireUserConfirmEvent, UserConfirmResultEvent
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

ROOT = Path(__file__).resolve().parents[2]
MAIN_PY = ROOT / ".venv" / "bin" / "python"
CLI = ROOT / "spikes" / "agentscope" / "tools_cli.py"


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
    return json.loads(lines[-1]) if lines else {"error": r.stderr[-300:]}


def judge_luohu(profile_json: str) -> ToolChunk:
    """按上海落户 2026 评分办法判定。profile_json 例：{"degree":"phd"}"""
    d = _call(["judge", profile_json])
    text = (f"判定：{d['verdict']}；已知 {d['total_known']} 分 / 上限 {d['total_upper']} 分 / "
            f"标准 {d['standard']} 分；差 {d['gap']} 分")
    if d.get("direct"):
        text += f"；直落条件：{d['direct']}"
    return ToolChunk(content=[TextBlock(text=text)], is_last=True)


def draft_plan(run_id: str, items_json: str) -> ToolChunk:
    """把办理计划写入待办（有副作用）。items_json：[{"text": "...", "doc_no": "..."}]"""
    d = _call(["draft", run_id, items_json])
    return ToolChunk(content=[TextBlock(text=json.dumps(d, ensure_ascii=False))], is_last=True)


SYSTEM = """你是 ClauseLedger 政策助手。用户问落户时：
1. 先调用 judge_luohu 做判定（profile 从用户描述里提取，缺失字段不要猜）。
2. 判定为 direct 或 satisfied 时，调用 draft_plan 写 3 条办理计划待办。
3. 待办里的 doc_no 只能来自 judge_luohu 或检索结果，**不得自己编文号**。"""


async def stream_collect(agent: Agent, payload) -> tuple[str, Msg | None, RequireUserConfirmEvent | None]:
    text: list[str] = []
    final: Msg | None = None
    pending: RequireUserConfirmEvent | None = None
    async for ev in agent.reply_stream(payload, yield_final_msg=True):
        if isinstance(ev, RequireUserConfirmEvent):
            pending = ev
            break
        if isinstance(ev, Msg):
            final = ev
            continue
        t = getattr(ev, "text", None)
        if t:
            text.append(t)
    return "".join(text), final, pending


async def main() -> None:
    allow = PermissionDecision(PermissionBehavior.ALLOW, "只读判定")
    toolkit = Toolkit(tools=[
        FunctionTool(judge_luohu, name="judge_luohu", description=judge_luohu.__doc__, permission=allow),
        FunctionTool(draft_plan, name="draft_plan", description=draft_plan.__doc__),
    ])
    model = DeepSeekChatModel(credential=DeepSeekCredential(api_key=_key()), model="deepseek-chat", stream=False)
    agent = Agent(name="ClauseLedger", system_prompt=SYSTEM, model=model, toolkit=toolkit)

    before = _call(["count", "todo"])["count"]
    print("批准前待办条数：", before)

    q = "我是博士研究生，想落户上海，帮我出一份办理计划。"
    print("=== 问：", q)
    text, _final, pending = await stream_collect(agent, UserMsg(name="user", content=q))
    print(f"\n--- 第一次回复：{text[:200] or '(无文本，直接停在审批)'}")
    if pending is None:
        print("!! 没有等到审批请求")
        return
    print(f"\n--- 暂停在工具：{pending.tool_calls[0].name} ---")
    print("批准后待办条数（应仍为", before, "）：", _call(["count", "todo"])["count"])

    tc = pending.tool_calls[0]
    args = json.loads(tc.input) if isinstance(tc.input, str) else tc.input
    items = json.loads(args["items_json"])
    print(f"\n模型起草的 {len(items)} 条待办：")
    for it in items:
        e = _call(["exists", it["doc_no"]])
        print(f"  - {it['text'][:34]}… | {it['doc_no']} → 存在={e['exists']}")

    ev = UserConfirmResultEvent(reply_id=pending.reply_id,
                                confirm_results=[ConfirmResult(confirmed=True, tool_call=tc)])
    text2, final2, _ = await stream_collect(agent, ev)
    print(f"\n--- 批准后回复：{(text2 or (final2.get_text_content() if final2 else ''))[:300]}")
    after = _call(["count", "todo"])["count"]
    print(f"\n批准后待办条数：{after}（新增 {after - before}）")

    d = _call(["draft", args["run_id"], args["items_json"]])
    print("同一批再写一次：", json.dumps(d, ensure_ascii=False))
    print("最终待办条数：", _call(["count", "todo"])["count"])


if __name__ == "__main__":
    asyncio.run(main())
