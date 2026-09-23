"""真实服务集成测试的运行选项。"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="连接真实后端运行集成测试；包含生成测试时会消耗模型配额。",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    requires_live = pytest.mark.skip(reason="真实服务测试需启动后端并指定 --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(requires_live)
