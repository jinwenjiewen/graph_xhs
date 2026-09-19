"""火山引擎文字服务的本地单元测试，不发起真实 API 请求。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import Request, Response
from langchain_core.messages import HumanMessage, SystemMessage
from openai import RateLimitError

from app.core.config import Settings
from app.services.volcengine_llm import (
    DRAFT_SYSTEM_PROMPT,
    LLMRateLimitError,
    TOPIC_SYSTEM_PROMPT,
    VolcengineTextLLMService,
)


@dataclass
class FakeResponse:
    """最小化的 LangChain 响应对象。"""

    content: Any


class FakeChatClient:
    """记录请求消息并返回预设内容，确保测试不依赖网络。"""

    def __init__(self, content: Any) -> None:
        self.content = content
        self.messages: list[SystemMessage | HumanMessage] = []

    async def ainvoke(
        self, messages: list[SystemMessage | HumanMessage]
    ) -> FakeResponse:
        self.messages = messages
        return FakeResponse(self.content)


class RetryingFakeChatClient(FakeChatClient):
    """按顺序返回响应或抛出异常，用于验证重试行为。"""

    def __init__(self, outcomes: list[Any]) -> None:
        super().__init__(None)
        self.outcomes = outcomes
        self.calls = 0

    async def ainvoke(
        self, messages: list[SystemMessage | HumanMessage]
    ) -> FakeResponse:
        self.messages = messages
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


def _service_with(client: FakeChatClient) -> VolcengineTextLLMService:
    service = VolcengineTextLLMService(
        Settings(
            VOLCENGINE_API_KEY="test-key",
            VOLCENGINE_MODEL="test-model",
            VOLCENGINE_BASE_URL="https://example.test/v3",
        )
    )
    service._client = client
    return service


def test_parse_topics_accepts_json_code_fence() -> None:
    """即使模型违反格式约定包裹了代码块，仍能兼容解析。"""
    topics = VolcengineTextLLMService._parse_topics(
        "```json\n[\"选题一\", \"选题二\", \"选题三\"]\n```"
    )

    assert topics == ["选题一", "选题二", "选题三"]


@pytest.mark.asyncio
async def test_plan_topics_uses_topic_prompt_and_parses_response() -> None:
    """选题调用应发送既定系统提示词并返回三条标题。"""
    client = FakeChatClient('["选题一", "选题二", "选题三"]')
    service = _service_with(client)

    topics = await service.plan_topics("AI 内容运营")

    assert topics == ["选题一", "选题二", "选题三"]
    assert client.messages[0].content == TOPIC_SYSTEM_PROMPT
    assert "<内容方向>\nAI 内容运营\n</内容方向>" in client.messages[1].content


@pytest.mark.asyncio
async def test_write_draft_keeps_review_feedback_as_content() -> None:
    """审核意见会传递给模型，但不会改变系统提示词。"""
    client = FakeChatClient("# 修改后的文章\n\n正文")
    service = _service_with(client)

    draft = await service.write_draft("AI Agent 实战", "补充一个示例")

    assert draft == "# 修改后的文章\n\n正文"
    assert client.messages[0].content == DRAFT_SYSTEM_PROMPT
    assert "补充一个示例" in client.messages[1].content


@pytest.mark.asyncio
async def test_empty_model_response_is_rejected() -> None:
    """空响应不应作为草稿写入工作流状态。"""
    service = _service_with(FakeChatClient("  "))

    with pytest.raises(ValueError, match="空文本"):
        await service.write_draft("AI Agent 实战")


@pytest.mark.asyncio
async def test_rate_limit_is_retried_with_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游首次限流后，应等待再重试，而非立即再次请求。"""
    rate_limit = RateLimitError(
        "busy",
        response=Response(429, request=Request("POST", "https://example.test/v3")),
        body={},
    )
    client = RetryingFakeChatClient([rate_limit, '["选题一", "选题二", "选题三"]'])
    service = _service_with(client)
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.services.volcengine_llm.asyncio.sleep", fake_sleep)

    assert await service.plan_topics("AI 内容运营") == ["选题一", "选题二", "选题三"]
    assert client.calls == 2
    assert delays == [2.0]


@pytest.mark.asyncio
async def test_exhausted_rate_limit_has_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """持续限流应返回可识别的业务异常，而不是泄露上游响应。"""
    rate_limit = RateLimitError(
        "busy",
        response=Response(429, request=Request("POST", "https://example.test/v3")),
        body={},
    )
    service = _service_with(RetryingFakeChatClient([rate_limit, rate_limit, rate_limit]))

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.volcengine_llm.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMRateLimitError, match="AI 服务繁忙"):
        await service.plan_topics("AI 内容运营")
