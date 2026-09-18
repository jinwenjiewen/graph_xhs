"""带类型约束的应用配置。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 ``backend/.env`` 与环境变量加载的应用配置。"""

    app_name: str = "AI 内容运营助手"
    database_url: str = "postgresql+asyncpg://postgres:root@localhost:5432/aicontent"
    checkpoint_database_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def checkpointer_url(self) -> str:
        """返回供 LangGraph 持久化使用的 psycopg 兼容连接地址。"""
        if self.checkpoint_database_url:
            # 检查点可独立使用另一数据库，便于与业务数据隔离。
            return self.checkpoint_database_url
        # SQLAlchemy 的 asyncpg 方言不能直接传给 psycopg 驱动。
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


settings = Settings()
