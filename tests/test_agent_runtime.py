"""Agent 运行时的静态约束。

真正的 ReAct 循环需要 `agentscope` 和模型 key，CI 里没有，所以这里只测三件
不需要网络也不需要模型的事：模块可导入、提示词里的红线还在、工具面里没有写操作。
"""
from __future__ import annotations

import pytest

from app.agent import runtime


def test_module_imports_without_agentscope():
    """没装 agentscope 时也要能 import，调用方据此回退到 pipeline。"""
    assert isinstance(runtime.available(), bool)


def test_system_prompt_keeps_the_red_lines():
    s = runtime.SYSTEM
    assert "search_policy" in s and "judge_luohu" in s
    assert "不要自己拼" in s
    assert "不要调用上面没列出的工具" in s


def test_tool_surface_has_no_write_tools():
    pytest.importorskip("agentscope")
    names = {t.name for t in runtime._tools()}
    assert names == {"search_policy", "check_coverage", "get_clause", "judge_luohu"}
    assert "draft_plan" not in names
