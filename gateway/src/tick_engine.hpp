// Tick 引擎：tick → bar 聚合 + 纯 tick 策略 + 风控前置。
// 订单意图（OrderIntent）交给上层（paper 日志 / WS 下单）。
#pragma once

#include <deque>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "qcore.hpp"  // qcore::Account / RiskManager（复用 backend/cpp）

namespace gw {

using json = nlohmann::json;
using qcore::Account;
using qcore::RiskManager;

struct Tick {
  std::string symbol;  // 交易所符号，如 BTC-USDT
  double last = 0.0;
  double bid = 0.0, ask = 0.0;
  int64_t ts_ns = 0;
};

struct OrderIntent {
  std::string symbol;
  std::string side;   // buy / sell
  double qty = 0.0;
  std::string tag;    // 策略标签（审计用）
  std::string reason; // 触发原因（审计用）
};

// ------------------------------------------------------------------ //
// 策略接口：每个 tick 都会被调用（纯 tick 策略）
// ------------------------------------------------------------------ //
struct StrategyContext {
  double position_qty = 0.0;   // 本地镜像持仓
  double avg_cost = 0.0;
  double cash = 0.0;
};

class TickStrategy {
 public:
  explicit TickStrategy(const json& params) : params_(params) {}
  virtual ~TickStrategy() = default;

  // 每 tick 调用；返回订单意图（可为空）
  virtual std::vector<OrderIntent> on_tick(const Tick& tick, const StrategyContext& ctx) = 0;

  // bar 收盘时调用（可选实现）
  virtual std::vector<OrderIntent> on_bar_close(const std::string& symbol, double open, double high,
                                                double low, double close, int64_t ts_ns,
                                                const StrategyContext& ctx) {
    (void)symbol; (void)open; (void)high; (void)low; (void)close; (void)ts_ns; (void)ctx;
    return {};
  }

  virtual const char* name() const = 0;

 protected:
  json params_;
};

// ------------------------------------------------------------------ //
// 三个内置 tick 策略
// ------------------------------------------------------------------ //
class GridStrategy final : public TickStrategy {
 public:
  explicit GridStrategy(const json& params);
  std::vector<OrderIntent> on_tick(const Tick& tick, const StrategyContext& ctx) override;
  const char* name() const override { return "grid"; }

 private:
  double step_pct_ = 0.01;
  double order_qty_ = 0.0;
  double upper_ = 0.0, lower_ = 0.0;
  double max_inventory_ = 0.0;
  std::map<std::string, double> last_level_;  // symbol → 上次所处格位
  std::map<std::string, double> anchor_;      // symbol → 中心价（首 tick 定锚）
};

class BreakoutStrategy final : public TickStrategy {
 public:
  explicit BreakoutStrategy(const json& params);
  std::vector<OrderIntent> on_tick(const Tick& tick, const StrategyContext& ctx) override;
  const char* name() const override { return "breakout"; }

 private:
  double upper_ = 0.0, lower_ = 0.0;
  double qty_ = 0.0;
  double cooldown_s_ = 60.0;
  bool buy_armed_ = true, sell_armed_ = true;
  int64_t last_fire_ns_ = 0;
};

class TickMomentumStrategy final : public TickStrategy {
 public:
  explicit TickMomentumStrategy(const json& params);
  std::vector<OrderIntent> on_tick(const Tick& tick, const StrategyContext& ctx) override;
  const char* name() const override { return "tick_momentum"; }

 private:
  struct Sample { int64_t ts_ns; double price; };
  double window_s_ = 30.0;
  double entry_threshold_ = 0.003;
  double exit_threshold_ = 0.0015;
  double exit_drawdown_ = 0.01;
  double qty_ = 0.0;
  double cooldown_s_ = 30.0;
  std::deque<Sample> window_;
  int64_t last_fire_ns_ = 0;
};

std::unique_ptr<TickStrategy> make_strategy(const json& cfg);

// ------------------------------------------------------------------ //
// TickEngine：行情聚合 + 策略 + 风控 + 账本镜像
// ------------------------------------------------------------------ //
class TickEngine {
 public:
  TickEngine(const json& config, Account& account, RiskManager& risk);

  // 每 tick 入口；返回过滤后的订单意图
  std::vector<OrderIntent> on_tick(const Tick& tick);

  // 成交回报：更新账本镜像（由上层在交易所确认后调用）
  void apply_fill(const std::string& symbol, bool buy, double qty, double price, int64_t ts_ns);

  const Account& account() const { return account_; }

 private:
  struct SymbolState {
    int64_t cur_bar_start_ns = 0;
    double open = 0, high = 0, low = 0, close = 0, volume = 0;
    bool bar_active = false;
  };

  std::vector<std::string> symbols_;
  std::map<std::string, SymbolState> state_;
  std::map<std::string, std::unique_ptr<TickStrategy>> strategies_;
  int64_t bar_sec_ = 60;
  Account& account_;
  RiskManager& risk_;
  std::string mirror_symbol(const std::string& ex_symbol) const;  // BTC-USDT → CRYPTO:BTC/USDT
  int64_t bar_start_of(int64_t ts_ns) const;
  int acct_holding_count();
};

}  // namespace gw
