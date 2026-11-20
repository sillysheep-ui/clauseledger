"""每个 step 一个 OTel span，落本地 JSONL——**审计看事件流，调试看 trace**。

为什么两套都要：
- `events` 是业务语义（step_started / gate_opened / step_finished），审计要的是它；
- span 是调用树（父子、耗时、属性），排查"这一步为什么慢/为什么没走到"要的是它。

这里不引后端：`FileSpanExporter` 把 span 写成 JSONL（`data/runs/traces.jsonl`），
真接 Phoenix/Langfuse 时换一个 exporter 即可，`span()` 调用点不用改。
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_TRACE_FILE = lambda: Path(os.environ.get("CLAUSELEDGER_TRACE", "data/runs/traces.jsonl"))  # noqa: E731

_enabled: bool | None = None
_provider = None


def enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = os.environ.get("CLAUSELEDGER_OTEL", "1") not in ("0", "off", "false")
        if _enabled:
            try:
                import opentelemetry.sdk.trace  # noqa: F401
            except Exception:  # noqa: BLE001 - 没装就静默降级，不影响业务
                _enabled = False
    return _enabled


def _setup():
    global _provider
    if _provider is not None or not enabled():
        return _provider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    class FileSpanExporter(SpanExporter):
        def export(self, spans) -> SpanExportResult:
            p = _TRACE_FILE()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                for s in spans:
                    ctx = s.get_span_context()
                    f.write(json.dumps({
                        "trace_id": format(ctx.trace_id, "032x"),
                        "span_id": format(ctx.span_id, "016x"),
                        "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
                        "name": s.name,
                        "start_ns": s.start_time,
                        "end_ns": s.end_time,
                        "status": s.status.status_code.name,
                        "attrs": dict(s.attributes or {}),
                    }, ensure_ascii=False) + "\n")
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    _provider = TracerProvider(resource=Resource.create({"service.name": "clauseledger"}))
    _provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter()))
    return _provider


def tracer():
    p = _setup()
    if p is None:
        return None
    return p.get_tracer("clauseledger")


@contextmanager
def span(name: str, **attrs: Any):
    """没有 OTel 时是空上下文，调用点不需要判空。"""
    t = tracer()
    if t is None:
        yield None
        return
    with t.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    if not enabled():
        return None
    from opentelemetry import trace
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.trace_id else None


@contextmanager
def use_trace(trace_id: str | None):
    """让本进程产生的 span 都挂到同一个 trace 上——跨进程恢复也能接回同一棵树。

    OTel 的 trace_id 是 128 bit；我们把它存在 `RunState.trace_id` 里，
    恢复时用 NonRecordingSpan 伪造一个远端父上下文，子 span 就会继承这个 id。
    """
    if not trace_id or not enabled():
        yield
        return
    from opentelemetry import context as ctx_api
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

    sc = SpanContext(trace_id=int(trace_id, 16), span_id=7, is_remote=True,
                     trace_flags=TraceFlags(TraceFlags.SAMPLED))
    token = ctx_api.attach(set_span_in_context(NonRecordingSpan(sc)))
    try:
        yield
    finally:
        ctx_api.detach(token)
