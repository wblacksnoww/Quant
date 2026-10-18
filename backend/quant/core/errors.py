"""统一异常体系。

分层的异常便于 API 层把领域错误映射成合适的 HTTP 状态码，
也便于策略作者区分"数据没准备好"和"风控不给过"。
"""

from __future__ import annotations


class QuantError(Exception):
    """所有业务异常的基类。"""

    code = "quant_error"
    http_status = 500

    def __init__(self, message: str = "", **context):
        super().__init__(message)
        self.message = message or self.__class__.__name__
        self.context = context

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "context": self.context}


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


class ConfigError(QuantError):
    code = "config_error"
    http_status = 500


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #


class DataError(QuantError):
    code = "data_error"
    http_status = 502


class DataNotFound(DataError):
    code = "data_not_found"
    http_status = 404


class DataProviderUnavailable(DataError):
    code = "data_provider_unavailable"
    http_status = 503


# --------------------------------------------------------------------------- #
# 交易
# --------------------------------------------------------------------------- #


class BrokerError(QuantError):
    code = "broker_error"
    http_status = 502


class InsufficientCash(BrokerError):
    code = "insufficient_cash"
    http_status = 400


class InsufficientPosition(BrokerError):
    code = "insufficient_position"
    http_status = 400


class OrderRejected(BrokerError):
    code = "order_rejected"
    http_status = 400


class RiskRejected(OrderRejected):
    code = "risk_rejected"


class LimitUpDownBlocked(OrderRejected):
    """涨跌停导致无法成交。"""

    code = "limit_updown_blocked"


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #


class EngineError(QuantError):
    code = "engine_error"
    http_status = 500


class EngineNotRunning(EngineError):
    code = "engine_not_running"
    http_status = 409


class EngineAlreadyRunning(EngineError):
    code = "engine_already_running"
    http_status = 409


# --------------------------------------------------------------------------- #
# 安全
# --------------------------------------------------------------------------- #


class SecurityError(QuantError):
    code = "security_error"
    http_status = 500


class AuthError(QuantError):
    code = "auth_error"
    http_status = 401


class PermissionDenied(AuthError):
    code = "permission_denied"
    http_status = 403


class CryptoError(SecurityError):
    """加解密失败（密钥不对、密文被篡改、格式非法）。"""

    code = "crypto_error"


class MasterKeyMissing(CryptoError):
    code = "master_key_missing"
    http_status = 500


__all__ = [
    "AuthError",
    "BrokerError",
    "ConfigError",
    "CryptoError",
    "DataError",
    "DataNotFound",
    "DataProviderUnavailable",
    "EngineAlreadyRunning",
    "EngineError",
    "EngineNotRunning",
    "InsufficientCash",
    "InsufficientPosition",
    "LimitUpDownBlocked",
    "MasterKeyMissing",
    "OrderRejected",
    "PermissionDenied",
    "QuantError",
    "RiskRejected",
    "SecurityError",
]
