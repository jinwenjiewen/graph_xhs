"""工作流 HTTP 接口测试，不依赖 PostgreSQL 或真实 LLM 服务。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.services.volcengine_llm import LLMRateLimitError


@dataclass
class FakeSnapshot:
    """供接口测试使用的最小 LangGraph 状态快照。"""

    values: dict[str, Any]
    next: tuple[str, ...]
    config: dict[str, dict[str, str]]
    metadata: dict[str, Any]
    created_at: str


class FakeWorkflow:
    """模拟工作流状态机，覆盖路由层需要的 LangGraph 接口。"""

    topics = ["LangGraph入门", "AI Agent实战", "Python高并发"]

    def __init__(self) -> None:
        self._snapshots: dict[str, list[FakeSnapshot]] = {}
        self._sequence = 0

    def _thread_id(self, config: dict[str, dict[str, str]]) -> str:
        return config["configurable"]["thread_id"]

    def _save(self, thread_id: str, values: dict[str, Any], next_nodes: tuple[str, ...]) -> None:
        self._sequence += 1
        snapshot = FakeSnapshot(
            # 快照必须是当时状态的副本，否则之后的更新会污染历史断言。
            values=deepcopy(values),
            next=next_nodes,
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": f"checkpoint-{self._sequence}",
                }
            },
            metadata={"step": self._sequence},
            created_at=(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self._sequence)).isoformat(),
        )
        self._snapshots.setdefault(thread_id, []).append(snapshot)

    async def ainvoke(self, values: dict[str, Any], config: dict[str, dict[str, str]]) -> None:
        thread_id = self._thread_id(config)
        self._save(
            thread_id,
            {
                **values,
                "generated_topics": self.topics,
                "status": "awaiting_topic_selection",
            },
            ("human_selection_node",),
        )

    async def aget_state(self, config: dict[str, dict[str, str]]) -> FakeSnapshot:
        thread_id = self._thread_id(config)
        snapshots = self._snapshots.get(thread_id)
        if not snapshots:
            return FakeSnapshot({}, (), config, {}, "")
        return snapshots[-1]

    async def aget_state_history(
        self, config: dict[str, dict[str, str]], *, limit: int | None = None
    ) -> AsyncIterator[FakeSnapshot]:
        thread_id = self._thread_id(config)
        # 与 LangGraph 保持一致：历史读取从最新检查点开始。
        snapshots = reversed(self._snapshots.get(thread_id, []))
        for index, snapshot in enumerate(snapshots):
            if limit is not None and index >= limit:
                return
            yield snapshot

    async def aupdate_state(
        self, config: dict[str, dict[str, str]], update: dict[str, Any]
    ) -> None:
        current = await self.aget_state(config)
        self._save(
            self._thread_id(config),
            {**current.values, **update},
            current.next,
        )

    async def astream(
        self, _: None, config: dict[str, dict[str, str]], stream_mode: str
    ) -> AsyncIterator[dict[str, object]]:
        assert stream_mode == "updates"
        current = await self.aget_state(config)
        thread_id = self._thread_id(config)

        # 用当前中断节点模拟续跑后的两类行为：选题后写初稿，审稿后完成或重写。
        if current.next == ("human_selection_node",):
            self._save(
                thread_id,
                {
                    **current.values,
                    "article_content": f"# {current.values['selected_topic']}",
                    "status": "awaiting_review",
                },
                ("human_review_node",),
            )
        elif current.values["review_decision"] == "approved":
            self._save(
                thread_id,
                {
                    **current.values,
                    "visual_points": ["文章工作流示意图"],
                    "image_urls": ["https://placehold.co/1200x800/png?text=workflow"],
                    "status": "completed",
                },
                (),
            )
        else:
            self._save(
                thread_id,
                {
                    **current.values,
                    "article_content": f"# {current.values['selected_topic']}（已修改）",
                    "status": "awaiting_review",
                },
                ("human_review_node",),
            )
        yield {"workflow": "updated"}


class RateLimitedWorkflow:
    """模拟上游模型限流，用于验证 API 不会将其错误地转成 500。"""

    async def ainvoke(self, *_: Any, **__: Any) -> None:
        raise LLMRateLimitError("AI 服务繁忙，请稍后重试。")


@asynccontextmanager
async def memory_workflow_lifespan(test_app: FastAPI) -> AsyncIterator[None]:
    """为 HTTP 测试注入内存版工作流，避免连接外部服务。"""
    test_app.state.content_graph = FakeWorkflow()
    yield


@asynccontextmanager
async def rate_limited_workflow_lifespan(test_app: FastAPI) -> AsyncIterator[None]:
    """为限流测试注入会失败的工作流。"""
    test_app.state.content_graph = RateLimitedWorkflow()
    yield


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TestClient]:
    """创建使用内存工作流的测试客户端。"""
    # 覆盖生产生命周期，防止测试尝试连接本地 PostgreSQL。
    monkeypatch.setattr(app.router, "lifespan_context", memory_workflow_lifespan)
    with TestClient(app) as test_client:
        yield test_client


def _start_workflow(client: TestClient) -> tuple[str, dict[str, Any]]:
    """创建工作流，并返回线程标识与响应体。"""
    response = client.post("/api/v1/workflow/start", json={"topic_direction": "AI 内容运营"})
    assert response.status_code == 201
    body = response.json()
    return body["thread_id"], body


def test_health_check(client: TestClient) -> None:
    """健康检查应返回正常状态。"""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_surfaces_upstream_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ark 限流时应返回 429，而不是不透明的 500。"""
    monkeypatch.setattr(app.router, "lifespan_context", rate_limited_workflow_lifespan)

    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/workflow/start", json={"topic_direction": "AI 内容运营"})

    assert response.status_code == 429
    assert response.json()["detail"] == "AI 服务繁忙，请稍后重试。"


def test_start_state_and_history(client: TestClient) -> None:
    """启动后应能读取最新状态和首个历史快照。"""
    thread_id, started = _start_workflow(client)

    assert started["status"] == "awaiting_topic_selection"
    assert started["message"] == "选题生成成功，请选择一个选题继续工作流"
    assert started["interrupted"] is True
    assert started["next"] == ["human_selection_node"]
    assert started["generated_topics"] == FakeWorkflow.topics

    state_response = client.get(f"/api/v1/workflow/state/{thread_id}")
    history_response = client.get(f"/api/v1/workflow/history/{thread_id}")

    assert state_response.status_code == 200
    assert state_response.json()["state"] == started["state"]
    assert history_response.status_code == 200
    history = history_response.json()["history"]
    assert len(history) == 1
    assert history[0]["checkpoint_id"] == "checkpoint-1"
    assert history[0]["created_at"] == "2026-01-01T00:00:01+00:00"
    assert history[0]["metadata"] == {"step": 1}


def test_resume_reject_then_approve_and_history(client: TestClient) -> None:
    """选题、驳回重写和通过完成应形成完整可查询的历史。"""
    thread_id, started = _start_workflow(client)
    selected_topic = started["generated_topics"][0]

    select_response = client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "select_topic", "data": {"selected_topic": selected_topic}},
    )
    assert select_response.status_code == 200
    assert select_response.json()["status"] == "awaiting_review"
    assert select_response.json()["message"] == "文章草稿已生成，请审核"

    reject_response = client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "reject", "data": {"review_feedback": "请补充案例"}},
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "awaiting_review"
    assert reject_response.json()["message"] == "文章已根据审核意见重写，请再次审核"
    assert reject_response.json()["state"]["article_content"].endswith("（已修改）")

    approve_response = client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "approve", "data": {}},
    )
    assert approve_response.status_code == 200
    completed = approve_response.json()
    assert completed["status"] == "completed"
    assert completed["message"] == "文章审核已通过，配图已生成"
    assert completed["interrupted"] is False
    assert completed["state"]["image_urls"]

    history_response = client.get(f"/api/v1/workflow/history/{thread_id}?limit=100")
    assert history_response.status_code == 200
    history = history_response.json()["history"]
    assert [item["metadata"]["step"] for item in history] == list(range(1, 8))
    assert history[-1]["status"] == "completed"


@pytest.mark.parametrize(
    ("method", "url", "payload", "expected_status"),
    [
        ("get", "/api/v1/workflow/state/not-found", None, 404),
        ("get", "/api/v1/workflow/history/not-found", None, 404),
        ("post", "/api/v1/workflow/start", {"topic_direction": ""}, 422),
        (
            "post",
            "/api/v1/workflow/resume/not-found",
            {"action": "approve", "data": {}},
            404,
        ),
    ],
)
def test_invalid_or_missing_workflow_requests(
    client: TestClient,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    expected_status: int,
) -> None:
    """缺失线程和无效请求体应返回明确的客户端错误。"""
    # TestClient 的 GET 方法不接收 JSON 请求体，只有 POST 请求才传入测试载荷。
    request = getattr(client, method)
    response = request(url) if payload is None else request(url, json=payload)

    assert response.status_code == expected_status


def test_invalid_resume_state_and_payload(client: TestClient) -> None:
    """不符合当前中断节点的决策与无效输入应被拒绝。"""
    thread_id, _ = _start_workflow(client)

    assert client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "approve", "data": {}},
    ).status_code == 409
    assert client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "select_topic", "data": {"selected_topic": "不存在的选题"}},
    ).status_code == 422

    select_response = client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "select_topic", "data": {"topic": FakeWorkflow.topics[0]}},
    )
    assert select_response.status_code == 200
    assert client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "reject", "data": {}},
    ).status_code == 422
    assert client.post(
        f"/api/v1/workflow/resume/{thread_id}",
        json={"action": "select_topic", "data": {"selected_topic": FakeWorkflow.topics[0]}},
    ).status_code == 409
