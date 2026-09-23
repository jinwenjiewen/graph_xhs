"""组装由中断驱动的 LangGraph 1.x 状态机。"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.metrics import instrument_node
from app.graph.nodes.content_nodes import (
    extract_visual_points,
    generate_draft,
    generate_images,
    human_review_node,
    route_after_review,
    write_draft,
)
from app.graph.nodes.topic_nodes import human_select_node, human_selection_node, plan_topics
from app.graph.state import AgentState
from app.graph.subgraphs.topic_selection import build_topic_selection_subgraph


def build_workflow(checkpointer: BaseCheckpointSaver) -> Any:
    """编译可持久化工作流，并在每个人工决策节点前暂停。"""
    graph = StateGraph(AgentState)
    # 编译后的子图直接作为节点挂载，共享选题字段并继承主图持久化器。
    graph.add_node("topic_selection", build_topic_selection_subgraph())
    # 旧平面图的节点仅供已有检查点续跑，新会话统一进入子图。
    graph.add_node("plan_topics", instrument_node("plan_topics", plan_topics))
    graph.add_node("human_select_node", instrument_node("human_select_node", human_select_node))
    graph.add_node("human_selection_node", instrument_node("human_selection_node", human_selection_node))
    graph.add_node("generate_draft", instrument_node("generate_draft", generate_draft))
    graph.add_node("write_draft", instrument_node("write_draft", write_draft))
    graph.add_node("human_review_node", instrument_node("human_review_node", human_review_node))
    graph.add_node("extract_visual_points", instrument_node("extract_visual_points", extract_visual_points))
    # 保留旧节点名，保证部署前已落库且正等待执行的检查点仍可恢复。
    graph.add_node("extract_visuals", instrument_node("extract_visuals", extract_visual_points))
    graph.add_node("generate_images", instrument_node("generate_images", generate_images))

    # 主路径：选题 -> 撰稿 -> 审核 -> 提炼知识点与配图 Prompt -> 并行生成素材。
    graph.add_edge(START, "topic_selection")
    graph.add_edge("topic_selection", "generate_draft")
    graph.add_edge("plan_topics", "human_select_node")
    graph.add_edge("human_select_node", "generate_draft")
    graph.add_edge("generate_draft", "human_review_node")
    # 以下两条边保留旧节点的续跑能力。只有升级前已经写入检查点的会话会命中它们。
    graph.add_edge("human_selection_node", "generate_draft")
    graph.add_edge("write_draft", "human_review_node")
    graph.add_conditional_edges(
        "human_review_node",
        route_after_review,
        {"extract_visual_points": "extract_visual_points", "generate_draft": "generate_draft"},
    )
    graph.add_edge("extract_visual_points", "generate_images")
    # 与上方兼容节点配套，供旧检查点继续完成剩余自动步骤。
    graph.add_edge("extract_visuals", "generate_images")
    graph.add_edge("generate_images", END)

    return graph.compile(
        checkpointer=checkpointer,
        # 在节点执行前暂停，确保节点只会消费经接口校验并写入的人工决策。
        # 旧节点名也必须暂停，否则历史检查点恢复时可能绕过人工选题。
        interrupt_before=["human_select_node", "human_selection_node", "human_review_node"],
    )
