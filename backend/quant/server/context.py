"""应用上下文：把配置、数据库、数据服务、引擎、安全服务装配成一份。

FastAPI 的 ``app.state.ctx`` 持有它，路由通过依赖注入拿到。
测试里可以构造一个用临时目录 + synthetic 数据的上下文，完全不碰真实环境。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..data.base import DataProvider, create_provider
from ..data.store import DataStore, MarketDataService
from ..engine.live import LiveManager
from ..engine.paper import PaperManager
from ..security.crypto import MasterKey, SecretBox, master_key
from ..security.password import hash_password
from ..security.tokens import TokenService
from ..security.totp import TotpService
from ..storage.db import Database
from ..storage.repositories import bootstrap
from .ws import EventHub

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    data: MarketDataService
    hub: EventHub
    token_service: TokenService
    totp_service: TotpService
    secret_box: SecretBox
    master: MasterKey
    paper: PaperManager = field(default_factory=PaperManager)
    live: LiveManager = field(default_factory=LiveManager)
    started_at: float = field(default_factory=time.monotonic)
    warnings: list[str] = field(default_factory=list)
    backtests: Any = None  # BacktestManager，延迟注入以避免循环导入
    coordinator: Any = None  # SessionCoordinator，多实例会话租约/命令
    news_ingestion: Any = None  # NewsIngestionService，自动新闻抓取
    gateway_manager: Any = None  # GatewayManager，C++ 交易网关
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def uptime(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def provider_name(self) -> str:
        return self.data.provider.name


def _provider_kwargs(name: str, settings: Settings) -> dict[str, Any]:
    if name == "csv":
        return {"root": settings.data_path.parent / settings.legacy_cache_dir}
    return {}


def build_data_service(settings: Settings) -> MarketDataService:
    """按配置装配数据源链。

    优先级：主数据源 → 配置的备用源 →（仅开发环境）合成数据。
    合成数据兜底是为了"容器起来就能看到东西"，生产环境请显式关掉。
    """
    names: list[str] = [settings.data_provider, *settings.fallback_providers]
    if not settings.is_production and "synthetic" not in names:
        names.append("synthetic")
    # 虚拟货币数据源：按市场路由，只有 CRYPTO 标的会真正用到（实例化不联网）。
    # 默认加入；无境外出口的服务器可设 QUANT_ENABLE_CRYPTO=false 关闭，避免请求超时。
    if settings.enable_crypto and "crypto" not in names:
        names.append("crypto")
    # 去重保序
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)

    providers: list[DataProvider] = []
    for name in seen:
        try:
            providers.append(create_provider(name, **_provider_kwargs(name, settings)))
        except Exception as exc:  # noqa: BLE001
            log.warning("数据源 %s 初始化失败: %s", name, exc)

    if not providers:
        log.warning("没有任何可用数据源，回退到 synthetic")
        providers = [create_provider("synthetic")]

    store = DataStore(
        root=settings.data_path / "store",
        provider=providers[0],
        fallbacks=providers[1:],
        legacy_dirs=[settings.data_path / "cache"],
        adjust=settings.adjust,
    )
    return MarketDataService(store, providers[0], quote_ttl=settings.quote_ttl)


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    warnings = settings.validate_runtime()

    # 安全组件
    master = master_key()
    token_service = TokenService(
        master,
        access_ttl=settings.access_token_ttl,
        refresh_ttl=settings.refresh_token_ttl,
        issuer=settings.jwt_issuer,
    )
    box = SecretBox.from_master(master, name="field")
    totp_service = TotpService(box, issuer=settings.app_name.replace(" ", ""))

    # 数据库：生产用 alembic 迁移（旧库自动 stamp），开发/测试用 create_all
    db = Database(settings.database_url)
    if settings.is_production:
        from ..storage.migrations import upgrade_database

        try:
            upgrade_database(db.engine)
        except Exception:  # noqa: BLE001 - 迁移失败必须阻止启动，避免半初始化
            log.exception("数据库迁移失败，服务停止启动")
            raise
    else:
        db.create_all()

    # 首次启动创建管理员
    if settings.admin_username:
        generated = settings.admin_password is None
        password = settings.effective_admin_password
        created = bootstrap(db, username=settings.admin_username, password_hash=hash_password(password))
        if created is not None:
            if generated:
                log.warning("=" * 72)
                log.warning("初始管理员账号: %s", settings.admin_username)
                log.warning("初始密码（仅显示一次）: %s", password)
                log.warning("请登录后立即修改密码，并开启两步验证。")
                log.warning("=" * 72)
            settings.__dict__["_bootstrap_password"] = password if generated else None

    data = build_data_service(settings)
    ctx = AppContext(
        settings=settings,
        db=db,
        data=data,
        hub=EventHub(),
        token_service=token_service,
        totp_service=totp_service,
        secret_box=box,
        master=master,
        warnings=warnings,
    )

    # 多实例协调器：租约 + 命令 + 状态快照（lifespan 中启动心跳）
    from ..services.coordinator import SessionCoordinator

    ctx.coordinator = SessionCoordinator(
        db=db,
        paper=ctx.paper,
        live=ctx.live,
        instance_id=ctx.instance_id,
    )
    from ..news.ingest import NewsIngestionService

    ctx.news_ingestion = NewsIngestionService(ctx)
    from ..gateway.manager import GatewayManager

    ctx.gateway_manager = GatewayManager(ctx)

    # 上次进程异常退出残留的 running 记录：活跃租约不标记，避免误伤其他实例
    try:
        active = set(ctx.coordinator.leases.active_run_ids())
        with db.session() as session:
            from sqlalchemy import select

            from ..storage.models import BacktestJob
            from ..storage.repositories import RunRepository

            # 其他实例正在跑的回测任务也不能误标中断
            active.update(session.scalars(select(BacktestJob.run_id).where(BacktestJob.status == "running")))
            count = RunRepository(session).mark_interrupted(exclude_run_ids=active)
            if count:
                log.warning("已将 %d 条残留的运行记录标记为 interrupted", count)
    except Exception:  # noqa: BLE001
        log.exception("清理残留运行记录失败")

    for warning in warnings:
        log.warning("配置检查: %s", warning)

    # 回测管理器（延迟导入避免 context ↔ backtest_manager 循环依赖）
    from .backtest_manager import BacktestManager

    ctx.backtests = BacktestManager(ctx)
    return ctx


__all__ = ["AppContext", "build_context", "build_data_service"]
