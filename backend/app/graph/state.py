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


class TopicSelectionState(TypedDict):
    """选题子图与主图共享的状态；不依赖正文、审核或配图字段。"""

    topic_direction: str
    generated_topics: NotRequired[list[str]]
    selected_topic: NotRequired[str]
    status: str
    # 子图继承已有指标并追加记录，完成时整体写回主图。
    node_metrics: NotRequired[list[NodeMetric]]


class AgentState(TopicSelectionState):
    """单次 AI 内容工作流运行的持久化状态。"""

    article_content: NotRequired[str]
    # 审核通过时冻结的最终正文。视觉规划只能使用该版本，避免恢复执行时
    # 读取到可继续被修改的草稿字段。
    final_content: NotRequired[str]
    # 旧检查点曾使用的字段。保留读取兼容，新的节点统一写入 ``final_content``。
    approved_article_content: NotRequired[str]
    # 人工审核的修改意见；重写草稿时只读取该字段。
    human_feedback: NotRequired[str]
    # 旧 API 字段的兼容副本，新的客户端请使用 ``human_feedback``。
    review_feedback: NotRequired[str]
    # 3–5 条可叠加到小红书图片上的核心知识点，与 ``image_prompts`` 按下标一一对应。
    visual_points: NotRequired[list[str]]
    # 给绘图模型的技术配图提示词。图片节点优先消费该字段，旧检查点则回退到
    # ``visual_points``，从而仍可继续执行。
    image_prompts: NotRequired[list[str]]
    image_urls: NotRequired[list[str]]
    # 工作流内部字段，仅用于审核中断后的路由判断。
    review_decision: NotRequired[str]
