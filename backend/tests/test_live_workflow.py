"""通过 HTTP 验证真实后端、PostgreSQL 检查点与 Ark 内容生成。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest


pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def api_client() -> Iterator[httpx.Client]:
    """连接使用 backend/.env 配置启动的后端进程。"""
    base_url = os.environ.get("LIVE_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    timeout = float(os.environ.get("LIVE_API_TIMEOUT_SECONDS", "600"))
    # 本地后端直连，避免系统代理把回环请求转发到外部网关。
    use_environment_proxy = urlparse(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}
    with httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(timeout, connect=10),
        trust_env=use_environment_proxy,
    ) as client:
        yield client


def _payload(response: httpx.Response, expected_status: int = 200) -> dict[str, Any]:
    assert response.status_code == expected_status, (
        f"{response.request.method} {response.request.url.path}: "
        f"期望 HTTP {expected_status}，实际 HTTP {response.status_code}"
    )
    body = response.json()
    assert isinstance(body, dict)
    return body


def _assert_human_pause(snapshot: dict[str, Any], node: str, status: str) -> None:
    assert snapshot["next"] == [node]
    assert snapshot["status"] == status
    assert snapshot["awaiting_human_input"] is True


def test_live_backend_readiness(api_client: httpx.Client) -> None:
    """读取真实健康状态与持久化会话列表，不触发内容生成。"""
    assert _payload(api_client.get("/health"))["status"] == "ok"
    listing = _payload(api_client.get("/api/v1/workflow/threads", params={"limit": 1}))
    assert isinstance(listing["threads"], list)
    assert len(listing["threads"]) <= 1


@pytest.mark.generation
def test_live_workflow_rewrite_review_and_images(api_client: httpx.Client) -> None:
    """由模型实际生成选题、初稿、重写稿、知识点和配图，并核对落库结果。"""
    direction = os.environ.get(
        "LIVE_TOPIC_DIRECTION", "面向 Python 开发者的 asyncio 并发控制与错误处理技术干货"
    )
    started = _payload(
        api_client.post("/api/v1/workflow/start", json={"topic_direction": direction}),
        expected_status=201,
    )
    thread_id = started["thread_id"]
    # 保留此次实际生成的会话，便于检查结果或定位失败步骤。
    print(f"真实集成测试会话：{thread_id}", flush=True)
    _assert_human_pause(started, "human_select_node", "awaiting_topic_selection")
    topics = started["state"]["generated_topics"]
    assert 3 <= len(topics) <= 5
    assert all(isinstance(topic, str) and topic.strip() for topic in topics)

    resume_path = f"/api/v1/workflow/resume/{thread_id}"
    draft = _payload(api_client.post(resume_path, json={
        "action": "select_topic", "data": {"selected_topic": topics[0]},
    }))
    _assert_human_pause(draft, "human_review_node", "awaiting_review")
    assert draft["state"]["article_content"].strip()

    feedback = "请在文中补充一个具体的操作步骤，并说明该方法的适用条件。"
    revised = _payload(api_client.post(resume_path, json={
        "action": "reject", "data": {"human_feedback": feedback},
    }))
    _assert_human_pause(revised, "human_review_node", "awaiting_review")
    assert revised["state"]["human_feedback"] == feedback
    assert revised["state"]["article_content"].strip()

    completed = _payload(api_client.post(resume_path, json={"action": "approve", "data": {}}))
    assert completed["status"] == "completed"
    assert completed["next"] == []
    assert completed["awaiting_human_input"] is False
    state = completed["state"]
    assert state["final_content"] == revised["state"]["article_content"].strip()
    points, prompts, urls = state["visual_points"], state["image_prompts"], state["image_urls"]
    assert 3 <= len(points) == len(prompts) == len(urls) <= 5
    assert all(isinstance(value, str) and value.strip() for value in [*points, *prompts, *urls])
    assert all(urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc for url in urls)

    metrics = state["node_metrics"]
    node_names = [metric["node_name"] for metric in metrics]
    assert node_names.count("generate_draft") == 2
    assert node_names[-2:] == ["extract_visual_points", "generate_images"]
    assert metrics[-1]["model_call_count"] == len(urls)
    for metric in metrics:
        assert metric["duration_ms"] >= 0
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            # 上游可不返回用量；只验证真实响应里的值，不推算或填充。
            assert metric[field] is None or (isinstance(metric[field], int) and metric[field] >= 0)

    saved = _payload(api_client.get(f"/api/v1/workflow/state/{thread_id}"))
    assert saved["state"] == state
    history = _payload(api_client.get(f"/api/v1/workflow/history/{thread_id}"))["history"]
    assert history
    assert history[-1]["status"] == "completed"
    assert all(item["checkpoint_id"] for item in history)
