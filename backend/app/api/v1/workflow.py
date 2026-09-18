"""驱动可中断 LangGraph 内容工作流的 HTTP 接口。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/workflow", tags=["workflow"])


class StartWorkflowRequest(BaseModel):
    """用于创建新的内容工作流线程的请求体。"""

    topic_direction: str = Field(min_length=1, max_length=500, description="内容方向或业务需求")


class ResumeWorkflowRequest(BaseModel):
    """ 。"""

    action: Literal["select_topic", "approve", "reject"]
    data: dict[str, Any] = Field(default_factory=dict)


def _config(thread_id: str) -> dict[str, dict[str, str]]:
    """构造指定线程的 LangGraph 持久化配置。"""
    # 同一个 thread_id 会读取到同一条检查点链，从而让多次 HTTP 请求续跑同一工作流。
    return {"configurable": {"thread_id": thread_id}}


def _snapshot_payload(thread_id: str, snapshot: Any) -> dict[str, Any]:
    """将 LangGraph 状态快照转换为稳定的 API 响应结构。"""
    # 复制状态，避免后续组装响应时意外修改 LangGraph 持有的原始快照。
    values = dict(snapshot.values)
    next_nodes = list(snapshot.next)
    return {
        "thread_id": thread_id,        # 会话线程ID
        "status": values.get("status", "unknown"), # 当前业务状态
        "next": next_nodes,            # 下一步将要执行的节点列表
        "interrupted": bool(next_nodes), # 是否暂停（关键点）
        "state": values,               #完整Agent状态数据
    }


def _history_snapshot_payload(thread_id: str, snapshot: Any) -> dict[str, Any]:
    """将历史状态快照转换为适合时间线展示的响应结构。"""
    payload = _snapshot_payload(thread_id, snapshot)
    configurable = snapshot.config.get("configurable", {})
    payload.update(
        {
            "checkpoint_id": configurable.get("checkpoint_id"),
            "created_at": snapshot.created_at,
            "metadata": dict(snapshot.metadata or {}),
        }
    )
    return payload


def _resume_message(action: str, workflow_status: str) -> str:
    """根据人工操作及恢复后的状态生成供前端展示的成功提示。"""
    messages = {
        ("select_topic", "awaiting_review"): "文章草稿已生成，请审核",
        ("approve", "completed"): "文章审核已通过，配图已生成",
        ("reject", "awaiting_review"): "文章已根据审核意见重写，请再次审核",
    }
    # 保留兜底信息，避免未来增加工作流状态时返回空提示。
    return messages.get((action, workflow_status), "工作流已恢复执行")


async def _get_snapshot_or_404(request: Request, thread_id: str) -> tuple[Any, dict[str, dict[str, str]]]:
    """读取已有持久化线程；不存在时转换为 HTTP 404。"""
    graph = request.app.state.content_graph
    config = _config(thread_id)
    snapshot = await graph.aget_state(config)   #`aget_state`读取当前线程的状态快照
    if not snapshot.values:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到该 thread_id")
    return snapshot, config


@router.post("/start", status_code=status.HTTP_201_CREATED)
async def start_workflow(payload: StartWorkflowRequest, request: Request) -> dict[str, Any]:
    """生成候选选题，并在人工选题节点之前暂停。"""
    graph = request.app.state.content_graph
    # UUID 由服务端生成，客户端无需提前维护 LangGraph 的线程标识。
    thread_id = str(uuid4())
    config = _config(thread_id)
    try:
        await graph.ainvoke(
            {"topic_direction": payload.topic_direction, "status": "planning_topics"},  #topic_direction 选题方向
            config,
        )

        snapshot = await graph.aget_state(config)   #`aget_state`读取当前线程的状态快照

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动工作流失败: {exc}") from exc

    response = _snapshot_payload(thread_id, snapshot)
    # 顶层提示语供前端直接展示，具体候选项仍保留在 generated_topics 中。 已生成的的选题
    response["message"] = "选题生成成功，请选择一个选题继续工作流"
    response["generated_topics"] = response["state"].get("generated_topics", [])
    return response


@router.get("/state/{thread_id}")
async def get_workflow_state(thread_id: str, request: Request) -> dict[str, Any]:
    """获取最新持久化状态，供前端渲染使用。"""
    snapshot, _ = await _get_snapshot_or_404(request, thread_id)
    return _snapshot_payload(thread_id, snapshot)


@router.get("/history/{thread_id}")
async def get_workflow_history(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100, description="最多返回的历史快照数量"),
) -> dict[str, Any]:
    """获取指定工作流线程的历史状态快照，按发生时间正序返回。"""
    graph = request.app.state.content_graph
    _, config = await _get_snapshot_or_404(request, thread_id)

    try:
        snapshots = [
            snapshot
            async for snapshot in graph.aget_state_history(config, limit=limit)
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取工作流历史失败: {exc}") from exc

    # LangGraph 按最近优先返回，前端时间线使用最早优先的顺序。
    snapshots.reverse()
    return {
        "thread_id": thread_id,
        "history": [_history_snapshot_payload(thread_id, snapshot) for snapshot in snapshots],
    }


@router.post("/resume/{thread_id}")     # 用于把人工决定写回已暂停的工作流，并从对应检查点继续执行。
async def resume_workflow(
    thread_id: str, payload: ResumeWorkflowRequest, request: Request
) -> dict[str, Any]:
    """注入人工决策，并运行至下一次中断或工作流完成。"""
    graph = request.app.state.content_graph
    snapshot, config = await _get_snapshot_or_404(request, thread_id)
    next_nodes = set(snapshot.next)         #拿到**待执行节点集合**

    if payload.action == "select_topic":                    # 分析是哪个 查看方向（select_topic）action: Literal["select_topic", "approve", "reject"]
        # 仅允许在选题中断点提交选题，防止用过期请求覆盖后续状态。
        # 选题阶段，对应节点 human_selection_node
        if "human_selection_node" not in next_nodes:        #要求当前必须停在
            raise HTTPException(status_code=409, detail="当前工作流并未等待选题")
        selected_topic = payload.data.get("selected_topic", payload.data.get("topic"))
        candidates = snapshot.values.get("generated_topics", [])
        # 校验选题是否来自系统生成的候选列表
        if not isinstance(selected_topic, str) or selected_topic not in candidates:
            raise HTTPException(
                status_code=422,
                detail="data.selected_topic 必须是 generated_topics 中的一个选题",
            )
        update: dict[str, Any] = {"selected_topic": selected_topic, "status": "topic_selected"}
    else:
        # approve/reject 都只能在审稿中断点处理。
        if "human_review_node" not in next_nodes:       #要求当前必须停在
            # 要求当前必须停在 human_review_node 这个待执行节点
            raise HTTPException(status_code=409, detail="当前工作流并未等待审稿")
        feedback = payload.data.get("review_feedback", payload.data.get("feedback", ""))
        # 驳回强制要求填写意见
        if not isinstance(feedback, str):
            raise HTTPException(status_code=422, detail="data.review_feedback 必须为字符串")
        if payload.action == "reject" and not feedback.strip():
            raise HTTPException(status_code=422, detail="驳回时必须提供 data.review_feedback")     #审核反馈意见（review_feedback）
        update = {
            "review_decision": "approved" if payload.action == "approve" else "rejected",
            "review_feedback": feedback,
            "status": "review_submitted",   #审核已提交
        }

    try:
        # 先写入人工输入，再从该检查点继续执行，保证中断前后的状态都可追溯。
        await graph.aupdate_state(config, update)
        # 传入 None 表示从已持久化的检查点恢复，并在下一个人工中断点停下。
        async for _ in graph.astream(None, config, stream_mode="updates"):
            pass
        updated_snapshot = await graph.aget_state(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"恢复工作流失败: {exc}") from exc

    response = _snapshot_payload(thread_id, updated_snapshot)
    response["message"] = _resume_message(payload.action, response["status"])
    return response
# 1. /start 启动graph → plan_topics生成generated_topics → human_selection_node中断
# 2. 前端调用 /resume action=select_topic，提交selected_topic
#     ✅ aupdate_state写入选题 → astream跑图 → 进入writing_draft节点生成草稿
#     ✅ 跑完writing_draft → 走到 human_review_node，触发interrupt暂停
# 3. 前端展示草稿，调用/resume action=approve / reject
#     ✅ 如果approve：写入review_decision=approved，继续执行，流程结束
#     ✅ 如果reject：写入review_decision=rejected + review_feedback，分支路由回到writing_draft重写草稿
