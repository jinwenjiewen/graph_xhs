"""用真实检查点和 HTTP 接口验证选题子图；模型服务全部离线替换。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.api.v1.workflow import router
from app.graph.metrics import instrument_node, record_model_usage
from app.graph.nodes import content_nodes, topic_nodes
from app.graph.state import AgentState
from app.graph.subgraphs.topic_selection import build_topic_selection_subgraph
from app.graph.workflow import build_workflow
from app.services.volcengine_llm import LLMServiceError


TOPICS = ["asyncio 并发控制", "任务取消与清理", "异常传播与处理"]


def _record_usage() -> None:
    record_model_usage(SimpleNamespace(usage_metadata={
        "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
    }))


class OfflineTextService:
    def __init__(self) -> None:
        self.topic_calls: list[str] = []
        self.draft_calls: list[tuple[str, str]] = []
        self.visual_calls: list[str] = []

    async def plan_topics(self, direction: str) -> list[str]:
        self.topic_calls.append(direction)
        _record_usage()
        return list(TOPICS)

    async def write_draft(self, topic: str, feedback: str) -> str:
        self.draft_calls.append((topic, feedback))
        _record_usage()
        return f"草稿 {len(self.draft_calls)}：{topic}；意见：{feedback}"

    async def extract_visual_plan(self, article: str) -> list[dict[str, str]]:
        self.visual_calls.append(article)
        _record_usage()
        return [
            {"knowledge_point": f"知识点 {i}", "image_prompt": f"技术配图 {i}"}
            for i in range(3)
        ]


class OfflineImageService:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def generate_images(self, prompts: list[str]) -> list[str]:
        self.calls.append(list(prompts))
        for _ in prompts:
            _record_usage()
        return [f"https://images.example.test/{i}.png" for i in range(len(prompts))]


@pytest.fixture
def offline_workflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    text = OfflineTextService()
    images = OfflineImageService()
    monkeypatch.setattr(topic_nodes, "_text_llm", text)
    monkeypatch.setattr(content_nodes, "_text_llm", text)
    monkeypatch.setattr(content_nodes, "_visual_asset_generator", images)
    saver = InMemorySaver()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.content_graph = build_workflow(saver)
    return SimpleNamespace(app=app, saver=saver, text=text, images=images)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


def _body(response: httpx.Response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return response.json()


def _assert_pause(body: dict[str, Any], node: str, status: str) -> None:
    assert body["next"] == [node]
    assert body["status"] == status
    assert body["awaiting_human_input"] is True
    assert body["interrupted"] is True


def test_start_exposes_child_pause_and_rejects_invalid_actions(offline_workflow: SimpleNamespace) -> None:
    async def scenario() -> None:
        async with _client(offline_workflow.app) as client:
            started = _body(await client.post("/api/v1/workflow/start", json={
                "topic_direction": "Python 异步编程",
            }), 201)
            thread = started["thread_id"]
            _assert_pause(started, "human_select_node", "awaiting_topic_selection")
            assert started["generated_topics"] == started["state"]["generated_topics"] == TOPICS
            assert [m["node_name"] for m in started["state"]["node_metrics"]] == ["plan_topics"]

            saved = _body(await client.get(f"/api/v1/workflow/state/{thread}"))
            assert saved["state"] == started["state"]
            listing = _body(await client.get("/api/v1/workflow/threads"))["threads"]
            assert len(listing) == 1
            assert listing[0]["thread_id"] == thread
            _assert_pause(listing[0], "human_select_node", "awaiting_topic_selection")

            history = _body(await client.get(f"/api/v1/workflow/history/{thread}"))["history"]
            assert history[-1]["state"] == started["state"]
            assert history[-1]["next"] == ["human_select_node"]
            assert any(item["checkpoint_ns"] for item in history)
            assert any("generated_topics" not in item["state"] for item in history)
            assert all(item["checkpoint_id"] for item in history)

            # 所有无效请求都应保留同一个人工中断点，不得生成正文。
            _body(await client.post(f"/api/v1/workflow/continue/{thread}"), 409)
            for action in ("approve", "reject"):
                _body(await client.post(f"/api/v1/workflow/resume/{thread}", json={
                    "action": action, "data": {"human_feedback": "请改写"},
                }), 409)
            for topic in ("不在候选列表中", None, [TOPICS[0]]):
                _body(await client.post(f"/api/v1/workflow/resume/{thread}", json={
                    "action": "select_topic", "data": {"selected_topic": topic},
                }), 422)
            unchanged = _body(await client.get(f"/api/v1/workflow/state/{thread}"))
            assert unchanged == saved
            assert offline_workflow.text.topic_calls == ["Python 异步编程"]
            assert offline_workflow.text.draft_calls == []

    asyncio.run(scenario())


def test_rebuilt_graph_resumes_child_then_rewrites_and_completes(offline_workflow: SimpleNamespace) -> None:
    async def scenario() -> None:
        async with _client(offline_workflow.app) as client:
            started = _body(await client.post("/api/v1/workflow/start", json={
                "topic_direction": "Python 异步编程",
            }), 201)
            thread = started["thread_id"]
            resume = f"/api/v1/workflow/resume/{thread}"
            history_url = f"/api/v1/workflow/history/{thread}"
            paused_history = _body(await client.get(history_url))["history"][-1]
            root = await offline_workflow.app.state.content_graph.aget_state(
                {"configurable": {"thread_id": thread}}, subgraphs=True,
            )
            assert root.next == ("topic_selection",)
            assert root.tasks[0].state.next == ("human_select_node",)
            assert root.tasks[0].state.config["configurable"]["checkpoint_ns"]

            # 模拟服务重启：新编译对象只能通过同一个持久化器找回子图输入。
            offline_workflow.app.state.content_graph = build_workflow(offline_workflow.saver)
            restored = _body(await client.get(f"/api/v1/workflow/state/{thread}"))
            assert restored["state"] == started["state"]
            draft = _body(await client.post(resume, json={
                "action": "select_topic", "data": {"selected_topic": TOPICS[1]},
            }))
            _assert_pause(draft, "human_review_node", "awaiting_review")
            assert draft["state"]["selected_topic"] == TOPICS[1]
            assert draft["state"]["node_metrics"][0] == started["state"]["node_metrics"][0]
            assert [m["node_name"] for m in draft["state"]["node_metrics"]] == [
                "plan_topics", "human_select_node", "generate_draft",
            ]
            _body(await client.post(f"/api/v1/workflow/continue/{thread}"), 409)
            _body(await client.post(resume, json={
                "action": "select_topic", "data": {"selected_topic": TOPICS[0]},
            }), 409)
            _body(await client.post(resume, json={"action": "reject", "data": {}}), 422)

            feedback = "补充取消任务时的清理代码"
            revised = _body(await client.post(resume, json={
                "action": "reject", "data": {"human_feedback": feedback},
            }))
            _assert_pause(revised, "human_review_node", "awaiting_review")
            assert revised["state"]["article_content"] != draft["state"]["article_content"]
            assert offline_workflow.text.draft_calls == [(TOPICS[1], ""), (TOPICS[1], feedback)]

            completed = _body(await client.post(resume, json={"action": "approve"}))
            assert completed["status"] == "completed"
            assert completed["next"] == []
            assert completed["awaiting_human_input"] is False
            state = completed["state"]
            assert state["final_content"] == revised["state"]["article_content"]
            assert offline_workflow.text.visual_calls == [state["final_content"]]
            assert offline_workflow.images.calls == [state["image_prompts"]]
            assert len(state["visual_points"]) == len(state["image_urls"]) == 3
            assert offline_workflow.text.topic_calls == ["Python 异步编程"]
            metrics = state["node_metrics"]
            assert [m["node_name"] for m in metrics] == [
                "plan_topics", "human_select_node", "generate_draft", "human_review_node",
                "generate_draft", "human_review_node", "extract_visual_points", "generate_images",
            ]
            assert metrics[:3] == draft["state"]["node_metrics"]
            assert [m["model_call_count"] for m in metrics] == [1, 0, 1, 0, 1, 0, 1, 3]
            assert [m["total_tokens"] for m in metrics] == [15, 0, 15, 0, 15, 0, 15, 45]

            history = _body(await client.get(history_url))["history"]
            original_pause = next(item for item in history if (
                item["checkpoint_id"] == paused_history["checkpoint_id"]
                and item["checkpoint_ns"] == paused_history["checkpoint_ns"]
            ))
            assert original_pause == paused_history
            assert "selected_topic" not in original_pause["state"]
            assert history[-1]["status"] == "completed"
            assert _body(await client.get(f"/api/v1/workflow/state/{thread}"))["state"] == state
            _body(await client.post(f"/api/v1/workflow/continue/{thread}"), 409)

    asyncio.run(scenario())


def test_topic_subgraph_runs_independently_and_preserves_existing_metrics(offline_workflow: SimpleNamespace) -> None:
    async def scenario() -> None:
        graph = build_topic_selection_subgraph(InMemorySaver())
        config = {"configurable": {"thread_id": "standalone-selection"}}
        upstream_metric = {
            "node_name": "upstream", "started_at": "2026-01-01T00:00:00+00:00",
            "duration_ms": 7, "model_call_count": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        }
        await graph.ainvoke({
            "topic_direction": "独立子图", "status": "planning_topics",
            "node_metrics": [upstream_metric],
        }, config)
        paused = await graph.aget_state(config)
        assert paused.next == ("human_select_node",)
        assert paused.values["generated_topics"] == TOPICS
        await graph.aupdate_state(config, {"selected_topic": TOPICS[0]})
        result = await graph.ainvoke(None, config)
        assert result["selected_topic"] == TOPICS[0]
        assert result["status"] == "writing_draft"
        assert result["node_metrics"][0] == upstream_metric
        assert [m["node_name"] for m in result["node_metrics"]] == [
            "upstream", "plan_topics", "human_select_node",
        ]
        assert (await graph.aget_state(config)).next == ()
        assert offline_workflow.text.draft_calls == []

    asyncio.run(scenario())


def test_continue_retries_failed_child_and_still_stops_for_selection(
    offline_workflow: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        attempts: list[str] = []
        plan_topics = offline_workflow.text.plan_topics

        async def fail_once(direction: str) -> list[str]:
            attempts.append(direction)
            if len(attempts) == 1:
                raise LLMServiceError("临时模型故障")
            return await plan_topics(direction)

        monkeypatch.setattr(offline_workflow.text, "plan_topics", fail_once)
        async with _client(offline_workflow.app) as client:
            _body(await client.post("/api/v1/workflow/start", json={
                "topic_direction": "失败后恢复",
            }), 503)
            threads = _body(await client.get("/api/v1/workflow/threads"))["threads"]
            assert len(threads) == 1
            assert threads[0]["awaiting_human_input"] is False
            thread = threads[0]["thread_id"]
            offline_workflow.app.state.content_graph = build_workflow(offline_workflow.saver)
            resumed = _body(await client.post(f"/api/v1/workflow/continue/{thread}"))
            _assert_pause(resumed, "human_select_node", "awaiting_topic_selection")
            assert resumed["state"]["generated_topics"] == TOPICS
            assert [m["node_name"] for m in resumed["state"]["node_metrics"]] == ["plan_topics"]
            assert attempts == ["失败后恢复", "失败后恢复"]
            assert offline_workflow.text.draft_calls == []
            _body(await client.post(f"/api/v1/workflow/continue/{thread}"), 409)

    asyncio.run(scenario())


@pytest.mark.parametrize("selection_node", ["human_select_node", "human_selection_node"])
def test_flat_graph_checkpoint_survives_upgrade(
    offline_workflow: SimpleNamespace, selection_node: str,
) -> None:
    async def scenario() -> None:
        legacy = StateGraph(AgentState)
        legacy.add_node("plan_topics", instrument_node("plan_topics", topic_nodes.plan_topics))
        legacy.add_node(selection_node, instrument_node(
            selection_node, getattr(topic_nodes, selection_node),
        ))
        legacy.add_node("generate_draft", content_nodes.generate_draft)
        legacy.add_edge(START, "plan_topics")
        legacy.add_edge("plan_topics", selection_node)
        legacy.add_edge(selection_node, "generate_draft")
        legacy.add_edge("generate_draft", END)
        graph = legacy.compile(
            checkpointer=offline_workflow.saver, interrupt_before=[selection_node],
        )
        thread = f"legacy-{selection_node}"
        await graph.ainvoke(
            {"topic_direction": "旧会话", "status": "planning_topics"},
            {"configurable": {"thread_id": thread}},
        )
        offline_workflow.app.state.content_graph = build_workflow(offline_workflow.saver)
        async with _client(offline_workflow.app) as client:
            saved = _body(await client.get(f"/api/v1/workflow/state/{thread}"))
            _assert_pause(saved, selection_node, "awaiting_topic_selection")
            _body(await client.post(f"/api/v1/workflow/continue/{thread}"), 409)
            resumed = _body(await client.post(f"/api/v1/workflow/resume/{thread}", json={
                "action": "select_topic", "data": {"selected_topic": TOPICS[0]},
            }))
            _assert_pause(resumed, "human_review_node", "awaiting_review")
            names = [m["node_name"] for m in resumed["state"]["node_metrics"]]
            assert names[0] == "plan_topics"
            assert names[1] in {"human_select_node", "human_selection_node"}
            assert names[2:] == ["generate_draft"]
            assert resumed["state"]["node_metrics"][0] == saved["state"]["node_metrics"][0]
            assert offline_workflow.text.topic_calls == ["旧会话"]
            assert offline_workflow.text.draft_calls == [(TOPICS[0], "")]

    asyncio.run(scenario())
