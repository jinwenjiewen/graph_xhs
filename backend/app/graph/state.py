"""每个内容生成线程的持久化状态结构。"""

from __future__ import annotations

from typing import NotRequired, TypedDict


class NodeMetric(TypedDict):
    """单次图节点执行的耗时与模型用量。"""

    node_name: str
    started_at: str
    duration_ms: int
    model_call_count: int
    # 上游未提供精确用量时为 ``None``，绝不以估算值冒充真实 token。
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class AgentState(TypedDict):
    """单次 AI 内容工作流运行的持久化状态。"""

    topic_direction: str
    generated_topics: NotRequired[list[str]]
    selected_topic: NotRequired[str]
    article_content: NotRequired[str]
    # 审核通过时冻结的正文。后续视觉规划只能使用此版本，避免恢复执行时
    # 读取到草稿字段的后续变更。
    approved_article_content: NotRequired[str]
    review_feedback: NotRequired[str]
    # 视觉规划节点输出的自然语言 Prompt；素材生成节点仅消费此字段。
    visual_points: NotRequired[list[str]]
    image_urls: NotRequired[list[str]]
    status: str
    # 工作流内部字段，仅用于审核中断后的路由判断。
    review_decision: NotRequired[str]
    # 每次节点执行均追加一条，供历史会话和当前状态展示性能指标。
    node_metrics: NotRequired[list[NodeMetric]]
