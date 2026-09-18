"""每个内容生成线程的持久化状态结构。"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class AgentState(TypedDict):
    """单次 AI 内容工作流运行的持久化状态。"""

    topic_direction: str
    generated_topics: NotRequired[list[str]]
    selected_topic: NotRequired[str]
    article_content: NotRequired[str]
    review_feedback: NotRequired[str]
    visual_points: NotRequired[list[str]]
    image_urls: NotRequired[list[str]]
    status: str
    # 工作流内部字段，仅用于审核中断后的路由判断。
    review_decision: NotRequired[str]
