"""带类型约束的应用配置。"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 ``backend/.env`` 与环境变量加载的应用配置。"""

    app_name: str = "AI 内容运营助手"
    # 使用 psycopg 标准连接串；仍兼容旧的 postgresql+asyncpg 配置，见
    # ``checkpointer_url`` 属性。
    database_url: str = "postgresql://postgres:root@localhost:5432/aicontent"
    checkpoint_database_url: str | None = None
    database_pool_min_size: int = Field(default=1, validation_alias="DATABASE_POOL_MIN_SIZE")
    database_pool_max_size: int = Field(default=5, validation_alias="DATABASE_POOL_MAX_SIZE")
    # 生文和文生图模型必须使用各自独立的连接配置，均从 backend/.env 读取。
    # 不提供任何密钥或上游地址默认值，防止误用、泄露或跨模型复用凭据。
    volcengine_api_key: str = Field(default="", validation_alias="VOLCENGINE_API_KEY")
    volcengine_model: str = Field(default="", validation_alias="VOLCENGINE_MODEL")
    volcengine_base_url: str = Field(default="", validation_alias="VOLCENGINE_BASE_URL")
    ark_api_key: str = Field(default="", validation_alias="ARK_API_KEY")
    ark_base_url: str = Field(default="", validation_alias="ARK_BASE_URL")
    ark_image_model: str = Field(
        default="doubao-seedream-5-0-260128", validation_alias="ARK_IMAGE_MODEL"
    )
    ark_image_size: str = Field(default="2K", validation_alias="ARK_IMAGE_SIZE")
    ark_image_watermark: bool = Field(
        default=False, validation_alias="ARK_IMAGE_WATERMARK"
    )

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
