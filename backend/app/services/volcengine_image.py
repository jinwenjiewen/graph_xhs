"""火山引擎 Ark 图像生成服务。"""

from __future__ import annotations

import asyncio
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.core.config import Settings, settings
from app.services.volcengine_llm import LLMRateLimitError, LLMServiceError


_RETRY_DELAYS_SECONDS = (2.0, 4.0)


class ArkImageService:
    """通过 Ark OpenAI 兼容接口，将视觉要点转换为可访问的图片 URL。"""

    def __init__(self, app_settings: Settings = settings) -> None:
        self._settings = app_settings
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        """延迟创建客户端，使未配置密钥时应用和测试仍可正常导入。"""
        missing = [
            name
            for name, value in {
                "ARK_API_KEY（或 VOLCENGINE_API_KEY）": self._settings.ark_api_key,
                "ARK_IMAGE_MODEL": self._settings.ark_image_model,
                "ARK_BASE_URL": self._settings.ark_base_url,
            }.items()
            if not value.strip()
        ]
        if missing:
            raise LLMServiceError(f"未配置 {', '.join(missing)}。请检查 backend/.env。")

        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._settings.ark_api_key,
                base_url=self._settings.ark_base_url,
                # 重试由本服务处理，避免多个配图请求在 SDK 内部紧凑重试。
                max_retries=0,
            )
        return self._client

    async def _generate_image(self, prompt: str) -> str:
        """生成一张图片，并将上游临时故障转换成稳定的业务错误。"""
        for delay in (*_RETRY_DELAYS_SECONDS, None):
            try:
                response = await self._get_client().images.generate(
                    model=self._settings.ark_image_model,
                    prompt=prompt,
                    size=self._settings.ark_image_size,
                    response_format="url",
                    extra_body={"watermark": self._settings.ark_image_watermark},
                )
                data: Any = getattr(response, "data", None)
                url = getattr(data[0], "url", None) if isinstance(data, list) and data else None
                if not isinstance(url, str) or not url.strip():
                    raise LLMServiceError("图像生成服务未返回有效图片地址。")
                return url
            except RateLimitError as exc:
                if delay is None:
                    raise LLMRateLimitError("AI 图片服务繁忙，请稍后重试。") from exc
            except (APIConnectionError, APITimeoutError, InternalServerError) as exc:
                if delay is None:
                    raise LLMServiceError("AI 图片服务暂时不可用，请稍后重试。") from exc
            except APIStatusError as exc:
                raise LLMServiceError("AI 图片服务请求失败，请检查模型配置后重试。") from exc

            # 仅在仍有下一次机会时等待，避免图像生成请求的瞬时重试放大限流。
            await asyncio.sleep(delay)

        raise AssertionError("图像生成重试循环不应执行到这里")

    async def generate_images(self, visual_points: list[str]) -> list[str]:
        """为每个非空视觉要点依次生成一张图片，返回 Ark 的 URL 列表。"""
        prompts: list[str] = []
        for point in visual_points:
            if not isinstance(point, str) or not point.strip():
                raise ValueError("visual_points 必须全部为非空字符串")
            prompts.append(point.strip())

        return [await self._generate_image(prompt) for prompt in prompts]
