"""确定性的图片生成模拟服务。"""

from __future__ import annotations

from urllib.parse import quote


class MockImageService:
    """使用公开占位图地址替代真实图片 API。"""

    async def generate_images(self, visual_points: list[str]) -> list[str]:
        """为每个视觉要点创建一个带文字的占位图地址。"""
        # 对中文和空格进行 URL 编码，避免提示词破坏查询参数。
        return [
            f"https://placehold.co/1200x800/png?text={quote(point)}"
            for point in visual_points
        ]
