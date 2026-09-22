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


def test_approved_review_routes_to_extract_visual_points() -> None:
    """新工作流主路径必须先生成 Prompt，再进入视觉素材生成节点。"""
    assert content_nodes.route_after_review({"review_decision": "approved"}) == "extract_visual_points"


@pytest.mark.asyncio
async def test_approval_freezes_the_draft_for_visual_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配图只能使用审核通过时冻结的正文，而非之后可能变更的草稿。"""
    text_service = ContentAwareTextService()
    image_service = RecordingImageService()
    monkeypatch.setattr(content_nodes, "_text_llm", text_service)
    monkeypatch.setattr(content_nodes, "_visual_asset_generator", image_service)
    article = "# AI 会议纪要\n\n用 AI 将会议录音整理为明确待办。"

    approval_update = await content_nodes.human_review_node(
        {"review_decision": "approved", "article_content": article}
    )
    visual_update = await content_nodes.extract_visual_points(
        {
            "review_decision": "approved",
            "article_content": "# 未审核的后续草稿",
            **approval_update,
        }
    )
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


@pytest.mark.asyncio
async def test_extract_visual_points_rejects_an_unapproved_or_missing_draft() -> None:
    """绕过审核节点的状态不能进入视觉规划。"""
    with pytest.raises(ValueError, match="只有审核通过"):
        await content_nodes.extract_visual_points(
            {"review_decision": "rejected", "article_content": "# 未通过草稿"}
        )

    with pytest.raises(ValueError, match="缺少已审核通过"):
        await content_nodes.extract_visual_points({"review_decision": "approved"})
