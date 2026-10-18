"""数据库连接与会话管理。

**选型说明：同步 SQLAlchemy 2.0。**

量化系统的写压力主要来自回测结果落库与纸盘成交流水，量级不高；而回测/纸盘
都跑在后台线程里。用同步 Session 比 async + run_in_threadpool 的组合更不容易
踩坑（尤其 SQLite）。FastAPI 的同步路由会自动在线程池执行，不会阻塞事件循环。

生产环境把 ``QUANT_DATABASE_URL`` 指向 PostgreSQL 即可，业务代码零改动。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

log = logging.getLogger(__name__)

DEFAULT_URL = "sqlite:///data/quant.db"


def normalize_url(url: str | None) -> str:
    """补全相对路径，确保容器/本地都能找到 SQLite 文件。"""
    raw = (url or DEFAULT_URL).strip()
    if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
        relative = raw[len("sqlite:///") :]
        path = Path(relative)
        if not path.is_absolute():
            base = Path(__file__).resolve().parents[2]
            path = (base / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"
    return raw


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    resolved = normalize_url(url)
    is_sqlite = resolved.startswith("sqlite")
    kwargs: dict = {"echo": echo, "future": True, "pool_pre_ping": True}
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(resolved, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 由驱动触发
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")  # 读写并发：后台回测写库不挡 API 读
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    log.info("数据库已连接: %s", _mask_url(resolved))
    return engine


def _mask_url(url: str) -> str:
    """日志里隐藏数据库口令。"""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


class Database:
    """引擎 + 会话工厂的轻量封装。"""

    def __init__(self, url: str | None = None, *, echo: bool = False) -> None:
        self.url = url
        self.engine = make_engine(url, echo=echo)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """事务性会话：正常提交，异常回滚，最后关闭。"""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def healthcheck(self) -> bool:
        from sqlalchemy import text

        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            log.exception("数据库健康检查失败")
            return False

    def dispose(self) -> None:
        self.engine.dispose()


_db: Database | None = None


def get_database(url: str | None = None) -> Database:
    global _db
    if _db is None:
        _db = Database(url)
        _db.create_all()
    return _db


def reset_database() -> None:
    """测试用：释放单例。"""
    global _db
    if _db is not None:
        _db.dispose()
    _db = None


__all__ = ["Database", "DEFAULT_URL", "get_database", "make_engine", "normalize_url", "reset_database"]
