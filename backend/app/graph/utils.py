"""LangGraph PostgreSQL 检查点持久化器的生命周期辅助函数。"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


async def setup_checkpointer(
    database_pool: AsyncConnectionPool,
) -> AsyncPostgresSaver:
    """连接持久化器，并在检查点数据表不存在时创建它们。

    Checkpointer 从应用级 psycopg 连接池借还连接；连接池的打开和关闭由
    FastAPI lifespan 统一管理。
    """
    checkpointer = AsyncPostgresSaver(database_pool)
    await checkpointer.setup()
    return checkpointer
