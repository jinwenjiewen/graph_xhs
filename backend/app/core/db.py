"""Psycopg 异步 PostgreSQL 连接池与生命周期管理。"""

from collections.abc import AsyncGenerator

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings


# 连接池在模块导入时保持关闭；由 FastAPI lifespan 在运行事件循环中显式打开。
# Checkpointer 需要自动提交、字典行和禁用预处理语句，因而这些设置在整个池中统一。
database_pool = AsyncConnectionPool(
    conninfo=settings.checkpointer_url,
    min_size=settings.database_pool_min_size,
    max_size=settings.database_pool_max_size,
    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    open=False,
    name="content-postgres",
)


async def get_db_connection() -> AsyncGenerator[AsyncConnection, None]:
    """为需要直接执行 PostgreSQL 语句的调用方借出一条连接。"""
    async with database_pool.connection() as connection:
        yield connection


def get_db_pool() -> AsyncConnectionPool:
    """返回应用唯一的连接池，供 LangGraph Checkpointer 复用。"""
    return database_pool


async def init_db() -> None:
    """打开连接池，建立最小连接数，并验证 PostgreSQL 可用。"""
    await database_pool.open()
    try:
        await database_pool.wait()
        async with database_pool.connection() as connection:
            await connection.execute("SELECT 1")
    except Exception:
        await database_pool.close()
        raise


async def close_db() -> None:
    """在应用关闭时归还并关闭所有 PostgreSQL 连接。"""
    await database_pool.close()


# 为引用旧名称的代码保留兼容别名。
initialize_database_pool = init_db
close_database_pool = close_db
