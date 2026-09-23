"""选题子图的候选生成与人工确认节点。"""

from __future__ import annotations

from app.graph.state import TopicSelectionState
from app.services.volcengine_llm import VolcengineTextLLMService

_text_llm = VolcengineTextLLMService()


async def plan_topics(state: TopicSelectionState) -> dict[str, object]:
    """根据用户的初始方向生成候选选题。"""
    topics = await _text_llm.plan_topics(state["topic_direction"])
    return {"generated_topics": topics, "status": "awaiting_topic_selection"}


async def human_select_node(state: TopicSelectionState) -> dict[str, object]:
    """校验恢复接口写入子图的选题，确认后交给主图撰稿。"""
    topic = state.get("selected_topic", "")
    if topic not in state.get("generated_topics", []):
        raise ValueError("selected_topic 必须是系统生成的候选选题之一")
    return {"status": "writing_draft"}


async def human_selection_node(state: TopicSelectionState) -> dict[str, object]:
    """兼容旧检查点中的人工选题节点名。"""
    return await human_select_node(state)
