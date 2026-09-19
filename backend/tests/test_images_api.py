"""独立图像生成接口测试，不发起 Ark 或数据库请求。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import images as images_api
from app.main import app
from app.services.volcengine_llm import LLMRateLimitError, LLMServiceError


class FakeImageService:
    """记录提示词并提供可预测的图像 URL。"""

    def __init__(self) -> None:
        self.prompts: list[list[str]] = []

    async def generate_images(self, prompts: list[str]) -> list[str]:
        self.prompts.append(prompts)
        return ["https://image.example.test/generated.png"]


@asynccontextmanager
async def no_database_lifespan(_: FastAPI) -> AsyncIterator[None]:
    """此接口测试无需启动应用的数据库和工作流资源。"""
    yield


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TestClient]:
    """注入假图片服务，确保接口层测试不产生外部调用。"""
    service = FakeImageService()
    monkeypatch.setattr(app.router, "lifespan_context", no_database_lifespan)
    monkeypatch.setattr(images_api, "_image_service", service)
    with TestClient(app) as test_client:
        yield test_client


def test_generate_image_returns_ark_url(client: TestClient) -> None:
    """提示词应由接口原样传给图片服务，并返回第一张图片 URL。"""
    response = client.post(
        "/api/v1/images/generate",
        json={"prompt": "未来感数据中心，蓝紫色霓虹灯"},
    )

    assert response.status_code == 200
    assert response.json() == {"url": "https://image.example.test/generated.png"}


@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": "x" * 4_001}])
def test_generate_image_validates_prompt(client: TestClient, payload: dict[str, str]) -> None:
    """缺失、空白或过长的提示词应在调用模型前被拒绝。"""
    response = client.post("/api/v1/images/generate", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (LLMRateLimitError("AI 图片服务繁忙，请稍后重试。"), 429),
        (LLMServiceError("AI 图片服务暂时不可用，请稍后重试。"), 503),
    ],
)
def test_generate_image_maps_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    """上游失败应转换为可供调用方处理的 HTTP 状态码。"""
    class FailingImageService:
        async def generate_images(self, _: list[str]) -> list[str]:
            raise error

    monkeypatch.setattr(app.router, "lifespan_context", no_database_lifespan)
    monkeypatch.setattr(images_api, "_image_service", FailingImageService())

    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/images/generate", json={"prompt": "测试"})

    assert response.status_code == expected_status
    assert response.json() == {"detail": str(error)}
