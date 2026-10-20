"""应用配置（pydantic-settings）。

所有配置都可以用环境变量覆盖，前缀 ``QUANT_``，例如::

    QUANT_DATABASE_URL=postgresql+psycopg://quant:***@db:5432/quant
    QUANT_MASTER_KEY=<base64 32 字节>
    QUANT_ALLOWED_ORIGINS=["https://quant.example.com"]

敏感项（主密钥、数据库口令）只从环境变量/密钥文件读取，
``.env`` 仅用于本地开发且已被 ``.gitignore`` 排除。
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUANT_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # 关闭 pydantic-settings 对 list/dict 字段的 JSON 预解析：
        # 我们自己的 _split_csv 校验器同时支持 "A,B,C" 与 '["A","B"]' 两种写法，
        # 否则 CSV 形式的环境变量会在进入校验器之前就抛 JSONDecodeError。
        enable_decoding=False,
    )

    # -- 基础 -------------------------------------------------------------- #
    app_name: str = "Quant Trading"
    version: str = "1.0.0"
    env: Literal["development", "production", "test"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api"

    # -- 数据库 ------------------------------------------------------------ #
    database_url: str = "sqlite:///data/quant.db"

    # -- 数据 -------------------------------------------------------------- #
    data_dir: str = "data"
    data_provider: str = "akshare"
    fallback_providers: list[str] = Field(default_factory=lambda: ["csv"])
    adjust: str = "qfq"
    legacy_cache_dir: str = "data/cache"
    default_symbols: list[str] = Field(default_factory=lambda: ["600519", "000001", "300059"])
    default_start: str = "2020-01-01"

    # -- 安全 -------------------------------------------------------------- #
    master_key: str | None = None
    master_key_file: str | None = None
    access_token_ttl: int = 900  # 15 分钟
    refresh_token_ttl: int = 7 * 24 * 3600
    jwt_issuer: str = "quant-trading"
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    secure_cookies: bool = False
    # 登录失败锁定
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    # 是否强制所有账号开启 TOTP（生产建议 True）
    require_totp: bool = False
    # API 限流（每分钟每 IP 的写请求数）
    rate_limit_per_minute: int = 120

    # -- 初始管理员（仅首次启动使用） -------------------------------------- #
    admin_username: str = "admin"
    admin_password: str | None = None  # 不设置则随机生成并打印到日志

    # -- 引擎 -------------------------------------------------------------- #
    max_concurrent_backtests: int = 2
    max_queued_backtests: int = 200
    default_initial_cash: float = 1_000_000.0
    default_risk_free_rate: float = 0.02
    paper_poll_interval: float = 5.0
    # 是否启用虚拟货币数据源（无境外出口/代理的云服务器可关闭，避免行情请求超时）
    enable_crypto: bool = True
    # 实时快照缓存秒数：越小越实时，但免费行情源高频请求可能限流。
    # A 股建议 >=2；以虚拟货币为主可设 1。
    quote_ttl: float = 3.0
    # 实盘总开关（默认关闭）：即使误配置真实凭据，也不会下单
    enable_live_trading: bool = False
    live_poll_interval: float = 5.0

    # -- 前端 -------------------------------------------------------------- #
    frontend_dist: str = "frontend/dist"

    # ------------------------------------------------------------------ #
    @field_validator("allowed_origins", "fallback_providers", "default_symbols", mode="before")
    @classmethod
    def _split_csv(cls, value):
        """支持 ``A,B,C`` 与 JSON 数组两种写法。"""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    # ------------------------------------------------------------------ #
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    @property
    def store_path(self) -> Path:
        return self.data_path / "store"

    @property
    def frontend_path(self) -> Path:
        path = Path(self.frontend_dist)
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    @property
    def effective_admin_password(self) -> str:
        """首次启动用的管理员口令：未配置则随机生成（长度 20），仅打印一次。"""
        if self.admin_password:
            return self.admin_password
        return secrets.token_urlsafe(15)

    def validate_runtime(self) -> list[str]:
        """启动自检，返回告警列表（不阻断启动）。"""
        warnings: list[str] = []
        if self.is_production:
            if not (self.master_key or self.master_key_file):
                warnings.append(
                    "生产环境未显式配置主密钥，将在 data/master.key 自动生成，请尽快迁移到 Docker secret"
                )
            if "*" in self.allowed_origins:
                warnings.append("生产环境不建议使用通配 CORS 来源")
            if self.debug:
                warnings.append("生产环境不应开启 debug")
            if not self.secure_cookies:
                warnings.append("生产环境建议开启 secure_cookies（需 HTTPS）")
        if self.database_url.startswith("sqlite") and self.is_production:
            warnings.append("生产环境建议使用 PostgreSQL；SQLite 仅适合单实例")
        if self.enable_live_trading:
            warnings.append(
                "实盘开关已开启（QUANT_ENABLE_LIVE_TRADING=true），请确认测试网/真实资金设置与额度上限"
            )
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()


__all__ = ["BASE_DIR", "Settings", "get_settings", "reset_settings"]
