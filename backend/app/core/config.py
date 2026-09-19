"""带类型约束的应用配置。"""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 ``backend/.env`` 与环境变量加载的应用配置。"""

    app_name: str = "AI 内容运营助手"
    database_url: str = "postgresql+asyncpg://postgres:root@localhost:5432/aicontent"
    checkpoint_database_url: str | None = None
    # LLM 连接信息统一由 backend/.env（或系统环境变量）提供。
    volcengine_api_key: str = Field(default="", validation_alias="VOLCENGINE_API_KEY")
    volcengine_model: str = Field(default="", validation_alias="VOLCENGINE_MODEL")
    volcengine_base_url: str = Field(default="", validation_alias="VOLCENGINE_BASE_URL")
    # Ark 图像生成使用 OpenAI 兼容接口。若未单独配置 ARK_API_KEY，复用文字生成
    # 使用的 VOLCENGINE_API_KEY，方便同一 Ark 账号下的模型共用密钥。
    ark_api_key: str = Field(
        default="", validation_alias=AliasChoices("ARK_API_KEY", "VOLCENGINE_API_KEY")
    )
    ark_base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        validation_alias=AliasChoices("ARK_BASE_URL", "VOLCENGINE_BASE_URL"),
    )
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
