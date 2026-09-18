"""异步 SQLAlchemy 引擎与会话依赖。"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


# pool_pre_ping 会在复用连接前探测其可用性，避免数据库重启后取到失效连接。
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
# 禁用提交后的属性过期，接口层可在会话结束后安全读取本次查询得到的对象字段。
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """提供一个不自动提交事务的异步 SQLAlchemy 会话。"""
    # 提交或回滚由具体业务决定；依赖本身只负责按请求范围释放会话。
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """在应用启动时建立并验证 SQLAlchemy 数据库连接池。"""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def close_db() -> None:
    """在应用关闭时释放所有 SQLAlchemy 连接。"""
    await engine.dispose()


# 为引用旧名称的代码保留兼容别名。
initialize_database_pool = init_db
close_database_pool = close_db
