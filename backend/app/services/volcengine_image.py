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
from app.graph.metrics import record_model_usage
from app.services.volcengine_llm import LLMRateLimitError, LLMServiceError


_RETRY_DELAYS_SECONDS = (2.0, 4.0)


class ArkImageService:
    """通过 Ark OpenAI 兼容接口，将视觉要点转换为可访问的图片 URL。"""

    def __init__(self, app_settings: Settings = settings) -> None:
        self._settings = app_settings
        self._client: AsyncOpenAI | None = None
        # 服务实例会在多个工作流调用之间复用；因此限流器也必须复用，
        # 才能限制所有同一 Ark 客户端发出的同时请求数。
        self._generation_semaphore = asyncio.Semaphore(
            self._settings.ark_image_max_concurrency
        )

    def _get_client(self) -> AsyncOpenAI:
        """首次图片生成时校验真实服务配置并创建客户端。"""
        missing = [
            name
            for name, value in {
                "ARK_API_KEY": self._settings.ark_api_key,
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
                record_model_usage(response)
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
        """为每个非空视觉要点并行生成图片，并保持输入顺序返回 URL。"""
        prompts: list[str] = []
        for point in visual_points:
            if not isinstance(point, str) or not point.strip():
                raise ValueError("visual_points 必须全部为非空字符串")
            prompts.append(point.strip())

        batch_cancelled = asyncio.Event()

        async def generate_one(prompt: str) -> str:
            # 单次工作流通常仅生成 3--5 张图，但仍限制同时发往上游的请求数，
            # 避免批量任务或重试造成瞬时并发放大。
            async with self._generation_semaphore:
                # 某个同批请求失败时，先标记批次再释放全局限流器。这样已在
                # semaphore 中排队的同批任务即使抢到空位，也不会发起新请求。
                if batch_cancelled.is_set():
                    raise asyncio.CancelledError
                try:
                    return await self._generate_image(prompt)
                except BaseException:
                    batch_cancelled.set()
                    raise

        # gather 的结果位置与传入协程的位置一致，因此即使请求完成顺序不同，
        # 调用方收到的 URL 仍与视觉要点一一对应。
        tasks = [asyncio.create_task(generate_one(prompt)) for prompt in prompts]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            # gather 在某个任务失败时不会自动取消同批其余任务。显式回收它们，
            # 防止等待中的任务稍后仍发起绘图请求，也避免悬挂的重试协程。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
