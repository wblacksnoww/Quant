# 框架图（FRAMEWORK）

> 本文是 Quant Terminal 的可渲染版框架图（Mermaid），与
> [PROJECT_MAP.md](PROJECT_MAP.md) §1 的 ASCII 总览、[ARCHITECTURE.md](ARCHITECTURE.md)
> 的分层描述互为补充。分层依赖方向永远向下：`前端 → 服务 → 引擎 → 交易内核 → 基础设施`。
> 更新时间：2026-10-18（虚拟货币 / 场外基金 / 实盘三块接入后；后端 232 项测试全绿）。

## 1. 总体框架

```mermaid
graph TB
    subgraph fe["① 前端 SPA — React + TS + Vite + ECharts"]
        pages["11 页面：Dashboard / Market / Strategies+Blueprint / Backtest / Paper / Live / Funds / Risk / Settings / Login"]
        api["api/client.ts（fetch + 401 静默刷新）+ api/types.ts 手写镜像"]
        store["store：auth / toast / events"]
        wshook["hooks/useWebSocket"]
    end

    subgraph gw["② 网关 — nginx(dev) / Caddy(prod, 自动 HTTPS)"]
        proxy["反代 /api/* · /ws · /healthz → backend:8000"]
    end

    subgraph server["③ 服务层 — FastAPI quant.server"]
        routers["routers ×11：auth · market · strategies · backtest · paper · live · funds · risk · credentials · system · admin"]
        guard["deps 鉴权 · schemas 校验 · custom_params 服务端白名单"]
        btmgr["BacktestManager 持久化队列（原子认领, 每实例并发上限）"]
        ppmgr["PaperManager 每用户一线程（同账号单会话）"]
        lvmgr["LiveManager 每用户一线程（实盘，单会话）"]
        hub["ws.EventHub 按用户定向推送（断线回放 200 条）"]
        recorder["RunRecorder 运行结果落库（runs.kind = backtest/paper/live）"]
    end

    subgraph engine["④ 引擎层 — quant.engine"]
        session["TradingSession（唯一交易时序：T+1/T+0 解冻 → 撮合/回报 → 重估 → 策略 → 风控 → 日终）"]
        bt["BacktestEngine 模拟时钟 · 逐交易日"]
        paper["PaperEngine replay 倍速回放 / live 真实时钟轮询"]
        live["LiveEngine 真实时钟 + 交易所回报（本地不撮合）"]
        frames["live_frames：今日活 bar（纸盘/实盘共用）"]
    end

    subgraph kernel["⑤ 交易内核"]
        strat["strategy：registry 注册 · builtin×4 · custom v1 表单 / v2 蓝图（共用 INDICATOR_SPECS）"]
        exec_sim["execution：SimBroker 撮合 · matching 一字板/量能/停牌 · cost 佣金税滑点"]
        exec_live["execution：LiveBroker 累计回报→增量记账 · adapters{ccxt, miniqmt}"]
        risk["risk：8 条规则链 pre_trade 拒单（原因落库）"]
        funds["funds：compute_positions 申赎流水→持仓/收益（纯函数）"]
        metrics["metrics：夏普 / 回撤 / 索提诺 / 月度 / 基准超额"]
        core_["core：types(float 数量) · events · portfolio(T+0/T+1) · clock · markets(5 市场规则)"]
    end

    subgraph infra["⑥ 基础设施"]
        data_["data：akshare / csv / synthetic / crypto(ccxt) / fund(净值) → normalize → DataStore(Parquet) · realtime 三源"]
        db["storage：SQLAlchemy → SQLite/PostgreSQL · CompressedJSON · fund_trades"]
        sec["security：HKDF+AES-GCM · Argon2id · JWT 轮换 · TOTP · 审计链（凭据加密）"]
    end

    fe ==>|"HTTPS：REST /api + WS /ws"| gw
    gw ==> server
    server ==> engine
    engine ==> kernel
    kernel -.-> infra

    btmgr --> bt
    ppmgr --> paper
    lvmgr --> live
    bt --> session
    paper --> session
    live --> session
    live --> exec_live
    paper -. 进度事件 .-> hub
    live -. 进度事件 .-> hub
    recorder -. 权益/成交/委托 .-> db

    classDef front fill:#e7f5ff,stroke:#1971c2,stroke-width:1.5px
    classDef gate fill:#f8f9fa,stroke:#868e96,stroke-width:1.5px
    classDef srv fill:#e5dbff,stroke:#5f3dc4,stroke-width:1.5px
    classDef eng fill:#d3f9d8,stroke:#2f9e44,stroke-width:1.5px
    classDef krn fill:#fff4e6,stroke:#e67700,stroke-width:1.5px
    classDef inf fill:#ffe8cc,stroke:#d9480f,stroke-width:1.5px
    class fe front
    class gw gate
    class server srv
    class engine eng
    class kernel krn
    class infra inf
```

图例：蓝=前端 · 灰=网关 · 紫=服务层 · 绿=引擎层 · 黄=交易内核 · 橙=基础设施。
实线 `==>` 是请求/事件主通道，虚线是落库与推送。

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

1. **新增两个市场**：`Market.CRYPTO`（`CRYPTO:BTC/USDT`，T+0/无涨跌停/小数数量/7×24）与
   `Market.FUND`（`FUND:007005`，场外基金，净值制）。
2. **新增实盘层**：`LiveEngine + LiveBroker + BrokerAdapter`；`TradingSession` 增加
   `auto_end_of_day` 开关与 `on_order/on_fill` 回调链。
3. **板块规则上移 `core/markets.py`**：涨跌停/整手/T+0 规则已成唯一来源；
   `execution/rules.py` 仅保留再导出（5 处旧 import 待收尾）。
4. **指标白名单单源化**：v1 `custom.py` 与 v2 `custom_graph.py` 共用 `INDICATOR_SPECS`。
5. **数量类型 float**：订单/成交/持仓全链路小数，A 股整手约束仍在校验层生效。
