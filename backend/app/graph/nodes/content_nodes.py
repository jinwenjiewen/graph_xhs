"""负责选题、撰稿、审核与配图准备的异步节点。"""

from __future__ import annotations

from app.graph.state import AgentState
from app.services.volcengine_image import ArkImageService
from app.services.volcengine_llm import VolcengineTextLLMService

# 文字、配图要点与图片均使用生产模型。配图要点必须从已审核的正文提炼，
# 不能使用固定文案，否则图片会与实际撰写内容脱节。
_text_llm = VolcengineTextLLMService()
_images = ArkImageService()


async def plan_topics(state: AgentState) -> dict[str, object]:
    """根据用户的初始方向生成候选选题。"""
    topics = await _text_llm.plan_topics(state["topic_direction"])
    # 不直接进入写作：状态机会在下一个节点前中断，等待编辑确认选题。
    return {"generated_topics": topics, "status": "awaiting_topic_selection"}


async def human_selection_node(state: AgentState) -> dict[str, object]:
    """校验恢复工作流接口提交的选题。"""
    topic = state.get("selected_topic", "")
    if topic not in state.get("generated_topics", []):
        raise ValueError("selected_topic 必须是系统生成的候选选题之一")
    return {"status": "writing_draft"}


async def write_draft(state: AgentState) -> dict[str, object]:
    """根据编辑反馈撰写或重写文章。"""
    draft = await _text_llm.write_draft(
        state.get("selected_topic", ""), state.get("review_feedback", "")
    )
    # 驳回后的反馈会保留在状态中，因此本节点既承担首稿生成也承担重写。
    return {"article_content": draft, "status": "awaiting_review"}


async def human_review_node(state: AgentState) -> dict[str, object]:
    """校验恢复工作流接口提交的通过或驳回决定。"""
    decision = state.get("review_decision")
    if decision not in {"approved", "rejected"}:
        raise ValueError("review_decision 必须为 approved 或 rejected")
    return {"status": "review_approved" if decision == "approved" else "rewriting_draft"}
    #approved提前视觉素材 rejected重写草稿


def route_after_review(state: AgentState) -> str:
    """审核后选择进入配图分支或回到重写循环。"""
    # 此处只负责路由；状态更新由 human_review_node 完成，便于职责分离。
    return "extract_visuals" if state["review_decision"] == "approved" else "write_draft"



async def extract_visuals(state: AgentState) -> dict[str, object]:
    """从已通过审核的文章中提炼配图提示要点。"""
    points = await _text_llm.extract_visual_points(state.get("article_content", ""))
    return {"visual_points": points, "status": "generating_images"}


async def generate_images(state: AgentState) -> dict[str, object]:
    """为已通过审核的文章调用 Ark 图像模型生成配图。"""
    urls = await _images.generate_images(state.get("visual_points", []))
    return {"image_urls": urls, "status": "completed"}
