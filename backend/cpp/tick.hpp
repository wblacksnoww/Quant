// tick 级回测：数据结构与引擎骨架。
//
// 设计要点（与 bar 级的关键区别）
// ---------------------------------
// 1. **整段下沉**：tick 循环、指标推进、信号求值、撮合全部在 C++ 内完成，
//    不逐 tick 回调 Python。每 tick 一次 pybind 往返（约 1~2 微秒）会彻底吃掉
//    C++ 的速度优势 —— 一天 4800 个 tick、十年 1200 万次调用，光跨语言开销
//    就比 Python 纯循环还慢。
//
// 2. **策略以 spec 表达**：tick 级不做"自定义 C++ 策略类"，而是复用
//    CompiledSpec / SpecState。策略在 bar 上定义、在 tick 上重采样求值，
//    保证 bar 回测与 tick 回测跑的是同一份逻辑。
//
// 3. **数据源可替换**：本文件只认 TickSeries（列式数组），不关心 tick 从哪来。
//    当前由分钟线插值生成（SyntheticTickProvider），将来接 Level2 真实
//    逐笔时只需换 provider，引擎代码不动。
//
// 4. **成交模型区别于 bar 级**：bar 级是"次日开盘价成交"，tick 级是
//    "订单簿到达即撮合"。这里实现一个最小可用的限价撮合：买单在
//    price <= 限价时成交，卖单在 price >= 限价时成交，成交量受 tick 量约束。

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "strategy.hpp"

namespace qcore {

// 单个 tick。价格已是最新成交价；bid/ask 缺失时用 price 填充。
struct Tick {
  int64_t ts_ns = 0;   // 时间戳（ns since epoch）
  double price = 0.0;  // 最新成交价
  double bid = 0.0;    // 买一价（0 表示无）
  double ask = 0.0;    // 卖一价（0 表示无）
  double volume = 0.0; // 本 tick 成交量
  double amount = 0.0; // 本 tick 成交额
};

// 单只标的的 tick 序列（列式存储：连续内存，缓存友好）。
struct TickSeries {
  std::vector<int64_t> ts_ns;
  std::vector<double> price;
  std::vector<double> bid;
  std::vector<double> ask;
  std::vector<double> volume;
  std::vector<double> amount;

  size_t size() const { return ts_ns.size(); }
  bool empty() const { return ts_ns.empty(); }

  // 按时间戳二分查找下标（返回 <= ts 的最后一个下标；无则 0）。
  size_t index_at_or_before(int64_t ts) const;

  // 从 [begin, end) 区间聚合出一根 bar（用于把 tick 重采样成策略能看的窗口）。
  // 返回的 Columns 指针直接指向本对象内部缓冲区，调用方不得长期持有。
  void append_as_bar(size_t begin, size_t end, std::vector<double>& out_open,
                     std::vector<double>& out_high, std::vector<double>& out_low,
                     std::vector<double>& out_close, std::vector<double>& out_volume) const;
};

// tick 级撮合结果。
struct TickFill {
  int64_t ts_ns = 0;
  double price = 0.0;
  double quantity = 0.0;
  double fee = 0.0;
  bool is_buy = true;
};

// tick 级账户（简化版：只跟踪现金、持仓、均价；T+1 与整手由引擎负责）。
struct TickAccount {
  double initial_cash = 0.0;
  double cash = 0.0;
  double position = 0.0;      // 持仓股数
  double available = 0.0;     // 可卖股数（T+1：买入当日不可卖）
  double avg_cost = 0.0;
  double last_price = 0.0;
  double realized_pnl = 0.0;
  double total_fees = 0.0;
  int64_t last_buy_day = -1;  // 最近一次买入的"日"编号，用于 T+1

  explicit TickAccount(double c) : initial_cash(c), cash(c) {}

  double equity() const { return cash + position * last_price; }

  void mark(double p) { last_price = p; }

  // 日切：把昨日买入转为可卖。
  void on_new_session(int64_t day) {
    if (last_buy_day >= 0 && day > last_buy_day) available = position;
  }

  void apply_buy(double qty, double price, double fee, int64_t day);
  void apply_sell(double qty, double price, double fee);
};

// 单只标的的 tick 回测结果。
struct TickSeriesResult {
  std::vector<TickFill> fills;
  std::vector<int64_t> snap_ts;
  std::vector<double> snap_equity;
  std::vector<double> snap_cash;
  std::vector<double> snap_position;
  std::vector<double> snap_price;
  size_t ticks_processed = 0;
  size_t ticks_skipped_warmup = 0;
};

// tick 回测配置。
struct TickConfig {
  double initial_cash = 100000.0;
  double fee_rate = 0.0003;      // 佣金费率（双边）
  double stamp_tax = 0.001;      // 印花税（卖出单边）
  double min_fee = 5.0;          // 最低佣金
  double lot = 100.0;            // 整手
  double volume_limit_pct = 0.10; // 单 tick 最多吃掉多少成交量
  int bars_for_signal = 0;       // 策略求值需要多少根"重采样 bar"（0 = 用 tick 直接求值）
  int64_t bar_width_ns = 0;      // 重采样 bar 宽度（0 = 自动取 1 分钟）
  double position_pct = 0.95;    // 目标仓位
  double stop_loss = 0.0;
  double take_profit = 0.0;
  double slippage_bps = 0.0;
};

// 对一个标的跑 tick 回测。策略以 spec 表达，复用 bar 级的 CompiledSpec。
//
// 返回该标的的成交与权益快照；跨标的的合并由调用方（绑定层）负责。
TickSeriesResult run_tick_backtest_one(const TickSeries& series, const CompiledSpec& spec,
                                        const TickConfig& cfg);

// 多标的：把各自的 tick 按时间戳归并后统一推进（组合层资金共享）。
struct TickPortfolioResult {
  std::vector<std::pair<std::string, TickSeriesResult>> per_symbol;
  std::vector<int64_t> snap_ts;
  std::vector<double> snap_equity;
};

TickPortfolioResult run_tick_backtest(const std::vector<std::string>& symbols,
                                       const std::vector<TickSeries>& series,
                                       const CompiledSpec& spec, const TickConfig& cfg);

}  // namespace qcore
