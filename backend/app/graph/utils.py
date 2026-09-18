"""LangGraph PostgreSQL 检查点持久化器的生命周期辅助函数。"""

from __future__ import annotations

from typing import AsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


async def setup_checkpointer(
    database_url: str,
) -> tuple[AsyncPostgresSaver, AsyncContextManager[AsyncPostgresSaver]]:
    """连接持久化器，并在检查点数据表不存在时创建它们。

    返回的上下文管理器必须在整个应用生命周期内保持开启。
    """
    # from_conn_string 返回的是异步上下文管理器，而不是已打开的保存器实例。
    context = AsyncPostgresSaver.from_conn_string(database_url)
    checkpointer = await context.__aenter__()
    try:
        await checkpointer.setup()
    except Exception:
        # setup 失败时立即退出上下文，避免启动失败遗留数据库连接。
        await context.__aexit__(None, None, None)
        raise
    return checkpointer, context


async def close_checkpointer(
    context: AsyncContextManager[AsyncPostgresSaver] | None,
) -> None:
    """关闭由 :func:`setup_checkpointer` 创建的连接上下文。"""
    if context is not None:
        # 允许启动过程在连接建立前失败，此时清理函数仍应可安全调用。
        await context.__aexit__(None, None, None)
