"""用于直接测试 Ark 图像生成能力的 HTTP 接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.volcengine_image import ArkImageService
from app.services.volcengine_llm import LLMRateLimitError, LLMServiceError


router = APIRouter(prefix="/images", tags=["images"])
_image_service = ArkImageService()


class GenerateImageRequest(BaseModel):
    """单张图片生成请求。"""

    prompt: str = Field(
        min_length=1,
        max_length=4_000,
        description="用于生成图片的提示词",
        # examples=["未来感数据中心，蓝紫色霓虹灯，电影级光影，细节丰富"],
        # examples=["奶油色背景，极简光影，细节丰富，ins风"],

    )


class GenerateImageResponse(BaseModel):
    """Ark 生成的单张图片地址。"""

    url: str


@router.post("/generate", response_model=GenerateImageResponse)
async def generate_image(payload: GenerateImageRequest) -> GenerateImageResponse:
    """生成一张图片；模型、尺寸和水印从 ``backend/.env`` 的 Ark 配置读取。"""
    try:
        urls = await _image_service.generate_images([payload.prompt])
        if not urls:
            raise LLMServiceError("图像生成服务未返回有效图片地址。")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return GenerateImageResponse(url=urls[0])
