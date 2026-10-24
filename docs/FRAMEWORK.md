# 框架图（FRAMEWORK）

> 本文是 Quant Terminal 的可渲染版框架图（Mermaid），与
> [PROJECT_MAP.md](PROJECT_MAP.md) §1 的 ASCII 总览、[ARCHITECTURE.md](ARCHITECTURE.md)
> 的分层描述互为补充。分层依赖方向永远向下：`前端 → 服务 → 引擎 → 交易内核 → 基础设施`。
> 更新时间：2026-10-25（C++ 加速核心与 C++ 交易网关接入后的混合架构；后端 279 项测试全绿）。

## 1. 总体框架

```mermaid
graph TB
    subgraph fe["① 前端 SPA — React + TS + Vite + ECharts（可安装 PWA）"]
        pages["13 页面：Dashboard / Market / News / Strategies+Blueprint / Backtest / Paper / Live / Gateway / Funds / Risk / Feedback / Settings / Login"]
        api["api/client.ts + OpenAPI 生成类型（schema.ts，CI 防漂移）"]
        store["store：auth / toast / events / watchlist"]
        wshook["hooks/useWebSocket"]
    end

    subgraph gw["② 网关 — nginx(dev) / Caddy(prod, 自动 HTTPS)"]
        proxy["反代 /api/* · /ws · /healthz → backend:8000"]
    end

    subgraph server["③ 业务层 — FastAPI quant.server（Python）"]
        routers["routers ×14：auth · market · news · strategies · backtest · paper · live · gateway · funds · risk · credentials · feedback · system · admin"]
        btmgr["BacktestManager 持久化队列（原子认领）"]
        ppmgr["PaperManager 多会话（同账号可多开）"]
        lvmgr["LiveManager 按凭证并行"]
        gtmgr["GatewayManager 网关子进程生命周期 + 密钥 env 下发 + TCP 事件接入"]
        hub["ws.EventHub 按用户定向推送"]
        recorder["RunRecorder 落库（runs / 压缩 JSON）"]
    end

    subgraph engine["④ Python 引擎层 — quant.engine"]
        session["TradingSession（唯一交易时序，模拟盘/实盘共用）"]
        bt["BacktestEngine —— 分发：受支持策略走 C++ 快速路径"]
        paper["PaperEngine replay / live（分钟级可选）"]
        live["LiveEngine + LiveBroker + adapters{ccxt, miniqmt}"]
    end

    subgraph native["⑤ C++ 加速核心 — quant._core（pybind11）"]
        ncore["qcore：账本 · 撮合 · 8 风控规则 · 14 指标增量状态机"]
        nspec["拼装策略 v1/v2 解释器 + 内置策略直译 + 回测主循环（~70×，等价性测试把门）"]
    end

    subgraph gateway["⑥ C++ 交易网关 — quant-gateway 独立进程"]
        gws["OKX WS 公共行情(tick) + 私有(登录/orders 回报)"]
        gtick["TickEngine tick→bar 聚合 · 网格/突破/动量纯 tick 策略"]
        gord["专职写线程下单(clOrdId 幂等, µs) · 启动恢复(REST) · JSONL 审计 · 本地 TCP :8790"]
    end

    subgraph kernel["⑦ 交易内核（Python 侧，供引擎/网关共用语义）"]
        strat["strategy：registry · builtin×4+news_dual_ma · custom v1/v2"]
        risk["risk：8 条规则链 pre_trade 拒单"]
        funds["funds：申赎流水聚合（纯函数）"]
        metrics["metrics：夏普 / 回撤 / 月度"]
    end

    subgraph infra["⑧ 基础设施"]
        data_["data：akshare / csv / synthetic / crypto / fund → DataStore(Parquet) + 分钟线增量缓存"]
        db["storage：SQLAlchemy → SQLite/PostgreSQL · 会话租约 · 持久化回测队列 · 新闻/自选/反馈"]
        sec["security：HKDF+AES-GCM · Argon2id · JWT 轮换 · TOTP · 审计链"]
        news["news：RSS 抓取 → 规则分类 → PIT 因子"]
    end

    fe ==>|HTTPS REST + WS| gw
    gw ==> server
    server ==> engine
    bt ==>|受支持策略| ncore
    gtmgr ==>|子进程 + 127.0.0.1 TCP| gateway
    engine ==> kernel
    gateway -. qcore 风控/账本复用 .-> ncore
    kernel -.-> infra
    gtmgr -. 网关事件(订单/成交/状态) .-> hub

    classDef front fill:#e7f5ff,stroke:#1971c2
    classDef gate fill:#f8f9fa,stroke:#868e96
    classDef srv fill:#e5dbff,stroke:#5f3dc4
    classDef eng fill:#d3f9d8,stroke:#2f9e44
    classDef nat fill:#ffc9c9,stroke:#c92a2a,stroke-width:2px
    classDef gtw fill:#ffdeeb,stroke:#a61e4d,stroke-width:2px
    classDef krn fill:#fff4e6,stroke:#e67700
    classDef inf fill:#ffe8cc,stroke:#d9480f
    class fe front
    class gw gate
    class server srv
    class engine eng
    class native nat
    class gateway gtw
    class kernel krn
    class infra inf
```

图例：蓝=前端 · 灰=反代 · 紫=业务层 · 绿=Python 引擎 · **红=C++ 回测核心** · **粉=C++ 交易网关** · 黄=交易内核 · 橙=基础设施。
实线 `==>` 是请求/事件主通道，虚线是落库、推送与复用。

**策略执行路径速查**：回测（拼装 v1/v2 + 4 内置策略 + 指标）→ C++ `_core`（~70×，等价性测试保证与 Python 逐点一致，故障自动回退）；模拟盘/实盘引擎 → Python 解释器；C++ 网关 → 网格/突破/动量三个纯 tick 策略全程 C++（tick→策略→下单，网关内部 ≈50µs）。

## 2. 一次回测的完整链路

```mermaid
sequenceDiagram
    participant FE as 前端
    participant API as FastAPI routers/backtest
    participant BM as BacktestManager
    participant ENG as TradingSession + BacktestEngine
    participant RK as RiskManager
    participant BK as SimBroker
    participant DB as RunRecorder
    participant WS as EventHub /ws

    FE->>API: POST /api/backtests（spec + 参数）
    API->>API: schemas 校验 · 按用户注入 custom spec（服务端白名单）
    API-->>FE: 202 + run_id
    API->>BM: 写入 backtest_jobs（queued）
    BM->>ENG: worker 原子认领
    alt 拼装策略 / 4 内置策略 / 无 benchmark
        ENG->>ENG: 走 C++ quant._core（账本·撮合·指标·解释器·主循环，~70×）
        Note over ENG: 等价性测试保证与 Python 逐点一致；故障自动回退
    else 其他策略 / 带 benchmark
        ENG->>ENG: Python TradingSession 路径
    end
    loop 逐交易日推进
        ENG->>BK: 跨日解冻(T+1/T+0) → 撮合「昨日」订单（开盘价）
        ENG->>ENG: 收盘价重估权益 → strategy.on_bars（游标防未来函数）
        ENG->>RK: ctx.submit() 过 8 条风控链
        RK-->>ENG: 拒绝（BlockReason 落库）/ 入队
        ENG->>ENG: on_order / on_fill 回调策略
    end
    ENG->>DB: 权益曲线/成交/委托/持仓落库（CompressedJSON）
    ENG->>WS: run.* 进度事件（按 user_id 定向）
    WS-->>FE: 实时进度推送
    ENG-->>FE: 200 + PerformanceReport
```

## 3. 一次实盘的完整链路

```mermaid
sequenceDiagram
    participant FE as 前端（实盘页）
    participant API as routers/live
    participant ENG as LiveEngine
    participant LB as LiveBroker
    participant AD as BrokerAdapter（ccxt/miniqmt）
    participant EX as 交易所/券商
    participant RK as RiskManager
    participant WS as EventHub /ws

    FE->>API: POST /api/live/start（闸门1 总开关 → 闸门2 测试网/确认 → 闸门3 凭据审计）
    API-->>FE: 202 + run_id
    API->>ENG: 后台线程
    ENG->>AD: connect() · fetch_balance/positions
    ENG->>LB: bind_account()（本地镜像以交易所为准）
    loop 每 poll_interval 秒
        ENG->>LB: sync()
        LB->>AD: sync()（累计成交/状态）
        AD->>EX: fetch_order / query_stock_orders
        LB->>LB: 增量记账 → on_fill
        ENG->>AD: quote()（失败回退 data.realtime）
        ENG->>ENG: TradingSession.step → strategy.on_bars
        ENG->>RK: ctx.submit() 风控前置
        ENG->>LB: submit() → AD.place_order() → EX
        ENG->>LB: 跨日 → 撤昨日 DAY 单
        ENG->>LB: 每 5 分钟 reconcile()（差异只报告）
    end
    ENG->>WS: run.* 事件（订单/成交/权益/日志）
    FE->>API: POST /api/live/stop
    API->>ENG: stop → session.finish → cancel_all（逐单撤单）
```

## 4. 场外基金链路

```mermaid
graph LR
    A["记一笔申购/赎回<br/>POST /funds/trades"] --> B["fund_trades 流水表"]
    B --> C["compute_positions()<br/>加权成本 / 赎回盈亏"]
    D["FundProvider<br/>akshare 净值"] --> E["最新净值 / 历史净值"]
    C --> F["GET /funds/holdings<br/>份额·成本·市值·收益"]
    E --> F
    E --> G["GET /funds/nav<br/>NavChart 折线"]
```

场外基金**不进事件驱动引擎**：净值确认制没有盘中撮合，用流水聚合才是正确的记账口径。

## 5. 与旧框架图的差异

1. **C++ 加速核心（quant._core）**：回测热路径（账本/撮合/风控/14 指标/拼装策略
   解释器/内置策略/主循环）C++ 化，与 Python 逐点等价（等价性测试），~70× 提速；
   不可用或不受支持自动回退 Python。
2. **C++ 交易网关（quant-gateway）**：策略层全 C++ 的独立进程——OKX 公共/私有 WS、
   tick→bar 聚合、网格/突破/动量纯 tick 策略、qcore 风控、WS 下单（clOrdId 幂等、
   专职写线程 µs 级）、启动恢复与 JSONL 审计；Python GatewayManager 管生命周期/密钥/事件。
3. **多市场**：`Market.CRYPTO`（T+0/无涨跌停/小数数量/7×24）与 `Market.FUND`（净值制）。
4. **实盘层**：`LiveEngine + LiveBroker + BrokerAdapter`（ccxt / miniqmt），三重闸门。
5. **指标白名单单源化**：v1/v2 共用 `INDICATOR_SPECS`；C++ 侧为增量状态机实现。
6. **数量类型 float**：全链路小数，A 股整手约束在校验层生效。
