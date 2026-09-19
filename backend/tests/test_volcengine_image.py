"""Ark 图像生成服务的本地单元测试，不发起真实 API 请求。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import Request, Response
from openai import RateLimitError

from app.core.config import Settings
from app.services.volcengine_image import ArkImageService
from app.services.volcengine_llm import LLMRateLimitError


class FakeImagesClient:
    """记录 images.generate 请求并按顺序提供预设结果。"""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    """仅实现 ArkImageService 需要的 OpenAI 客户端表面。"""

    def __init__(self, outcomes: list[Any]) -> None:
        self.images = FakeImagesClient(outcomes)


def _service_with(client: FakeClient) -> ArkImageService:
    service = ArkImageService(
        Settings(
            ARK_API_KEY="test-key",
            ARK_BASE_URL="https://example.test/api/v3",
            ARK_IMAGE_MODEL="test-image-model",
            ARK_IMAGE_SIZE="2K",
            ARK_IMAGE_WATERMARK=True,
        )
    )
    service._client = client  # type: ignore[assignment]
    return service


@pytest.mark.asyncio
async def test_generate_images_uses_ark_image_request_fields() -> None:
    """请求参数应与 Ark images.generate 接口契约一致。"""
    client = FakeClient(
        [
            SimpleNamespace(data=[SimpleNamespace(url="https://image.test/one.png")]),
            SimpleNamespace(data=[SimpleNamespace(url="https://image.test/two.png")]),
        ]
    )
    service = _service_with(client)

    urls = await service.generate_images([" 首图：内容运营流程 ", "配图：审核协作"])

    assert urls == ["https://image.test/one.png", "https://image.test/two.png"]
    assert client.images.calls == [
        {
            "model": "test-image-model",
            "prompt": "首图：内容运营流程",
            "size": "2K",
            "response_format": "url",
            "extra_body": {"watermark": True},
        },
        {
            "model": "test-image-model",
            "prompt": "配图：审核协作",
            "size": "2K",
            "response_format": "url",
            "extra_body": {"watermark": True},
        },
    ]


@pytest.mark.asyncio
async def test_rate_limit_is_retried_and_exposed_as_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续限流不泄露上游响应，且会按退避间隔重试。"""
    rate_limit = RateLimitError(
        "busy",
        response=Response(429, request=Request("POST", "https://example.test/api/v3")),
        body={},
    )
    client = FakeClient([rate_limit, rate_limit, rate_limit])
    service = _service_with(client)
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.services.volcengine_image.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMRateLimitError, match="AI 图片服务繁忙"):
        await service.generate_images(["内容运营流程"])

    assert delays == [2.0, 4.0]


@pytest.mark.asyncio
async def test_empty_image_url_is_rejected() -> None:
    """Ark 未返回 URL 时不可将空值写入工作流状态。"""
    service = _service_with(FakeClient([SimpleNamespace(data=[SimpleNamespace(url=None)])]))

    with pytest.raises(RuntimeError, match="有效图片地址"):
        await service.generate_images(["内容运营流程"])
