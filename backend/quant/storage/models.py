"""ORM 模型。

数据分级：

* **敏感**：``users.password_hash`` / ``users.totp_secret`` / ``credentials.payload``
  —— 口令用 Argon2id 不可逆哈希；TOTP 种子与券商凭据用 AES-256-GCM 加密。
* **审计**：``audit_logs`` 为哈希链，只追加不改写。
* **业务**：``runs`` 等，含大块 JSON（权益曲线/成交），用 zlib 压缩后存 BLOB，
  单条回测通常从 ~2MB 压到 ~200KB。
"""

from __future__ import annotations

import json
import zlib
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow_naive() -> datetime:
    """数据库统一存 naive UTC，避免 SQLite/Postgres 时区行为差异。"""
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class CompressedJSON(TypeDecorator):
    """zlib 压缩的 JSON 列，用于大块只读数据（权益曲线、成交明细）。"""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> bytes | None:
        if value is None:
            return None
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return zlib.compress(raw, level=6)

    def process_result_value(self, value: bytes | None, dialect: Any) -> Any:
        if value is None:
            return None
        try:
            return json.loads(zlib.decompress(value).decode("utf-8"))
        except (zlib.error, ValueError):
            return None


# --------------------------------------------------------------------------- #
# 用户与认证
# --------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # approved / pending / rejected；注册用户默认 pending，管理员创建默认 approved
    approval_status: Mapped[str] = mapped_column(
        String(16), default="approved", server_default="approved", nullable=False
    )

    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)  # AES-GCM 密文
    totp_recovery: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 恢复码 SHA-256 列表
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "approval_status": self.approval_status,
            "totp_enabled": self.totp_enabled,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "detail": self.detail or {},
            "ip": self.ip,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


# --------------------------------------------------------------------------- #
# 加密凭据
# --------------------------------------------------------------------------- #


class Credential(Base):
    """券商/数据源凭据。``payload`` 是 AES-256-GCM 密文，明文永不落库。"""

    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # broker | datasource
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # 密文
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    def to_dict(self) -> dict[str, Any]:
        """注意：**不返回 payload**。"""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# --------------------------------------------------------------------------- #
# 运行记录（回测 / 纸盘）
# --------------------------------------------------------------------------- #


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="backtest", nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    equity_curve: Mapped[list | None] = mapped_column(CompressedJSON, nullable=True)
    trades: Mapped[list | None] = mapped_column(CompressedJSON, nullable=True)
    orders_data: Mapped[list | None] = mapped_column("orders_json", CompressedJSON, nullable=True)
    positions: Mapped[list | None] = mapped_column(CompressedJSON, nullable=True)
    logs: Mapped[list | None] = mapped_column(CompressedJSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_runs_user_created", "user_id", "created_at"),)

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.id,
            "kind": self.kind,
            "strategy_id": self.strategy_id,
            "symbols": self.symbols or [],
            "status": self.status,
            "progress": round(self.progress, 3),
            "metrics": self.metrics or {},
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }

    def to_detail(self) -> dict[str, Any]:
        data = self.to_summary()
        data.update(
            {
                "config": self.config or {},
                "equity_curve": self.equity_curve or [],
                "trades": self.trades or [],
                "orders": self.orders_data or [],
                "positions": self.positions or [],
                "logs": self.logs or [],
            }
        )
        return data


# --------------------------------------------------------------------------- #
# 场外基金申赎记录
# --------------------------------------------------------------------------- #


class FundTrade(Base):
    """场外基金申购/赎回流水。

    持仓不单独存表，而是由流水按时间顺序聚合（见 :mod:`quant.funds`），
    避免"持仓表"与"流水表"双写不一致。
    """

    __tablename__ = "fund_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # FUND:007005
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)  # subscribe | redeem
    trade_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    confirm_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    nav: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    fee_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    __table_args__ = (Index("ix_fund_trades_user_symbol", "user_id", "symbol"),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side,
            "trade_date": self.trade_date.date().isoformat() if self.trade_date else None,
            "confirm_date": self.confirm_date.date().isoformat() if self.confirm_date else None,
            "nav": round(self.nav, 4),
            "shares": round(self.shares, 4),
            "amount": round(self.amount, 2),
            "fee_rate": self.fee_rate,
            "fee": round(self.fee, 2),
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# --------------------------------------------------------------------------- #
# 用户自建策略（条件拼装）
# --------------------------------------------------------------------------- #


class UserCustomStrategy(Base):
    """网页拼装的策略 spec。``spec`` 是经过白名单校验的 JSON，不包含可执行代码。"""

    __tablename__ = "user_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="trend", nullable=False)
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 从共享策略复制而来的快照；源策略删除后置空，副本仍可独立使用
    source_strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_strategies.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    __table_args__ = (Index("uq_user_strategies_user_name", "user_id", "name", unique=True),)

    def to_dict(self, *, with_spec: bool = True) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "spec": self.spec if with_spec else None,
            "enabled": self.enabled,
            "version": self.version,
            "source_strategy_id": self.source_strategy_id,
            "source_version": self.source_version,
            "forked_at": self.forked_at.isoformat() if self.forked_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class StrategyVersion(Base):
    """拼装策略的不可变版本快照（创建/修改时写入）。"""

    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("user_strategies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="trend", nullable=False)
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)


class StrategyShare(Base):
    """共享关系：target_user_id 为空表示完全共享，否则是点对点共享。"""

    __tablename__ = "strategy_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("user_strategies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


__all__ = [
    "AuditLog",
    "Base",
    "CompressedJSON",
    "Credential",
    "FundTrade",
    "RefreshToken",
    "Run",
    "User",
    "StrategyShare",
    "StrategyVersion",
    "UserCustomStrategy",
    "utcnow_naive",
]


# --------------------------------------------------------------------------- #
# 分布式会话协调（多 worker / 多副本）
# --------------------------------------------------------------------------- #


class SessionLease(Base):
    """会话租约：谁拥有某个纸盘/实盘会话，以及最近一次状态快照。

    多实例部署时，引擎只能由一个实例持有；租约通过数据库原子更新抢占，
    过期后其他实例可以接管。状态快照让任意实例都能回答 ``/sessions``。
    """

    __tablename__ = "session_leases"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    owner: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)


class SessionCommand(Base):
    """跨实例控制命令：stop / pause / resume / switch。

    任意实例都可以写入；持有租约的实例轮询并执行。
    """

    __tablename__ = "session_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(96), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BacktestJob(Base):
    """持久化回测队列：提交先落库，重启或多实例下由 worker 原子认领。"""

    __tablename__ = "backtest_jobs"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)


# --------------------------------------------------------------------------- #
# 新闻与政策因子
# --------------------------------------------------------------------------- #


class NewsItem(Base):
    """一条新闻/政策原始条目（Point-in-Time：策略只能看到 effective_at 之前的数据）。"""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(600), default="", nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    symbols: Mapped[list | None] = mapped_column(JSON, nullable=True)
    market: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    policy_risk: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
            "effective_at": self.effective_at.isoformat() if self.effective_at else None,
            "symbols": self.symbols or [],
            "market": self.market,
            "event_type": self.event_type,
            "sentiment": self.sentiment,
            "policy_risk": self.policy_risk,
            "confidence": self.confidence,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WatchlistItem(Base):
    """账号级自选：同一用户同一标的一条，sort_order 保持手动顺序。"""

    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
        Index("ix_watchlist_user_order", "user_id", "sort_order"),
    )


class Feedback(Base):
    """用户意见/建议；列表仅管理员可见。"""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    username: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(24), default="suggestion", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "category": self.category,
            "content": self.content,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class IndexMembership(Base):
    """指数成分股的**历史区间**（point-in-time）。

    存成区间而不是"某次快照的名单"，是为了让回测问出"2021-06-01 那天这个
    指数里有哪些股票"。用今天的名单回测三年前，等于只挑了活到今天的幸存者，
    收益会被系统性高估（幸存者偏差）。

    ``end_date`` 为空表示该标的目前仍在指数内；导入新快照时，掉出名单的
    标的会被补上 ``end_date``，所以多次导入快照能自动拼出区间。
    """

    __tablename__ = "index_membership"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("index_code", "symbol", "start_date", name="uq_index_member_entry"),
        Index("ix_index_membership_lookup", "index_code", "start_date", "end_date"),
    )

    def to_dict(self) -> dict:
        return {
            "index_code": self.index_code,
            "symbol": self.symbol,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "source": self.source,
        }
