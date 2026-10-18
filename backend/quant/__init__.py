"""Quant Trading System —— 可扩展、可容器化、带加密的 A 股量化系统。

分层结构（上层可依赖下层，下层绝不反向依赖上层）::

    quant.core        领域内核：行情/订单/成交/持仓/事件/时钟（纯标准库）
    quant.data        数据层：Provider 抽象 + 本地缓存 + 交易日历
    quant.execution   执行层：费用模型 / 撮合规则 / Broker（回测与纸盘共用）
    quant.strategy    策略层：Strategy 基类 + 注册表 + 内置策略
    quant.risk        风控层：下单前风控规则链
    quant.engine      引擎层：回测引擎 / 纸盘引擎（同一套组件）
    quant.metrics     绩效指标
    quant.security    安全层：信封加密 / 口令 / JWT / TOTP / 审计哈希链
    quant.storage     持久化：SQLAlchemy ORM + 仓储
    quant.server      HTTP/WS 接口：FastAPI 应用

设计要点：**同一套 Strategy / Broker / Risk 组件**既跑回测也跑纸盘，
二者的差别被收敛到 ``quant.engine`` 的驱动方式上（推历史 bar vs 推实时 bar）。
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
