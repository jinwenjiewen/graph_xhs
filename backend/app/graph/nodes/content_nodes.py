"""负责撰稿、审核与配图准备的异步节点。"""

from __future__ import annotations

from typing import Any

# 保留旧 Python 导入路径，选题实现由独立模块维护。
from app.graph.nodes.topic_nodes import human_select_node, human_selection_node, plan_topics
from app.graph.state import AgentState
from app.services.volcengine_image import ArkImageService
from app.services.volcengine_llm import VolcengineTextLLMService

# 文字、配图要点与图片均使用生产模型。配图要点必须从已审核的正文提炼，
# 不能使用固定文案，否则图片会与实际撰写内容脱节。
_text_llm = VolcengineTextLLMService()
# 视觉素材的具体生产方式可替换（如当前的文生图 API、未来的代码截图服务）；
# 节点只依赖其“Prompt 列表 -> 素材 URL 列表”的契约。
_visual_asset_generator = ArkImageService()


async def generate_draft(state: AgentState) -> dict[str, object]:
    """根据编辑反馈撰写或重写文章。"""
    feedback = state.get("human_feedback")
    if not isinstance(feedback, str):
        # 兼容升级前已持久化的审核意见，防止历史会话重写时丢失上下文。
        legacy_feedback = state.get("review_feedback", "")
        feedback = legacy_feedback if isinstance(legacy_feedback, str) else ""
    draft = await _text_llm.write_draft(
        state.get("selected_topic", ""), feedback
    )
    # 驳回后的反馈会保留在状态中，因此本节点既承担首稿生成也承担重写。
    return {"article_content": draft, "status": "awaiting_review"}


async def write_draft(state: AgentState) -> dict[str, object]:
    """兼容旧检查点的节点别名；新图使用 :func:`generate_draft`。"""
    return await generate_draft(state)


async def human_review_node(state: AgentState) -> dict[str, object]:
    """校验恢复工作流接口提交的通过或驳回决定。"""
    decision = state.get("review_decision")
    if decision not in {"approved", "rejected"}:
        raise ValueError("review_decision 必须为 approved 或 rejected")
    if decision == "rejected":
        return {"status": "rewriting_draft"}

    raw_article = state.get("article_content", "")
    if not isinstance(raw_article, str) or not (article := raw_article.strip()):
        raise ValueError("无法审核通过空文章")
    # 审核动作是草稿版本的边界：此处复制后，视觉节点不再依赖可变草稿字段。
    return {
        "final_content": article,
        # 保留该字段，确保部署前已暂停的旧工作流仍可被视觉节点消费。
        "approved_article_content": article,
        "status": "review_approved",
    }


def route_after_review(state: AgentState) -> str:
    """审核后选择进入配图分支或回到重写循环。"""
    # 此处只负责路由；状态更新由 human_review_node 完成，便于职责分离。
    return "extract_visual_points" if state["review_decision"] == "approved" else "generate_draft"


async def extract_visual_points(state: AgentState) -> dict[str, object]:
    """从最终正文提炼可叠字知识点与对应技术配图 Prompt。"""
    if state.get("review_decision") != "approved":
        raise ValueError("只有审核通过的文章才能提炼配图")
    raw_article = state.get("final_content") or state.get("approved_article_content", "")
    if not isinstance(raw_article, str) or not (article := raw_article.strip()):
        raise ValueError("缺少已审核通过的文章，无法提炼配图")

    visual_points, image_prompts = await _extract_visual_plan(article)
    return {
        "visual_points": visual_points,
        "image_prompts": image_prompts,
        "status": "generating_images",
    }


async def _extract_visual_plan(article: str) -> tuple[list[str], list[str]]:
    """调用真实文本模型，使用服务层校验后的知识点与绘图 Prompt。"""
    plan = await _text_llm.extract_visual_plan(article)
    return (
        [item["knowledge_point"] for item in plan],
        [item["image_prompt"] for item in plan],
    )


async def extract_visuals(state: AgentState) -> dict[str, object]:
    """兼容旧的 Python 调用方；新工作流节点使用 :func:`extract_visual_points`。"""
    return await extract_visual_points(state)


async def generate_images(state: AgentState) -> dict[str, object]:
    """按视觉规划中的配图 Prompt 并行生成技术视觉素材。"""
    prompts: Any = (
        state["image_prompts"]
        if "image_prompts" in state
        else state.get("visual_points", [])
    )
    if not isinstance(prompts, list) or not 3 <= len(prompts) <= 5:
        raise ValueError("图片生成提示词必须为 3 至 5 条")
    urls = await _visual_asset_generator.generate_images(prompts)
    if (
        not isinstance(urls, list)
        or len(urls) != len(prompts)
        or not all(isinstance(url, str) and url.strip() for url in urls)
    ):
        raise ValueError("图片生成结果必须与图片提示词一一对应")
    return {"image_urls": urls, "status": "completed"}
