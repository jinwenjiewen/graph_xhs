"""驱动可中断 LangGraph 内容工作流的 HTTP 接口。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.graph.snapshots import (
    get_workflow_history as read_workflow_history,
    get_workflow_snapshot,
    topic_selection_snapshot,
)
from app.services.volcengine_llm import LLMRateLimitError, LLMServiceError

router = APIRouter(prefix="/workflow", tags=["workflow"])

# 新名称与升级前持久化工作流的名称同时视作人工中断点。这样部署升级不会让
# 已暂停会话绕过用户输入，也不会强迫用户放弃正在进行的内容任务。
_HUMAN_SELECT_NODES = frozenset({"human_select_node", "human_selection_node"})
_HUMAN_REVIEW_NODES = frozenset({"human_review_node"})
_HUMAN_INTERRUPT_NODES = _HUMAN_SELECT_NODES | _HUMAN_REVIEW_NODES


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
        "interrupted": bool(set(next_nodes) & _HUMAN_INTERRUPT_NODES), # 是否正在等待人工输入
        "awaiting_human_input": bool(set(next_nodes) & _HUMAN_INTERRUPT_NODES),
        "state": values,               #完整Agent状态数据
    }


def _history_snapshot_payload(thread_id: str, snapshot: Any) -> dict[str, Any]:
    """将历史状态快照转换为适合时间线展示的响应结构。"""
    payload = _snapshot_payload(thread_id, snapshot)
    configurable = snapshot.config.get("configurable", {})
    payload.update(
        {
            "checkpoint_id": configurable.get("checkpoint_id"),
            "checkpoint_ns": configurable.get("checkpoint_ns", ""),
            "created_at": snapshot.created_at,
            "metadata": dict(snapshot.metadata or {}),
        }
    )
    return payload


def _thread_summary_payload(thread_id: str, snapshot: Any) -> dict[str, Any]:
    """将线程的最新快照精简为会话列表所需的信息。"""
    values = dict(snapshot.values)
    next_nodes = list(snapshot.next)
    return {
        "thread_id": thread_id,
        "topic_direction": values.get("topic_direction", ""),
        "selected_topic": values.get("selected_topic"),
        "status": values.get("status", "unknown"),
        "next": next_nodes,
        "interrupted": bool(set(next_nodes) & _HUMAN_INTERRUPT_NODES),
        "awaiting_human_input": bool(set(next_nodes) & _HUMAN_INTERRUPT_NODES),
        "updated_at": snapshot.created_at,
    }


def _resume_message(action: str, workflow_status: str) -> str:
    """根据人工操作及恢复后的状态生成供前端展示的成功提示。"""
    messages = {
        ("select_topic", "awaiting_review"): "文章草稿已生成，请审核",
        ("approve", "completed"): "文章审核已通过，配图已生成",
        ("reject", "awaiting_review"): "文章已根据审核意见重写，请再次审核",
    }
    # 保留兜底信息，避免未来增加工作流状态时返回空提示。
    return messages.get((action, workflow_status), "工作流已恢复执行")


async def _run_until_pause_or_completion(graph: Any, config: dict[str, dict[str, str]]) -> Any:
    """从当前检查点执行，直到下一处人工暂停或工作流结束。"""
    # None 表示不注入新状态，直接从 PostgreSQL 保存的检查点恢复。
    async for _ in graph.astream(None, config, stream_mode="updates"):
        pass
    return await get_workflow_snapshot(graph, config)


async def _get_snapshot_or_404(request: Request, thread_id: str) -> tuple[Any, dict[str, dict[str, str]]]:
    """读取已有持久化线程；不存在时转换为 HTTP 404。"""
    graph = request.app.state.content_graph
    config = _config(thread_id)
    snapshot = await get_workflow_snapshot(graph, config)
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

        snapshot = await get_workflow_snapshot(graph, config)

    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
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


@router.get("/threads")
async def list_workflow_threads(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100, description="最多返回的历史会话数量"),
) -> dict[str, Any]:
    """按最近更新顺序列出可恢复的工作流线程。"""
    graph = request.app.state.content_graph
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=500, detail="工作流检查点存储尚未初始化")

    latest_thread_ids: list[str] = []
    seen_thread_ids: set[str] = set()
    try:
        # ``alist(None)`` 会按 checkpoint_id 从新到旧遍历所有线程的检查点。
        # 每个 thread_id 仅取第一次出现的检查点，即该会话的最新状态。
        # 不能在 ``alist`` 尚持有数据库游标时调用 ``aget_state``：两者共用连接池，
        # 在真实 PostgreSQL 环境会导致状态读取等待游标释放。先收集 thread_id 并显式
        # 关闭迭代器，再读取各会话的最新状态。
        checkpoint_iterator = checkpointer.alist(None)
        try:
            async for checkpoint in checkpoint_iterator:
                configurable = checkpoint.config.get("configurable", {})
                thread_id = configurable.get("thread_id")
                # 按父子图任一最新检查点排序，再按 thread_id 去重。
                # 后续仍读取根图快照，子图不会被展示为独立会话。
                if not isinstance(thread_id, str):
                    continue
                if thread_id in seen_thread_ids:
                    continue
                seen_thread_ids.add(thread_id)
                latest_thread_ids.append(thread_id)
                if len(latest_thread_ids) >= limit:
                    break
        finally:
            close_iterator = getattr(checkpoint_iterator, "aclose", None)
            if close_iterator is not None:
                await close_iterator()

        threads: list[dict[str, Any]] = []
        for thread_id in latest_thread_ids:
            snapshot = await get_workflow_snapshot(graph, _config(thread_id))
            if snapshot.values:
                threads.append(_thread_summary_payload(thread_id, snapshot))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取历史会话列表失败: {exc}") from exc

    return {"threads": threads}


@router.delete("/threads/{thread_id}")
async def delete_workflow_thread(thread_id: str, request: Request) -> dict[str, str]:
    """删除一个历史会话及其全部 LangGraph 检查点。"""
    # 先校验存在性，使删除不存在的会话保持与状态读取一致的 404 语义。
    _, _ = await _get_snapshot_or_404(request, thread_id)
    graph = request.app.state.content_graph
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is None:
        raise HTTPException(status_code=500, detail="工作流检查点存储尚未初始化")

    try:
        # 使用 LangGraph Checkpointer 的官方删除方法，确保 checkpoints、blobs 和
        # pending writes 会在同一线程范围内一并清理。
        await checkpointer.adelete_thread(thread_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除历史会话失败: {exc}") from exc

    return {"thread_id": thread_id, "message": "历史会话已删除"}


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
        snapshots = await read_workflow_history(graph, config, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取工作流历史失败: {exc}") from exc

    # 父子图历史已按时间合并为最早优先，保留选题过程的独立检查点。
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
    update_config = config

    if payload.action == "select_topic":                    # 分析是哪个 查看方向（select_topic）action: Literal["select_topic", "approve", "reject"]
        # 仅允许在选题中断点提交选题，防止用过期请求覆盖后续状态。
        # 选题阶段对应 canonical human_select_node，且兼容旧 checkpoint 的节点名。
        if not _HUMAN_SELECT_NODES & next_nodes:        #要求当前必须停在
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
        child = topic_selection_snapshot(snapshot)
        if child is not None:
            # 子图暂停时尚未向父图输出状态，人工选择必须写入子图命名空间。
            update_config = child.config
    else:
        # approve/reject 都只能在审稿中断点处理。
        if "human_review_node" not in next_nodes:       #要求当前必须停在
            # 要求当前必须停在 human_review_node 这个待执行节点
            raise HTTPException(status_code=409, detail="当前工作流并未等待审稿")
        feedback = payload.data.get(
            "human_feedback",
            payload.data.get("review_feedback", payload.data.get("feedback", "")),
        )
        # 驳回强制要求填写意见
        if not isinstance(feedback, str):
            raise HTTPException(status_code=422, detail="data.human_feedback 必须为字符串")
        if payload.action == "reject" and not feedback.strip():
            raise HTTPException(status_code=422, detail="驳回时必须提供 data.human_feedback")
        update = {
            "review_decision": "approved" if payload.action == "approve" else "rejected",
            "human_feedback": feedback,
            # 保留旧字段，保证升级前的自定义节点实现仍能读取审核意见。
            "review_feedback": feedback,
            "status": "review_submitted",   #审核已提交
        }

    try:
        # 先写入人工输入，再从该检查点继续执行，保证中断前后的状态都可追溯。
        await graph.aupdate_state(update_config, update)
        updated_snapshot = await _run_until_pause_or_completion(graph, config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"恢复工作流失败: {exc}") from exc

    response = _snapshot_payload(thread_id, updated_snapshot)
    response["message"] = _resume_message(payload.action, response["status"])
    return response


@router.post("/continue/{thread_id}")
async def continue_workflow(thread_id: str, request: Request) -> dict[str, Any]:
    """继续执行被中断的自动节点，不允许跳过人工决策。"""
    graph = request.app.state.content_graph
    snapshot, config = await _get_snapshot_or_404(request, thread_id)
    next_nodes = set(snapshot.next)

    if not next_nodes:
        raise HTTPException(status_code=409, detail="当前工作流已完成，无需继续执行")
    if _HUMAN_INTERRUPT_NODES & next_nodes:
        raise HTTPException(status_code=409, detail="当前工作流正在等待人工决策，请使用 resume 接口提交操作")

    try:
        updated_snapshot = await _run_until_pause_or_completion(graph, config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"继续执行工作流失败: {exc}") from exc

    response = _snapshot_payload(thread_id, updated_snapshot)
    response["message"] = "工作流已从持久化检查点继续执行"
    return response
# 1. /start 进入 topic_selection 子图 → plan_topics 生成候选题 → human_select_node 中断
# 2. 前端调用 /resume action=select_topic，提交selected_topic
#     ✅ aupdate_state 写入子图选题 → 从根图 astream 续跑 → generate_draft 生成草稿
#     ✅ 跑完generate_draft → 走到 human_review_node，触发interrupt暂停
# 3. 前端展示草稿，调用/resume action=approve / reject
#     ✅ 如果approve：写入review_decision=approved，继续执行，流程结束
#     ✅ 如果reject：写入review_decision=rejected + human_feedback，分支路由回到generate_draft重写草稿
