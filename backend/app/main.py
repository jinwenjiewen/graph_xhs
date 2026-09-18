"""FastAPI 应用入口与资源生命周期管理。"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

if __package__ in {None, ""}:
    # 支持从任意目录直接执行：``python app/main.py``。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
import uvicorn

from app.api.v1.workflow import router as workflow_router
from app.core.config import settings
from app.core.db import close_db, init_db
from app.graph.utils import close_checkpointer, setup_checkpointer
from app.graph.workflow import build_workflow


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """在应用生命周期内管理数据库和 LangGraph 持久化资源。"""
    checkpointer_context = None

    # Startup
    print(f"正在启动 {settings.app_name}...")
    try:
        print("初始化数据库连接...")
        await init_db()

        print("初始化 LangGraph Checkpointer...")
        checkpointer, checkpointer_context = await setup_checkpointer(settings.checkpointer_url)
        app.state.content_graph = build_workflow(checkpointer)
        print("Checkpointer 表结构已创建/验证")
        print(f"{settings.app_name} 启动成功!")
        print("API 文档: http://127.0.0.1:8001/docs")
    except Exception as exc:
        print(f"启动失败: {exc}")
        await close_checkpointer(checkpointer_context)
        await close_db()
        raise RuntimeError(
            "无法连接本地 PostgreSQL。请先创建 aicontent 数据库，并检查 backend/.env 中的账号密码。"
        ) from exc

    try:
        yield
    finally:
        # 关闭阶段：单个资源清理失败不应影响其余资源释放。
        print(f"正在关闭 {settings.app_name}...")
        try:
            await close_checkpointer(checkpointer_context)
        except Exception as exc:
            print(f"关闭 Checkpointer 时出错: {exc}")
        try:
            await close_db()
            print("资源已释放")
        except Exception as exc:
            print(f"关闭数据库连接时出错: {exc}")


app = FastAPI(
    title="AI 内容运营助手",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(workflow_router, prefix="/api/v1")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """提供进程级健康检查接口。"""
    return {"status": "ok"}


if __name__ == "__main__":
    """使用兼容 psycopg 的 asyncio 配置在本地启动 API。"""
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=8001,
        log_level="info",
        loop="none",
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # psycopg 异步模式在 Windows 上需要 Selector 事件循环；``Runner`` 是
        # Python 3.14+ 中替代已废弃全局事件循环策略的推荐方式。
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            try:
                runner.run(server.serve())
            except KeyboardInterrupt:
                pass
    else:
        try:
            asyncio.run(server.serve())
        except KeyboardInterrupt:
            pass
