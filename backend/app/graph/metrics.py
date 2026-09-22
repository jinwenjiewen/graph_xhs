"""LangGraph 节点性能与模型 token 用量记录。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from typing import Any

from app.graph.state import AgentState, NodeMetric


@dataclass
class _TokenUsageTracker:
    """收集当前节点内所有模型调用的精确用量。"""

    model_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_known: bool = True
    output_tokens_known: bool = True
    total_tokens_known: bool = True

    def add(self, input_tokens: int | None, output_tokens: int | None, total_tokens: int | None) -> None:
        self.model_call_count += 1
        if input_tokens is None:
            self.input_tokens_known = False
        else:
            self.input_tokens += input_tokens
        if output_tokens is None:
            self.output_tokens_known = False
        else:
            self.output_tokens += output_tokens
        if total_tokens is None:
            self.total_tokens_known = False
        else:
            self.total_tokens += total_tokens

    def payload(self) -> dict[str, int | None]:
        if self.model_call_count == 0:
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return {
            "input_tokens": self.input_tokens if self.input_tokens_known else None,
            "output_tokens": self.output_tokens if self.output_tokens_known else None,
            "total_tokens": self.total_tokens if self.total_tokens_known else None,
        }


_token_usage_tracker: ContextVar[_TokenUsageTracker | None] = ContextVar(
    "node_token_usage_tracker", default=None
)


def _token_count(usage: Any, names: tuple[str, ...]) -> int | None:
    """从 dict 或 SDK 对象中读取一个非负整型 token 字段。"""
    for name in names:
        value = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _response_usage(response: Any) -> Any | None:
    """兼容 LangChain 与 OpenAI SDK 的用量字段。"""
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata:
        return usage_metadata

    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
        if token_usage:
            return token_usage

    usage = getattr(response, "usage", None)
    return usage if usage else None


def record_model_usage(response: Any) -> None:
    """把一次成功模型响应的 token 用量加入当前节点；无追踪上下文时静默跳过。"""
    tracker = _token_usage_tracker.get()
    if tracker is None:
        return

    usage = _response_usage(response)
    if usage is None:
        tracker.add(None, None, None)
        return

    input_tokens = _token_count(usage, ("input_tokens", "prompt_tokens", "prompt_token_count"))
    output_tokens = _token_count(
        usage, ("output_tokens", "completion_tokens", "completion_token_count")
    )
    total_tokens = _token_count(usage, ("total_tokens", "total_token_count"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    tracker.add(input_tokens, output_tokens, total_tokens)


NodeFunction = Callable[[AgentState], Awaitable[dict[str, object]]]


def instrument_node(node_name: str, node: NodeFunction) -> NodeFunction:
    """为节点增加 wall-clock 耗时与 token 用量，并把结果追加到持久化状态。"""

    @wraps(node)
    async def wrapped(state: AgentState) -> dict[str, object]:
        tracker = _TokenUsageTracker()
        context_token: Token[_TokenUsageTracker | None] = _token_usage_tracker.set(tracker)
        started_at = datetime.now(UTC)
        started_at_counter = perf_counter()
        try:
            update = await node(state)
        finally:
            _token_usage_tracker.reset(context_token)

        duration_ms = round((perf_counter() - started_at_counter) * 1000)
        metric: NodeMetric = {
            "node_name": node_name,
            "started_at": started_at.isoformat(),
            "duration_ms": duration_ms,
            "model_call_count": tracker.model_call_count,
            **tracker.payload(),
        }
        return {**update, "node_metrics": [*state.get("node_metrics", []), metric]}

    return wrapped
