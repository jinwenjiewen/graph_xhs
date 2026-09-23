"""封装生成候选题、暂停等待人工确认和校验选题的完整流程。"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.metrics import instrument_node
from app.graph.nodes.topic_nodes import human_select_node, plan_topics
from app.graph.state import TopicSelectionState


def build_topic_selection_subgraph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """主图中继承其持久化器；独立调用时可传入自己的持久化器。"""
    graph = StateGraph(TopicSelectionState)
    graph.add_node("plan_topics", instrument_node("plan_topics", plan_topics))
    graph.add_node("human_select_node", instrument_node("human_select_node", human_select_node))
    graph.add_edge(START, "plan_topics")
    graph.add_edge("plan_topics", "human_select_node")
    graph.add_edge("human_select_node", END)
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_select_node"],
    )
