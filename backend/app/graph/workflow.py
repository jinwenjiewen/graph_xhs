"""组装由中断驱动的 LangGraph 1.x 状态机。"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.graph.metrics import instrument_node
from app.graph.nodes.content_nodes import (
    extract_visual_points,
    generate_images,
    human_review_node,
    human_selection_node,
    plan_topics,
    route_after_review,
    write_draft,
)
from app.graph.state import AgentState


def build_workflow(checkpointer: AsyncPostgresSaver) -> Any:
    """编译可持久化工作流，并在每个人工决策节点前暂停。"""
    graph = StateGraph(AgentState)
    # 节点名称同时是 API 层判断“当前等待何种人工输入”的稳定协议。
    graph.add_node("plan_topics", instrument_node("plan_topics", plan_topics))
    graph.add_node("human_selection_node", instrument_node("human_selection_node", human_selection_node))
    graph.add_node("write_draft", instrument_node("write_draft", write_draft))
    graph.add_node("human_review_node", instrument_node("human_review_node", human_review_node))
    graph.add_node("extract_visual_points", instrument_node("extract_visual_points", extract_visual_points))
    # 保留旧节点名，保证部署前已落库且正等待执行的检查点仍可恢复。
    graph.add_node("extract_visuals", instrument_node("extract_visuals", extract_visual_points))
    graph.add_node("generate_images", instrument_node("generate_images", generate_images))

    # 主路径：选题 -> 撰稿 -> 审核 -> 生成视觉素材 Prompt -> 生成视觉素材。
    graph.add_edge(START, "plan_topics")
    graph.add_edge("plan_topics", "human_selection_node")
    graph.add_edge("human_selection_node", "write_draft")
    graph.add_edge("write_draft", "human_review_node")
    graph.add_conditional_edges(
        "human_review_node",
        route_after_review,
        {"extract_visual_points": "extract_visual_points", "write_draft": "write_draft"},
    )
    graph.add_edge("extract_visual_points", "generate_images")
    # 与上方兼容节点配套，供旧检查点继续完成剩余自动步骤。
    graph.add_edge("extract_visuals", "generate_images")
    graph.add_edge("generate_images", END)

    return graph.compile(
        checkpointer=checkpointer,
        # 在节点执行前暂停，确保节点只会消费经接口校验并写入的人工决策。
        interrupt_before=["human_selection_node", "human_review_node"],
    )
