"""Alembic 环境：数据库地址与运行时配置保持一致。

``create_all`` 仍负责"建缺失的表"；alembic 负责**后续列/索引变更**。
历史库（已有业务表但没有 alembic_version）用 ``scripts/migrate.sh`` 自动 stamp。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from quant.config import get_settings
from quant.storage.db import normalize_url
from quant.storage.models import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers 默认 True 会把应用（quant.*）与 uvicorn 的 logger
    # 全部禁用，导致迁移失败时 traceback 消失；这里必须保留。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", normalize_url(get_settings().database_url))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
