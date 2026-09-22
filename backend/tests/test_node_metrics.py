"""图节点性能与模型用量记录的单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.graph.metrics import instrument_node, record_model_usage


@pytest.mark.asyncio
async def test_instrumented_node_persists_exact_usage_and_duration() -> None:
    """LangChain usage_metadata 应被原样累计到节点状态。"""

    async def node(_: dict[str, object]) -> dict[str, object]:
        record_model_usage(
            SimpleNamespace(
                usage_metadata={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
            )
        )
        return {"status": "done"}

    update = await instrument_node("plan_topics", node)(
        {"topic_direction": "AI 内容创作", "status": "planning_topics"}
    )

    metric = update["node_metrics"][0]
    assert metric["node_name"] == "plan_topics"
    assert metric["duration_ms"] >= 0
    assert metric["model_call_count"] == 1
    assert metric["input_tokens"] == 12
    assert metric["output_tokens"] == 8
    assert metric["total_tokens"] == 20


@pytest.mark.asyncio
async def test_instrumented_node_keeps_existing_metrics_and_counts_all_model_calls() -> None:
    """重写草稿等重复节点需要保留旧记录，并累加本次多次模型调用。"""

    async def node(_: dict[str, object]) -> dict[str, object]:
        record_model_usage(SimpleNamespace(usage={"prompt_tokens": 3, "completion_tokens": 2}))
        record_model_usage(SimpleNamespace(usage={"prompt_tokens": 7, "completion_tokens": 5}))
        return {"status": "done"}

    old_metric = {
        "node_name": "plan_topics",
        "started_at": "2026-01-01T00:00:00+00:00",
        "duration_ms": 12,
        "model_call_count": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
    }
    update = await instrument_node("write_draft", node)(
        {
            "topic_direction": "AI 内容创作",
            "status": "writing_draft",
            "node_metrics": [old_metric],
        }
    )

    assert update["node_metrics"][0] == old_metric
    metric = update["node_metrics"][1]
    assert metric["model_call_count"] == 2
    assert metric["input_tokens"] == 10
    assert metric["output_tokens"] == 7
    assert metric["total_tokens"] == 17


@pytest.mark.asyncio
async def test_instrumented_node_marks_missing_usage_without_estimating() -> None:
    """图片服务等未返回 usage 的调用不能以估算 token 误导用户。"""

    async def node(_: dict[str, object]) -> dict[str, object]:
        record_model_usage(SimpleNamespace(data=[]))
        return {"status": "done"}

    update = await instrument_node("generate_images", node)(
        {"topic_direction": "AI 内容创作", "status": "generating_images"}
    )

    metric = update["node_metrics"][0]
    assert metric["model_call_count"] == 1
    assert metric["input_tokens"] is None
    assert metric["output_tokens"] is None
    assert metric["total_tokens"] is None


@pytest.mark.asyncio
async def test_human_node_has_zero_model_calls_and_tokens() -> None:
    """人工节点也有耗时，但不应显示为模型调用。"""

    async def node(_: dict[str, object]) -> dict[str, object]:
        return {"status": "topic_selected"}

    update = await instrument_node("human_selection_node", node)(
        {"topic_direction": "AI 内容创作", "status": "awaiting_topic_selection"}
    )

    metric = update["node_metrics"][0]
    assert metric["model_call_count"] == 0
    assert metric["input_tokens"] == 0
    assert metric["output_tokens"] == 0
    assert metric["total_tokens"] == 0
