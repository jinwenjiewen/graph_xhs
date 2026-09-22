"""负责选题、撰稿、审核与配图准备的异步节点。"""

from __future__ import annotations

from app.graph.state import AgentState
from app.services.volcengine_image import ArkImageService
from app.services.volcengine_llm import VolcengineTextLLMService

# 文字、配图要点与图片均使用生产模型。配图要点必须从已审核的正文提炼，
# 不能使用固定文案，否则图片会与实际撰写内容脱节。
_text_llm = VolcengineTextLLMService()
# 视觉素材的具体生产方式可替换（如当前的文生图 API、未来的代码截图服务）；
# 节点只依赖其“Prompt 列表 -> 素材 URL 列表”的契约。
_visual_asset_generator = ArkImageService()


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
    if decision == "rejected":
        return {"status": "rewriting_draft"}

    article = state.get("article_content", "").strip()
    if not article:
        raise ValueError("无法审核通过空文章")
    # 审核动作是草稿版本的边界：此处复制后，视觉节点不再依赖可变草稿字段。
    return {"approved_article_content": article, "status": "review_approved"}


def route_after_review(state: AgentState) -> str:
    """审核后选择进入配图分支或回到重写循环。"""
    # 此处只负责路由；状态更新由 human_review_node 完成，便于职责分离。
    return "extract_visual_points" if state["review_decision"] == "approved" else "write_draft"



async def extract_visual_points(state: AgentState) -> dict[str, object]:
    """只从已审核正文提炼视觉素材 Prompt，不调用素材生成 API。"""
    if state.get("review_decision") != "approved":
        raise ValueError("只有审核通过的文章才能提炼配图")
    article = state.get("approved_article_content", "")
    if not article.strip():
        raise ValueError("缺少已审核通过的文章，无法提炼配图")
    prompts = await _text_llm.extract_visual_points(article)
    return {"visual_points": prompts, "status": "generating_images"}


async def extract_visuals(state: AgentState) -> dict[str, object]:
    """兼容旧的 Python 调用方；新工作流节点使用 :func:`extract_visual_points`。"""
    return await extract_visual_points(state)


async def generate_images(state: AgentState) -> dict[str, object]:
    """按已提炼的 Prompt 调用当前视觉素材生成实现。"""
    urls = await _visual_asset_generator.generate_images(state.get("visual_points", []))
    return {"image_urls": urls, "status": "completed"}
