# 架构设计

本文说明 Quant Terminal 的分层、事件时序、关键子系统的实现约定，以及扩展点。
目标读者：要改代码或加功能的人。

## 1. 总览

```
┌────────────────────────────────────────────────────────────────┐
│ 前端 SPA（React + TS + Vite + ECharts）                          │
│ 认证（内存 access / localStorage refresh）· 轮询 + WebSocket     │
└──────────────┬─────────────────────────────┬───────────────────┘
               │ REST /api                    │ WS /ws
┌──────────────▼─────────────────────────────▼───────────────────┐
│ FastAPI 应用（quant.server）                                     │
│  中间件：RequestContext → 安全响应头 → 限流                      │
│  路由：auth · market · strategies · backtest · paper ·          │
│        risk · credentials · system · admin                      │
│  BacktestManager（持久化队列 worker）· PaperManager（按用户会话池）  │
│  EventHub（按用户投递事件到 WS）· RunRecorder（运行落库）           │
└──────────────┬─────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────┐
│ 引擎层（quant.engine）                                           │
│  TradingSession（唯一交易时序）                                  │
│   ├─ BacktestEngine  模拟时钟，逐交易日推进                       │
│   └─ PaperEngine     replay 倍速回放 / live 真实时钟轮询          │
└──────────────┬─────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────┐
│ 交易内核                                                        │
│  Strategy / StrategyContext ── RiskManager ── SimBroker         │
│  MatchingEngine · CostModel · BoardRules(涨跌停/整手)            │
│  Account / Position（T+1）· EventBus · Clock                     │
│  Metrics（绩效指标）                                             │
└──────────────┬─────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────┐
│ 基础设施                                                        │
│  DataStore（parquet 缓存）← akshare / csv / synthetic 数据源     │
│  SQLAlchemy 2.0 → SQLite / PostgreSQL                           │
│  security：HKDF 子密钥 · AES-GCM · Argon2id · JWT · TOTP · 审计链 │
└────────────────────────────────────────────────────────────────┘
```

依赖方向永远向下：`server → engine → 交易内核 → 基础设施`。
引擎与服务器都不反向依赖路由；`RunRecorderProtocol` 让引擎只在运行时依赖「记录器接口」，
单测里可以传假实现。

## 2. 为什么自研内核（而不是 backtrader）

- **回测与纸盘必须共用一套代码**。backtrader 的撮合/事件模型与真实纸盘差异大，
  容易「回测能过、纸盘不对」。这里把交易时序抽成 `TradingSession`，
  两个引擎只负责"怎么推进时间"。
- **数据源、风控、指标都是项目特有逻辑**，插件式注册比继承框架类更简单直观。
- **可测试性**：内核不依赖 pandas IO、不依赖网络，纯内存即可跑完整会话（见 `tests/test_engine.py`）。

代价是撮合保真度取决于我们自己的建模（下一节），因此每次改动撮合都要跑回测确定性测试。

## 3. 事件时序（防未来函数的核心）

一次 `TradingSession.step(moment, bars)` 的顺序**不可随意调整**：

```
1. 跨日？ → account.on_new_session()   # T+1 解冻：昨仓变为可卖
2. broker.on_bar(bar)                  # 撮合「昨天收盘后」下的单，用当日 OHLC
3. mark_price → 权益快照                # 以收盘价重估
4. 策略 on_bars(bars)                  # 策略看到的是「当日收盘为止」的数据
   └─ ctx.submit() 内部先过 RiskManager  # 风控在提交时同步拦截
5. broker.end_of_day()                 # 当日未成交的 DAY 单过期
```

结论：**策略在第 4 步决策，最早在第 2 步（次日开盘）成交**。
`StrategyContext.history()` 只能读游标之前的数据（`ctx.seek_all` 由引擎推进），
从接口上杜绝"偷看下一根 bar"。

## 4. 撮合与成本（`quant.execution`）

- **市价单**：以撮合 bar 的开盘价成交（因为订单是在上一根 bar 之后下达的）。
- **限价单**：买单要求 `bar.low <= 限价`（取 `min(限价, 开盘价)` 保守成交）；
  卖单要求 `bar.high >= 限价`（取 `max(限价, 开盘价)`）。
- **涨跌停**：一字涨停买不进、一字跌停卖不出，拒绝原因写入 `BlockReason`，
  前端可解释"为什么信号出现了却没买进"。
- **成交量约束**：单根 bar 最多吃掉 `volume_limit_pct`（回测默认 10%，纸盘关闭）。
- **整手**：买入按 100 股取整（科创板 200 股，见 `BoardRules`），卖出允许零股清仓；
  港股按每手 100 股取整，美股允许任意股数（lot=1）。
- **涨跌停**：仅 A 股适用（主板 10%、创业/科创 20%、ST 5%）；美股 / 港股无价格限制，
  规则里 `limit_pct=0` 时自动返回 `(None, None)`，撮合不做涨跌停拦截。
- **成本**：A股默认 佣金 0.03%（最低 5 元）、印花税 0.05%（仅卖出）、过户费 0.001%（双向）、
  滑点按基点，滑点金额单独计入费用明细（成交价里不藏成本）。
  成本模型按市场取预设 `CostModel.for_market()`：美股 佣金 0.01%（无最低、无印花、无过户）；
  港股 佣金 0.01% + 印花税 0.1%（**双边**，`stamp_both_sides` 字段驱动）。
  请求里显式传 `cost` 时以显式配置为准（`BacktestRequest.cost` 可选）。
- **资金校验**：下单即冻结估算现金；卖出冻结可卖数量；撤单释放冻结。

## 5. 引擎与任务

| 引擎 | 时间驱动 | 撮合方式 | 典型用途 |
| --- | --- | --- | --- |
| `BacktestEngine` | 模拟时钟，逐交易日 | 次日开盘撮合 | 历史验证、参数研究 |
| `PaperEngine.replay` | 回放最近 N 个交易日，按 `replay_interval` 加速 | 同回测 | 快速演练策略行为 |
| `PaperEngine.live` | 真实时钟，轮询快照 | 市价单立即成交 | 盘中模拟 |
| `LiveEngine` | 真实时钟，轮询快照 | **交易所真实撮合**（本地不撮合） | 实盘下单 |

- 回测走**持久化队列**：提交写入 `backtest_jobs`（`queued`），`BacktestManager` 的
  worker 用条件更新原子认领；`QUANT_MAX_CONCURRENT_BACKTESTS` 控制本实例并发，
  `QUANT_MAX_QUEUED_BACKTESTS` 控制队列长度。提交返回 `202` + `run_id`，前端轮询
  `GET /api/backtests/{id}` 并订阅 WS 进度；重启后 queued 任务保留、失联的 running 自动重排。
- 纸盘**按用户隔离、可多开**：`PaperManager` 为每个用户的每个 `run_id` 维护一个 `PaperRunner`
  （默认每用户最多 5 个并发会话）；每个会话有独立的虚拟账户与线程，可分别查看/停止。
  不同账号互不影响（各自线程、各自落库）。
- 结果统一落 `runs` 表：指标、权益曲线、成交、委托、持仓、日志（JSON 压缩存储），
  大 payload 由 `CompressedJSON` 类型 zlib 压缩。

#### 运行中切换策略（纸盘 / 实盘）

`POST /api/paper/switch`、`POST /api/live/switch`：

- 只做一次 `pending` 写入，**下一轮 `TradingSession.step()` 开始时**替换策略实例，
  避免与 `on_bars` 并发修改；
- 默认撤销旧策略的未成交挂单（`cancel_open_orders=true`），持仓与资金保持不变；
- 旧策略 `on_stop()` → 新策略 `on_start()`；**策略内部状态不继承**；
- `ctx.strategy_name` 同步更新，之后下的单都标记为新策略；
- 切换写入 `run.log` 与审计（`paper.switch_strategy` / `live.switch_strategy`），
  `GET /api/*/state` 的 `strategy_id` 反映当前策略（`runs` 表保留启动时的策略用于追溯）。

### 5.1 实盘通道（`quant.execution.adapters`）

实盘 = `LiveEngine` + `LiveBroker` + `BrokerAdapter`，三者职责严格分离：

| 组件 | 职责 |
| --- | --- |
| `LiveEngine` | 真实时钟循环：同步回报 → 取价 → `TradingSession.step`（策略决策） |
| `LiveBroker` | 实现 `Broker` 接口；把交易所**累计口径**回报换算成增量成交并记账；启动对账、停止撤单 |
| `BrokerAdapter` | 只做翻译：下/撤单、余额、持仓、订单查询、报价（`ccxt` / `miniqmt`） |

- **不本地撮合**：`LiveBroker.on_bar()` 不产生成交，只把 `sync()` 收到的成交交给会话/策略。
- **幂等**：适配器返回累计成交量/均价/费用，`LiveBroker` 用差值记账，重复 `sync()` 不重复成交。
- **对账**：`reconcile()` 比对本地镜像与交易所资金/持仓，只报告不自动改账。
- **日终语义**：`TradingSession(auto_end_of_day=False)`，实盘由 `LiveEngine` 在真正跨日时
  撤 DAY 单（避免每轮轮询撤掉当日挂单）；GTC 单保留。
- **策略回调**：`TradingSession` 在撮合/回报后调用 `strategy.on_fill()`，订单状态变化调用
  `strategy.on_order()`（同一状态只通知一次）。
- 三重闸门与操作细节见 [LIVE_TRADING.md](LIVE_TRADING.md)。

## 6. 策略插件模型

```python
from quant.strategy.base import Strategy, ParamSpec, register_strategy

@register_strategy
class MyStrategy(Strategy):
    strategy_id = "my_breakout"
    display_name = "突破策略"
    params_schema = (
        ParamSpec("lookback", "int", 20, min=5, max=120, label="回看天数"),
        ParamSpec("threshold", "float", 0.02, step=0.005, label="突破幅度"),
    )

    def on_bars(self, bars): ...
```

- `@register_strategy` 是唯一要求；类被 import 即注册，API `GET /api/strategies` 自动返回
  含参数 schema 的目录，前端**自动渲染表单**——新增策略不需要动路由和前端。
- `StrategyContext` 提供：`history / bar / price / position / positions / equity / cash /
  exposure / submit / cancel / log / state`，以及 `ParamSpec` 之外的运行期 `ctx.now`。
- 内置策略在 `quant/strategy/builtin/`：`buy_hold`、`dual_ma`、`rsi_reversion`、
  `momentum_rotation`。指标函数在 `quant/strategy/indicators.py`（纯 pandas，无状态）。

### 6.1 条件拼装策略（免代码，`custom_rule`）

不想写 Python 的用户可以在网页「策略库 → 我的拼装策略」里用下拉框拼买卖规则，
保存到 `user_strategies` 表（按用户隔离），回测/纸盘提交时选择即可运行：

- spec 是纯 JSON：`{entry[], exit[], position_pct, stop_loss, take_profit}`；
  `entry` 条件**全部满足**且空仓才买入，`exit` 条件**任一满足**即清仓；
  操作数三选一：`field`（开高低收）/ `indicator`（sma/ema/rsi/std/rolling_high/rolling_low/
  momentum/atr/boll 三轨/macd 三线，参数带边界）/ `const`（常数）。
- **安全边界**：指标/字段/比较符全部白名单，无 `eval`、无 import 面；spec 在
  创建（CRUD）、更新、提交运行三条路径都过 `quant.strategy.custom.parse_spec` 校验，
  且有 spec 字节上限（8KB）与条件数上限（每侧 ≤6）。
- 实现：一个通用解释器类 `CustomRuleStrategy`（`quant/strategy/custom.py`），
  spec 通过 `params["spec"]` 传入，`ParamSpec` 新增 `"json"` 类型原样透传；
  提交运行时 `quant/server/custom_params.py` 允许 `params["custom_strategy_id"]`
  → 服务端按归属取 spec 注入（拼装策略仅本人可见）。
- 订单语义与内置策略一致：on_bar 决策 → 次日开盘撮合；止损/止盈优先于信号判断。
  API：`GET/POST /api/strategies/custom`、`PATCH/DELETE /api/strategies/custom/{id}`。

## 6.2 蓝图编辑器（spec v2：节点数据流图）

`/strategies/blueprint` 页面提供 React Flow（`@xyflow/react`，n8n/Langflow 同内核，
本机 npmmirror 可达）画布：拖节点、连色线、存成 **spec v2**。与 6.1 的表单拼装共用
同一个策略 ID `custom_rule` 与同一套订单纪律，spec 版本决定解释路径：

- **图语义是数据流**：每个节点输出一条时间序列（或常数），连线即引用；
  `outputs.entry / exit` 指向的布尔节点在当前 bar 的取值决定买入/清仓。
  节点类型白名单（`quant/strategy/custom_graph.py`）：数据源（开高低收量）、
  `const`、11 个指标（参数边界与 v1 一致）、算术（`add/sub/mul/div`，除零→NaN）、
  比较（`gt/gte/lt/lte` + `cross_above/cross_below`，交叉只能吃序列）、逻辑
  （`and/or/not`）。节点 ≤48、连线 ≤96、spec ≤16KB。
- **类型连线**：端口分「数值（青）」与「布尔（橙）」——布尔只能进逻辑端口与 outputs，
  常数不能进指标的 `in`（指标必须吃序列）。前端 `onConnect` 同步校验并给连线着色，
  服务端在 `parse_graph_spec` 再做一遍结构校验（未知类型/端口重复/缺失输入/环检测
  都是 422 级中文报错）。
- **校验细节**：节点字段白名单（`id/type/pos` + `const.value` + 指标 `params`）；
  `pos` 是编辑器布局坐标，服务端仅透传不参与计算；**悬挂节点**（编辑器遗留）保留
  结构、不参与预热与求值（可达性剪枝）；环用 Kahn 判定。
- **预热对齐**：交叉节点自带 +1（前一根比较），`graph_lookback = lookback + 1`
  与 v1 的 `lookback + 2` 等价——同一数据的金叉/死叉交易日与 v1 条件、内置
  `dual_ma` 逐点一致（有等价性测试锚定）。
- **实现**：前端 `frontend/src/components/blueprint/`（`domain.ts` 节点注册表 ↔
  后端白名单一一对应、`compile.ts` 画布↔spec 双向编译、`nodes.tsx`、
  `FlowEditor.tsx`、`templates.ts` 内置双均线/RSI 模板）；编辑器保留「买入/卖出输出」
  哨兵节点不可删除，保存时本地编译报错（中文清单）优先于服务端 422。
- 测试见 `tests/test_custom_strategy.py` 的 v2 段落（24 个用例：类型连线、环、
  悬挂、预热、与 v1/dual_ma 等价、端到端 API）。

## 7. 风控链



`RiskManager` 顺序执行规则，任一 `critical` 立即短路：

| id | 说明 | 默认 |
| --- | --- | --- |
| `max_position_pct` | 单票市值 / 权益 | 98% |
| `max_order_value_pct` | 单笔委托金额 / 权益 | 98% |
| `max_positions` | 最大持仓只数 | 20 |
| `daily_loss_limit` | 当日亏损熔断 | 10% |
| `max_drawdown` | 权益回撤熔断（只拦买） | 30% |
| `order_rate_limit` | 窗口内下单笔数（按市场时钟） | 120/60s |
| `blacklist` | 黑名单标的 | 空 |
| `lot_size` | 整手校验 | 开 |

默认值刻意宽松——它们拦的是"代码写错"级事故，而不是替用户决定仓位。
更严的预设通过 `GET /api/risk/presets` 提供，前端可一键套用；
每次拒绝（含原因、规则 id）都会落进运行日志。

## 8. 数据层

- **符号体系（多市场的地基）**：全系统统一 `MARKET:CODE` 规范 id —— `CN:600519` / `US:AAPL` / `HK:00700` /
  `CRYPTO:BTC/USDT` / `FUND:007005`，解析与规范化集中在 `quant/core/markets.py`。解析只发生在边界
  （HTTP 入参、前端输入、缓存文件名、localStorage），业务代码内部一律持有规范 id。
  `parse_instrument()` 接受常见写法并启发式归一：6 位数字→A股、1~5 位→港股、字母→美股
  （`600519`、`SH600519`、`600519.SH`、`AAPL`、`TSLA.N`、`0700.HK` 都能归一；美股交易所后缀
  `.OQ/.N/.AM` 会被剥掉）。**虚拟货币与场外基金必须显式前缀**（`CRYPTO:`/`FUND:`），
  否则裸 6 位数字会被 A 股启发式截胡。无法判定时显式报 422，不猜。
- **单一市场约束**：一次回测/纸盘会话只允许一个市场（`EngineConfig.validate()` 校验）——
  不同货币无法统一记账，混市场提交直接 422 并说明混用了哪些市场。
- `MarketDataService`（`quant/data/store.py`）负责：缓存查询 → 逐级降级取数 → 规范化 → 落 parquet。
  缓存文件名带市场前缀 `{MARKET}_{CODE}_{adjust}.parquet`（如 `CN_600519_qfq.parquet`），
  启动时自动把旧的裸代码命名迁移成新命名。
- 数据源通过 `@register_provider("name")` 注册，实现 `daily()` 即可接入；
  可选实现 `realtime()` / `snapshot()`、`search()`、`calendar()`。
- 内置：`akshare`（默认）、`csv`（`data/cache/*.csv` 离线种子）、`synthetic`（确定性随机，
  仅用于链路验证）、`synthetic_volatile`、`crypto`（ccxt 交易所 K 线）、`fund`（akshare 场外基金净值）。
- **虚拟货币（`crypto`）**：ccxt 封装，交易所由 `QUANT_CRYPTO_EXCHANGE` 配置（默认 okx），
  直连不畅时用 `QUANT_CRYPTO_PROXY`；`fetch_ohlcv` 分页拉日线，7×24 全周日历；
  搜索用内置主流 USDT 交易对白名单（ccxt 无免费检索）；缓存文件名把 `/` 转义为 `~`。
- **场外基金（`fund`）**：`fund_name_em` + `fund_open_fund_info_em`，净值映射成 OHLCV
  （OHLC=净值、volume=0）以复用图表；**不进入撮合引擎**，申赎走 `quant.funds` 的流水聚合。
- **日线主源分市场**（`akshare_provider.daily()`）：A股 `stock_zh_a_hist`、美股 `stock_us_daily`、
  港股 `stock_hk_daily`（sina 源，量纲为股）；失败统一回退腾讯 K 线
  （`web.ifzq.gtimg.cn` 的 `fqkline` / `usfqkline` / `hkfqkline` 三端点，单次 640 根向前分页；
  美股代码需交易所后缀如 `usAAPL.OQ`，后缀从实时快照顺路缓存）。
- **检索统一走腾讯 smartbox**（`quant/data/search.py`）：A 股 / 港股 / 美股一个入口，
  过滤场外基金（`jj`）与指数（`ZS`），美股剥交易所后缀；失败回退 akshare A 股代码表。
  `GET /api/market/search?q=&market=` 支持 `CN/US/HK` 过滤。
- **实时行情是多源聚合**（`quant/data/realtime.py`）：腾讯 `qt.gtimg.cn` → 东财
  `push2.ulist.np` → 新浪 `hq.sinajs.cn`，逐源补齐缺失标的；三个市场各有各的代码映射
  （A股 `sh/sz/bj` 前缀、港股裸 5 位、美股 `gb_` 小写或 `us` 带后缀）。
  快照统一为 名称/现价/昨收/涨跌/量额/时间 + `market`/`currency`，**成交量一律换算成股**
  （腾讯 A 股手×100、东财手×100、新浪与港美原生股），成交额为本币元。
  单源被限流（TLS 正常但连接被重置是常见表现）不影响整体可用性。
- `MarketDataService.snapshot()` 带 **3 秒 TTL 缓存**（抵挡页面轮询），数据源全部失败时
  回退"最近一次成功快照"并标记 `stale=True`，附带 `note` 说明；引擎 live 模式复用同一份快照。
- **指数（`GET /api/market/indices`）**：内置 12 个常用指数（上证/深证/创业板/沪深300/中证500/
  科创50、恒生/国企/恒生科技、道琼斯/纳斯达克/标普500），腾讯 `qt.gtimg.cn` 一次请求
  批量取点数与涨跌，服务端 5 秒缓存、失败保留上次结果。指数**不可交易**，用独立 id
  （`sh000001` / `hkHSI` / `usDJI`）而不进入 `MARKET:CODE` 体系，避免与股票代码冲突
  （上证指数 000001 与平安银行 000001）。前端卡片可编辑显示项与顺序，配置存 localStorage。
- **分钟 K 线（`GET /api/market/intraday`）**：详情页的"今日/本周"视图。
  A股走新浪 `CN_MarketDataService.getKLineData`（scale=1/5/15/30/60，容器内可用；
  腾讯分钟接口对容器 IP 返回 301/空），虚拟货币走 ccxt `fetch_ohlcv(timeframe=…)`；
  港股/美股暂无稳定免费分钟源，接口返回 422 并在前端置灰按钮。
  详情页今日=5 分钟、本周=30 分钟，每 15 秒自动刷新；日线范围（本月/1/3/5 年）走 `/market/bars`。
- **任意周期 K 线（`GET /api/market/klines`）**：`1m/5m/15m/30m/60m/1d/1w/1M`。
  周线/月线由日线重采样（`W-FRI` / `ME`，日期取周期内最后一个交易日，避免出现未来的月末）；
  前端范围映射：今日 5m、本周 30m、本月 1d、1 年 1d、3 年 1w、5 年 1M。
  持续缩小到当前周期的可视根数上限（或接近全部数据）时，自动升到更长周期，
  形成「5 分钟 → 30 分钟 → 日线 → 周线 → 月线」的递进；刷新数据时保留用户当前缩放窗口。
- **排行榜（`GET /api/market/ranking`）**：支持 `period=day|week|month`。
  - 今日榜：A股/港股取新浪行情中心的全市场榜单（`hs_a` / `qbgg_hk`），
    美股用 30 只高关注度标的池 + 多源快照，虚拟货币用 ccxt 主流交易对批量 tickers；
  - 本周/本月榜：免费源没有全市场周/月涨幅字段，因此取「成交额前 30 + 涨幅前 15 +
    跌幅前 15」的并集（虚拟货币为成交额前 8），并发拉日线计算自然周/月涨幅
    （A股用腾讯 K 线，容器内被风控时回退新浪；虚拟货币用 ccxt 日线）；
  - 东财对 Python 客户端的 TLS 指纹会直接断开连接，因此没有采用东财 spot 全表；
  - 服务端缓存：今日榜 5 秒、周/月榜 120 秒；前端今日榜 5 秒刷新、周/月 30 秒；
  - 找不到数据源的市场返回空列表 + `note`，不伪造数据；周/月榜的 `note` 会说明样本口径。
- `QUANT_FALLBACK_PROVIDERS` 定义降级链；每次降级都会打日志并在响应里带 `source`，
  避免"以为用的是真实行情，其实是合成数据"。
- 标准化 schema：`date open high low close volume amount prev_close limit_up limit_down`，
  涨跌停缺失时由 `quant.execution.rules` 按 A 股板块规则推算（港美为 `None`）。
- **交易日历**：A股用 akshare 真实日历；美股/港股退化为"工作日近似"；虚拟货币为全周日历；
  `is_market_open(market, moment)` 按各市场时区判断开盘时段（CN/HK UTC+8、美股 UTC-4 近似夏令时、
  CRYPTO 恒为开市）。

### 8.1 交易规则差异（`core.markets.BoardRules`）

| 市场 | 涨跌停 | 整手 | 交收 | 报价精度 |
| --- | --- | --- | --- | --- |
| A 股主板 | ±10%（ST ±5%） | 100 股 | T+1 | 2 位 |
| 创业板/科创板 | ±20% | 100 / 200 股 | T+1 | 2 位 |
| 美股 | 无 | 1 股 | T+0 | 2 位 |
| 港股 | 无 | 100 股（近似） | T+0 | 3 位 |
| 虚拟货币 | 无 | 任意（小数） | T+0 | 8 位 |
| 场外基金 | 无 | 任意（份额小数） | —（净值确认制） | 4 位 |

`t_plus_1` 由 `Account.apply_fill` 真实消费（T+0 市场当日买入立即可卖）；
订单数量全链路为 `float`，A 股整手约束在 `is_valid_lot` / 撮合层保留。

### 8.2 场外基金模块（`quant.funds` + `routers/funds.py`）

- **不进引擎**：场外基金无盘中撮合、按 T 日净值确认份额，塞进逐 bar 撮合只会制造假精度。
- **流水即账本**：`fund_trades` 表只存申赎流水；持仓/收益由 `compute_positions()` 纯函数聚合，
  避免"持仓表"与"流水表"双写不一致。
- 申购：`份额 = (金额 - 申购费) / 净值`；赎回：`到账 = 份额 × 净值 - 赎回费`，
  已实现盈亏按加权平均成本结转。
- 删除流水即重算（补录错误修正），写操作留审计。

## 9. 存储

SQLAlchemy 2.0 同步 ORM，表结构（`quant/storage/models.py`）：

| 表 | 说明 |
| --- | --- |
| `users` | 账号、Argon2id 哈希、角色、TOTP 状态与加密种子、失败计数与锁定 |
| `refresh_tokens` | jti、SHA-256 摘要、过期与吊销时间、UA/IP |
| `audit_logs` | 追加式哈希链（`prev_hash` + `hash`），唯一索引防重复 |
| `credentials` | 加密 payload（AES-GCM 密文），列表接口不返回 payload |
| `runs` | 回测/纸盘运行：配置、指标、权益曲线（压缩）、成交、委托、日志 |

仓储在 `quant/storage/repositories.py`；业务代码不直接写 SQL。
SQLite 默认 `data/quant.db`；PostgreSQL 用 `QUANT_DATABASE_URL` 切换，无需改代码。

## 9.1 多用户与权限

- **角色**：`admin` 与 `user` 两种。管理员独享用户管理、审计日志与凭据明文查看。
- **用户管理**：界面「设置 → 用户管理」创建/停用账号、切换角色、重置密码；
  等价 API 为 `GET/POST /api/admin/users`、`PATCH /api/admin/users/{id}`、
  `POST /api/admin/users/{id}/reset-password`，全部写审计。
- **数据隔离**：回测（`GET /api/backtests?mine=true`，详情/取消/删除校验归属）与纸盘记录
  按 `Run.user_id` 隔离；加密凭据按 `owner_id` 隔离；管理员可跨用户查看回测与凭据。
- **会话控制**：停用用户或重置密码会吊销其全部 refresh 令牌；用户可查看并吊销自己的会话。
- **纸盘与实时事件**：`PaperManager` 为每个用户按 `run_id` 维护独立的 `PaperRunner`，同一账号可多开（上限 5 个），
  状态与历史只对自己可见；回测与纸盘的 WebSocket 事件在 `EventHub` 中按 `user_id` 定向投递，
  不会广播给其他登录用户。并发回测总量由 `QUANT_MAX_CONCURRENT_BACKTESTS` 统一限制。

## 10. API 与实时通道

- REST 前缀 `/api`：认证、行情、策略、回测、纸盘、风控、凭据、系统、管理（见 README）。
- 统一错误体：`{code, message, detail, request_id}`，由 `QuantError` 异常层产出；
  生产环境未处理异常不回显堆栈。
- WebSocket `/ws?token=<access_token>`：先用 JWT 校验再 `accept`，随后推送
  `bar/order/fill/equity/status/log` 事件；**事件按用户定向投递**——回测/纸盘事件只发给
  发起者的连接（`user_id` 不匹配的订阅者收不到），公共事件仍广播。
  `EventHub` 有 200 条环形缓冲，新连接可回放最近事件（回放同样按用户过滤），断线重连不丢上下文。
- 引擎 `EventBus`（同步）由 `bus_bridge` 转发到 `EventHub`（asyncio），
  跨线程通过 `loop.call_soon_threadsafe` 投递。

## 11. 前端

```
src/
├── api/client.ts      # fetch 封装、401 静默刷新、全部端点包装
├── api/types.ts       # 与后端 schema 对齐的 TS 类型
├── store/             # zustand：auth（会话）/ events（WS 事件）/ toast
├── hooks/useWebSocket.ts
├── components/        # Layout、ui 基础件、ECharts 封装、Toasts、icons、Ranking（推荐/榜单）
├── pages/             # Login Dashboard Market Instrument Strategies Backtest Paper Live Funds Risk Settings
└── styles/global.css  # 深色主题、响应式
```

- **可拖拽卡片布局（`CardBoard`）**：行情页与仪表盘把区块注册成卡片项
  （`{id, title, wide, render}`）；编辑模式下拖动 ⠿ 排序、切换宽/窄、隐藏/显示，
  配置（顺序/宽窄/可见性）存 localStorage（`quant.layout.market` / `quant.layout.dashboard`），
  新增卡片自动追加。桌面端用 HTML5 拖放，窄屏自动单列。
- **实时快照的前端容错**：自选表合并新旧快照（缺失标的保留上次价格并标 `stale`），
  并把最近一次快照写入 localStorage，刷新页面时先渲染缓存再拉新数据，避免整表短暂变成 "—"。
- **标的详情页（`/market/:code`）**：行情页点击自选/榜单/推荐里的代码或名称进入。
  展示 K 线（1/3/5 年）、现价与涨跌、交易规则（板块/涨跌停/整手/交收/报价精度，来自
  `GET /api/market/instrument`），并可直接选策略、调参数、指定风控预设后
  **一键启动模拟盘**（回放/实时模拟）；实盘页签支持通道/凭据/测试网选择，
  关闭测试网需二次确认。虚拟货币的 `/` 在路径中用 `~` 转义（`CRYPTO:BTC~USDT`）。

- **会话策略**：access token 只在内存；refresh token 在 `localStorage`，
  启动时一次性换取新 access。任何 `401` 触发一次静默刷新并重放原请求；失败则回登录页。
- **行情页**：顶部是实时自选（`GET /api/market/quote` 多源快照，3/5/10/30 秒自动刷新可暂停，
  页面隐藏时不轮询；自选存 `localStorage` 并自动迁移为规范 id，搜索走 `/api/market/search`
  防抖联想，可按 全部/A股/美股/港股 过滤，联想项与自选表带市场徽标，价格按币种显示 ¥/$/HK$，
  港币 3 位小数；成交量统一以股展示），
  下方是 K 线研究与本地缓存管理（缓存表同样带市场列）。
- 图表统一用 ECharts（K 线、权益曲线 + 基准、回撤、月度收益热力、持仓暴露）。
- 回测页同时使用轮询（结果）与 WS（进度/日志），断线自动降级为纯轮询。
- **策略库页**：上半部是内置策略目录（含参数表、「回测」直达入口）；
  下半部「我的拼装策略」——`StrategyBuilder` 条件拼装器（入场/离场规则、仓位/止损/止盈）
  + 「✦ 蓝图编辑器」入口（`/strategies/blueprint`，React Flow 画布，见 §6.2）
  + 已保存列表（v1 行显示两边条件文本；v2 行显示「蓝图 · N 节点」徽标与「编辑蓝图」按钮；
    启停、删除、「回测」带 `?strategy=custom_rule&custom=<id>` 跳转）。
  回测/纸盘页选 `custom_rule` 时渲染 `CustomStrategySelect` 拉取本人拼装策略
  （v2 条目显示节点数），spec 不在通用参数表单出现（由 `custom_strategy_id` 提交）。
- **蓝图编辑器**：`components/blueprint/`——palette 分六组拖拽、节点内联改参数（`nodrag`）、
  连线即数据流（青=数值/橙=布尔）、模板按钮、删除键删选中、保存前本地编译报中文错误清单；
  fitView/snapToGrid/网格吸附为默认体验。

## 12. 扩展点速查

| 需求 | 做法 |
| --- | --- |
| 新策略 | 加一个带 `@register_strategy` 的类（无需改 API/前端）；或让用户在网页上条件拼装（零代码） |
| 新数据源 | 继承 `DataProvider` + `@register_provider("x")`，配置 `QUANT_DATA_PROVIDER=x` |
| 新风控规则 | 继承 `RiskRule`，加入 `RULE_TYPES`，规则配置按 `{id, ...params}` 传入 |
| 新指标 | 在 `strategy/indicators.py` 加纯函数 + `quant.strategy.custom._INDICATORS` 白名单登记（拼装器自动可用）；绩效指标在 `metrics/performance.py` |
| 新引擎（如多因子批量） | 继承 `BaseEngine`，复用 `TradingSession` 与 `MarketDataService` |
| 新部署形态 | 只改 compose 覆盖层 / Dockerfile，不动代码（见 DEPLOY.md） |

## 13. 测试约定

`backend/tests/`：

- `test_crypto.py`：AAD 绑定（换字段解密必须失败）、密文篡改、JWT 类型混淆、TOTP。
- `test_execution.py`：费用口径（含港美预设、双边印花）、撮合阻塞原因、T+1 可卖。
- `test_engine.py`：同输入两次回测结果逐点一致（确定性）；策略读不到未来数据。
- `test_markets.py`：符号解析参数化（三市场 + 非法输入）、市场感知规则/成本、
  EngineConfig 单一市场校验、缓存文件迁移、smartbox 解析、API 422 契约。
- `test_realtime.py`：三市场快照解析 fixture（真实抓包数据）、腾讯端点/后缀选择、
  量纲换算（手→股）、快照降级与 stale 标记。
- `test_paper_manager.py`：多用户纸盘并行/互斥隔离、EventHub 事件按用户定向投递与 replay。
- `test_risk.py`：各规则命中/放行、窗口滑过恢复、熔断只拦买、空规则集语义（`risk=[]` 无风控）。
- `test_custom_strategy.py`：拼装 spec 白名单校验（参数化 17 种非法输入）、
  交叉/比较信号语义、与内置 `dual_ma` 金叉信号的等价性（同一数据交易日一致）、
  引擎端到端确定性、CRUD 用户隔离（404/409）、提交路径的 spec 注入与停用拦截。
- `test_crypto_data.py`：ccxt 假交易所（K 线归一/分页/快照/白名单检索/全周日历）+ 缓存文件名转义。
- `test_t0.py` / `test_tif.py`：T+0 市场当日买入可卖、A 股 T+1 拦截；DAY 单日终过期、GTC 跨日存活。
- `test_funds.py`：申赎聚合（加权成本/赎回盈亏/负持仓保护）、akshare 假源、路由 CRUD 与用户隔离。
- `test_live_broker.py`：累计口径幂等、部分成交增量价、撤单/拒单终态、对账差异、资金校验。
- `test_adapters.py`：ccxt（假模块：sandbox/下单/成交/撤单/余额）与 miniQMT（假 xtquant：代码映射/状态码）。
- `test_live_router.py`：三重闸门、凭据归属、单会话、启停审计、按用户隔离状态。
- `test_callbacks.py`：`on_order` / `on_fill` 确实被会话调用。
- `conftest.py`：临时 SQLite + 临时数据目录 + `TestClient`，测试间完全隔离。

运行：`cd backend && ../.venv/bin/python -m pytest -q`。

## 14. 已知限制

- 撮合是 bar 级近似：不模拟盘口排队、部分成交的价格优先级、集合竞价细节。
- 美股 / 港股交易日历是"工作日近似"：法定假日（感恩节、复活节等）会被多算成交易日，
  开盘时段用固定时区偏移近似（未精确处理美股夏令时切换边界）。
- `live` 纸盘依赖数据源的快照频率；数据源不可用时回退最新收盘价并在事件流中告警。
- 单个运行只允许单一市场（不同币种无法统一记账）；跨市场组合需要分别运行。
- **虚拟货币**：只支持现货；ccxt 交易所差异（最小下单量、费率档位）以交易所返回为准；
  国内网络通常需要代理（`QUANT_CRYPTO_PROXY`）。
- **场外基金**：不做事件驱动回测（净值确认制，不是连续竞价）；申赎费率按记录时填写的费率，
  不自动查基金公告；净值源依赖 akshare 的公开接口。
- **miniQMT 实盘**：只能在 Windows + QMT 终端环境运行，本仓库的自动化测试用假 xtquant 覆盖接口；
  QMT 不返回逐笔费用，盈亏以券商日终账单为准。
- **ccxt 实盘**：测试网与真实环境的最小下单量/精度可能不同；建议先用极小金额验证。
- 单进程内存态：WS 事件、纸盘与实盘会话都不跨副本；后端多实例部署需要粘性会话或事件总线外置。
  实盘在多副本下同一账号可能各开一个会话——生产请用单实例或外部锁。
- 审计哈希链防"悄悄改账"，但拥有数据库写权限者可整链重算——它是证据链，不是权限隔离。


## 新闻/政策因子与统一退出

- `quant/news/`：`NewsProvider`（当前为手工录入）→ `news_items` → `compute_factor`
  → `NewsFactor`（PIT 聚合）；`NewsFactorProvider` 由引擎创建，按当前 bar 时间计算，
  实盘每 60 秒刷新一次。
- `StrategyContext.news_factor(symbol, window)` 是策略唯一入口；没有新闻时返回中性因子，
  不会因为新闻源故障阻断交易。
- `news_dual_ma` 策略演示：金叉 + 情绪/政策风险门槛入场；止损、止盈、政策风险、
  负面情绪、死叉五类退出规则统一走 `apply_exit`。
- `quant/strategy/exits.py`：`ExitReason` / `ExitDecision` / `pick_exit`；订单 `tag`
  记录退出原因，便于审计与归因。
- 回测 PIT 规则：只使用 `effective_at <= bar_time` 的条目；手工录入时“生效时间”
  默认等于发布时间，历史补录必须显式填写，否则会被视为当前才可见。
