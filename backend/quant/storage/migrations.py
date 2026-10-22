"""数据库迁移入口。

策略：

* **生产**：启动时走 alembic（空库直接建表；已有业务表但没有版本表的旧库自动
  ``stamp head``，之后只应用增量迁移）；
* **开发/测试**：仍用 ``create_all``，测试无需迁移开销。

``create_all`` 不再在生产使用，避免"新迁移建表与 create_all 重复"的冲突。
模型改动后需要生成迁移：``alembic revision --autogenerate -m "描述"``。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import Engine, inspect

log = logging.getLogger(__name__)


def _alembic_ini() -> Path:
    """定位 alembic.ini。

    容器里 ``quant`` 安装在 site-packages，不能靠 ``__file__`` 推 backend 根；
    优先环境变量，其次容器工作目录（Dockerfile 的 WORKDIR=/app/backend），
    最后是源码树。
    """
    candidates: list[Path] = []
    env_path = os.environ.get("QUANT_ALEMBIC_INI")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.cwd() / "alembic.ini")
    candidates.append(Path(__file__).resolve().parents[2] / "alembic.ini")
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("找不到 alembic.ini（可用 QUANT_ALEMBIC_INI 指定）")


def _alembic_config():
    from alembic.config import Config

    ini = _alembic_ini()
    config = Config(str(ini))
    config.set_main_option("script_location", str(ini.parent / "alembic"))
    return config


def upgrade_database(engine: Engine) -> str:
    """把数据库升级到最新版本，返回动作：``created`` / ``stamped`` / ``upgraded``。"""
    from alembic import command

    config = _alembic_config()
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" in tables:
        command.upgrade(config, "head")
        log.info("数据库迁移：已升级到 head")
        return "upgraded"
    if tables:
        # 旧库由 create_all 建出、没有 alembic_version：先判断它已经具备哪些
        # 迁移的表，再从对应版本继续升级。直接 stamp head 会跳过后续新增表
        # （例如 news_items / watchlist_items），导致"更新后功能缺表"。
        target = "2e6eb884e37d"  # initial schema
        if "watchlist_items" in tables:
            target = "head"
        elif "news_items" in tables:
            target = "b7c2d9e1f4a6"
        elif "session_leases" in tables or "backtest_jobs" in tables:
            target = "9b1f4c2d7e30"
        command.stamp(config, target)
        if target != "head":
            command.upgrade(config, "head")
        log.info("数据库迁移：旧库已标记为 %s 并升级到 head", target)
        return "stamped"
    command.upgrade(config, "head")
    log.info("数据库迁移：空库已建表并标记 head")
    return "created"


__all__ = ["upgrade_database"]
