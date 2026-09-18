"""组装由中断驱动的 LangGraph 1.x 状态机。"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.content_nodes import (
    extract_visuals,
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
    graph.add_node("plan_topics", plan_topics)              #生成选题
    graph.add_node("human_selection_node", human_selection_node)    #人选主题
    graph.add_node("write_draft", write_draft)              #写草稿
    graph.add_node("human_review_node", human_review_node)  #人审稿件
    graph.add_node("extract_visuals", extract_visuals)      #提取配图需求
    graph.add_node("generate_images", generate_images)      #Ai出图

    # 主路径：选题 -> 撰稿 -> 审核 -> 提炼视觉要点 -> 生成图片。
    graph.add_edge(START, "plan_topics")
    graph.add_edge("plan_topics", "human_selection_node")
    graph.add_edge("human_selection_node", "write_draft")
    graph.add_edge("write_draft", "human_review_node")
    graph.add_conditional_edges(
        "human_review_node",
        route_after_review,
        {"extract_visuals": "extract_visuals", "write_draft": "write_draft"},
    )
    graph.add_edge("extract_visuals", "generate_images")
    graph.add_edge("generate_images", END)

    return graph.compile(
        checkpointer=checkpointer,
        # 在节点执行前暂停，确保节点只会消费经接口校验并写入的人工决策。
        interrupt_before=["human_selection_node", "human_review_node"],
    )
