"""内容节点的配图链路测试。"""

from __future__ import annotations

import pytest

from app.graph.nodes import content_nodes


class ContentAwareTextService:
    """记录传给视觉规划器的正文。"""

    def __init__(self) -> None:
        self.article_content = ""

    async def extract_visual_points(self, article_content: str) -> list[str]:
        self.article_content = article_content
        return [
            "基于正文的首图",
            "基于正文的场景图",
            "基于正文的核心步骤图",
            "基于正文的应用图",
            "基于正文的总结图",
        ]


class RecordingImageService:
    """记录传给图像模型的提示词。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_images(self, prompts: list[str]) -> list[str]:
        self.prompts = prompts
        return [f"https://image.test/{index}.png" for index in range(1, 6)]


@pytest.mark.asyncio
async def test_visual_chain_uses_approved_article_to_generate_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已审核正文应参与视觉规划，规划结果应原样成为生图提示词。"""
    text_service = ContentAwareTextService()
    image_service = RecordingImageService()
    monkeypatch.setattr(content_nodes, "_text_llm", text_service)
    monkeypatch.setattr(content_nodes, "_images", image_service)
    article = "# AI 会议纪要\n\n用 AI 将会议录音整理为明确待办。"

    visual_update = await content_nodes.extract_visuals({"article_content": article})
    image_update = await content_nodes.generate_images(visual_update)

    assert text_service.article_content == article
    assert image_service.prompts == visual_update["visual_points"]
    assert image_update["image_urls"] == [
        "https://image.test/1.png",
        "https://image.test/2.png",
        "https://image.test/3.png",
        "https://image.test/4.png",
        "https://image.test/5.png",
    ]
