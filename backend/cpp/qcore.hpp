// Quant Terminal C++ 加速核心：回测热路径（账本/撮合/指标/策略/主循环）。
// 数值语义必须与 backend/quant/ 下同名 Python 实现逐点一致（等价性测试把门）。
#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <deque>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace qcore {

using ClockNs = int64_t;  // epoch 纳秒（naive UTC）

constexpr double NAN_ = std::numeric_limits<double>::quiet_NaN();
inline bool is_nan(double v) { return std::isnan(v); }

// ------------------------------------------------------------------ //
// 市场规则（镜像 core.markets.board_of / rules_for / price_limits）
// ------------------------------------------------------------------ //
struct SymbolRules {
  int lot_size = 1;
  bool t_plus_1 = false;
  double limit_pct = 0.0;
  int price_digits = 2;
};

SymbolRules rules_for(const std::string& symbol);

// Decimal(str(v)).quantize(half_up, digits) 的等价实现
double round_price_half_up(double v, int digits);
// Python _round(value, digits) = float(f"{value:.{digits}f}")
double py_round(double v, int digits);
std::string iso_from_ns(ClockNs ns);

// ------------------------------------------------------------------ //
// 费用
// ------------------------------------------------------------------ //
struct FeeBreakdown {
  double commission = 0.0, stamp_tax = 0.0, transfer_fee = 0.0, slippage = 0.0;
  double total() const { return commission + stamp_tax + transfer_fee + slippage; }
};

struct CostModel {
  double commission_rate = 3e-4;
  double min_commission = 5.0;
  double stamp_tax_rate = 5e-4;
  double transfer_fee_rate = 1e-5;
  double slippage_bps = 0.0;
  bool stamp_both_sides = false;

  double slippage_price(bool buy, double price) const;
  FeeBreakdown fees(bool buy, double price, double qty, double gross) const;
  double estimate_buy_cost(double price, double qty) const;
};

CostModel cost_from_dict(const std::map<std::string, double>& d, bool stamp_both_sides);

// ------------------------------------------------------------------ //
// 订单 / 成交
// ------------------------------------------------------------------ //
struct Order {
  std::string id, symbol, strategy, tag, reason;
  bool buy = true;
  bool is_limit = false;
  double quantity = 0.0, limit_price = 0.0;
  bool tif_day = true;  // DAY else GTC
  std::string status = "pending";  // pending/submitted/partial/filled/cancelled/rejected/expired
  double filled_quantity = 0.0, avg_fill_price = 0.0;
  FeeBreakdown fees;
  ClockNs created_ns = 0, submitted_ns = 0, updated_ns = 0;
  double frozen_cash = 0.0;
  bool has_frozen_qty = false;
  std::string last_block, last_block_detail;

  bool is_open() const;
  bool is_final() const;
  double remaining() const;
  void apply_fill(double qty, double price, const FeeBreakdown& f);
  void reject(const std::string& r, ClockNs now);
  void cancel_order(ClockNs now);
  void expire_order(const std::string& r, ClockNs now);
};

struct Fill {
  std::string id, order_id, symbol;
  bool buy = true;
  double quantity = 0.0, price = 0.0;
  ClockNs dt_ns = 0;
  FeeBreakdown fees;
  double realized_pnl = 0.0;
  std::string strategy, tag;
};

// ------------------------------------------------------------------ //
// 账户账本（镜像 core.portfolio.Account）
// ------------------------------------------------------------------ //
struct Position {
  double quantity = 0.0, available = 0.0, avg_cost = 0.0, last_price = 0.0, realized_pnl = 0.0;
  ClockNs opened_ns = 0;
  bool flat() const { return quantity == 0.0; }
  double market_value() const { return (last_price != 0.0 ? last_price : avg_cost) * quantity; }
  double cost_value() const { return avg_cost * quantity; }
  double unrealized() const { return flat() ? 0.0 : market_value() - cost_value(); }
};

class Account {
 public:
  double initial_cash = 0.0, cash = 0.0, frozen_cash = 0.0;
  double realized_pnl = 0.0, total_fees = 0.0, turnover = 0.0;
  int trade_count = 0;
  std::map<std::string, Position> positions;
  std::unordered_map<std::string, double> last_prices;

  explicit Account(double initial);
  double available_cash() const { return cash - frozen_cash; }
  double market_value() const;
  double equity() const { return cash + market_value(); }
  double unrealized_pnl() const;
  Position& get_position(const std::string& symbol);
  double last_price_of(const std::string& symbol) const;
  void mark_price(const std::string& symbol, double price);
  void freeze(double amount);
  void unfreeze(double amount);
  double realized_pnl_for(const std::string& symbol, bool buy, double price, double qty, double fees) const;
  void apply_fill(const Fill& fill);
  void on_new_session();
  int holding_count() const;
};

// ------------------------------------------------------------------ //
// 撮合（镜像 execution.matching.MatchingEngine）
// ------------------------------------------------------------------ //
struct MatchOutcome {
  bool filled = false;
  double price = 0.0, qty = 0.0;
  std::string reason, detail;  // 非空 = blocked
};

struct BarView {  // 某只标在时间轴 i 上的行情视图
  const std::string* symbol;
  ClockNs dt_ns;
  double open, high, low, close, volume, amount, prev_close, limit_up, limit_down;
};

class Matcher {
 public:
  CostModel cost;
  double volume_limit_pct = 0.10;

  MatchOutcome match(const Order& order, const BarView& bar) const;
  FeeBreakdown fees(const Order& order, double price, double qty, double base_price) const;
};

// ------------------------------------------------------------------ //
// 券商（镜像 execution.broker.SimBroker，match_on_submit=False 路径）
// ------------------------------------------------------------------ //
class Broker {
 public:
  Account& acct;
  Matcher matcher;
  bool match_on_submit = false;
  std::map<std::string, Order> orders;
  std::vector<std::string> queued, live;  // order ids（保持插入序）
  std::vector<Fill> fills;

  Broker(Account& a, const CostModel& cost, double vol_pct, ClockNs t0);
  Order& record(Order o);
  void submit(Order o);           // 不含风控（风控在引擎层）
  bool cancel(const std::string& id, ClockNs now);
  void on_bar(const BarView& bar);
  void end_of_day(ClockNs now);
  size_t open_order_count() const;
  bool has_open_order(const std::string& symbol) const;
  std::vector<Fill> take_fills();

 private:
  ClockNs now_ns_ = 0;
  int id_seq_ = 0;
  std::string next_id(const char* prefix);
  std::optional<std::string> validate(const Order& o);
  double estimate_cash(const Order& o) const;
  double sellable(const std::string& symbol, const std::string& exclude_id) const;
  void release_reservation(Order& o);
  void drop(const std::string& id);
  bool try_match(Order& o, const BarView& bar);
};

// ------------------------------------------------------------------ //
// 风控（镜像 risk.base 8 条规则与 RiskManager 状态）
// ------------------------------------------------------------------ //
struct RiskRuleCfg {
  std::string id;
  bool enabled = true;
  double p_limit = 0.0;
  int p_max_orders = 0;
  double p_window = 0.0;
  std::vector<std::string> p_symbols;
};

class RiskManager {
 public:
  std::vector<RiskRuleCfg> rules;
  double peak_equity = 0.0, day_start_equity = 0.0;
  int64_t today_day = -1;
  std::deque<double> rate_ts;

  // 返回空串 = 放行；否则返回 "[风控:{rule}] {reason}"
  std::string pre_trade(const Order& o, Account& acct, ClockNs now_ns, size_t open_orders,
                        int holding_count);
};

// ------------------------------------------------------------------ //
// 增量指标（与 pandas 版本数值对齐）
// ------------------------------------------------------------------ //
struct EmaState {
  double a = 0.0;
  int min_periods = 1;
  double y = NAN_;
  long count = 0;
  explicit EmaState(double alpha, int mp) : a(alpha), min_periods(mp) {}
  void update(double x);
  double value() const { return count >= min_periods ? y : NAN_; }
};

// 单只标的、单个指标实例的滚动状态
struct IndState {
  enum class Kind { SMA, EMA, STD, RSI, ATR, ROLL_HIGH, ROLL_LOW, MOMENTUM, BOLL_L, BOLL_M, BOLL_U, MACD_DIF, MACD_DEA, MACD_HIST };
  Kind kind;
  int period = 0, fast = 0, slow = 0, signal = 0;
  double width = 2.0;
  std::deque<double> window;      // 滚动窗口（parent 值）
  std::deque<double> close_win;   // ATR 用
  std::deque<double> high_win, low_win;
  EmaState ema{0.0, 1};           // EMA / RSI gain / RSI loss / ATR / MACD 子状态
  EmaState ema2{0.0, 1}, ema3{0.0, 1};
  double prev_in = NAN_;          // 上一次 parent 值（cross 用）
  double last_out = NAN_;

  explicit IndState(Kind k);
  void update(double x, double high, double low, double close_prev, double close_cur);
  double value() const { return last_out; }
};

}  // namespace qcore
