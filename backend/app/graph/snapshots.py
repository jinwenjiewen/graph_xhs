"""将选题子图的持久化状态投影到现有工作流接口。"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import StateSnapshot


def topic_selection_snapshot(snapshot: StateSnapshot) -> StateSnapshot | None:
    """返回当前正在执行的选题子图快照。"""
    for task in snapshot.tasks:
        if (
            task.name == "topic_selection"
            and isinstance(task.state, StateSnapshot)
            and task.state.values
        ):
            return task.state
    return None


async def get_workflow_snapshot(graph: Any, config: RunnableConfig) -> StateSnapshot:
    """读取子图内的候选题和暂停节点，保持前端使用的平面状态协议。"""
    snapshot = await graph.aget_state(config, subgraphs=True)
    child = topic_selection_snapshot(snapshot)
    if child is None or "topic_selection" not in snapshot.next:
        return snapshot
    return snapshot._replace(
        values={**snapshot.values, **child.values},
        # 子图已完成但父图尚未推进时，仍有自动步骤可继续。
        next=child.next or snapshot.next,
        created_at=child.created_at or snapshot.created_at,
    )


async def get_workflow_history(
    graph: Any, config: RunnableConfig, *, limit: int
) -> list[StateSnapshot]:
    """合并父子图的真实历史检查点，避免把最新子图值写入旧快照。"""
    snapshots = [snapshot async for snapshot in graph.aget_state_history(config, limit=limit)]
    history = list(snapshots)
    seen_namespaces: set[str] = set()
    for snapshot in snapshots:
        for task in snapshot.tasks:
            if task.name != "topic_selection" or not isinstance(task.state, dict):
                continue
            namespace = task.state.get("configurable", {}).get("checkpoint_ns")
            if not namespace or namespace in seen_namespaces:
                continue
            seen_namespaces.add(namespace)
            async for child in graph.aget_state_history(task.state, limit=limit):
                history.append(child._replace(
                    values={**snapshot.values, **child.values},
                    next=child.next or ("topic_selection",),
                ))
    history.sort(key=lambda snapshot: snapshot.created_at or "")
    return history[-limit:]
