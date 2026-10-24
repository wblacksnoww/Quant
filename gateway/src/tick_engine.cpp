#include "tick_engine.hpp"
#include "risk.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace gw {

using qcore::Account;
using qcore::BarView;
using qcore::Columns;
using qcore::Position;
using qcore::Order;
using qcore::RiskManager;
using qcore::compile_spec;

// ------------------------------------------------------------------ //
// 网格
// ------------------------------------------------------------------ //
GridStrategy::GridStrategy(const json& params) : TickStrategy(params) {
  step_pct_ = params.value("step_pct", 0.01);
  order_qty_ = params.value("order_qty", 0.0);
  upper_ = params.value("upper", 0.0);
  lower_ = params.value("lower", 0.0);
  max_inventory_ = params.value("max_inventory", 0.0);
  if (step_pct_ <= 0 || step_pct_ >= 0.5)
    throw std::runtime_error("grid.step_pct 需在 (0, 0.5) 之间");
  if (order_qty_ <= 0) throw std::runtime_error("grid.order_qty 必须大于 0");
}

// 格位 = floor(log(price / center) / log(1 + step))；center 在首个 tick 定锚。
// 价格上穿一格 → 卖出一份；下穿一格 → 买入一份（受区间与库存上限约束）。
std::vector<OrderIntent> GridStrategy::on_tick(const Tick& tick, const StrategyContext& ctx) {
  if (tick.last <= 0) return {};
  if (upper_ > 0 && tick.last > upper_) return {};
  if (lower_ > 0 && tick.last < lower_) return {};

  auto it = last_level_.find(tick.symbol);
  if (it == last_level_.end()) {
    last_level_[tick.symbol] = 0;  // 定锚：首个 tick 不交易
    anchor_[tick.symbol] = tick.last;
    return {};
  }
  int64_t prev_level = it->second;

  // 以锚定价（首 tick 价格）为几何中心
  double center = anchor_.count(tick.symbol) ? anchor_[tick.symbol] : tick.last;
  if (!anchor_.count(tick.symbol)) anchor_[tick.symbol] = tick.last;

  double level_f = std::log(tick.last / center) / std::log(1.0 + step_pct_);
  int64_t level = static_cast<int64_t>(std::floor(level_f));
  last_level_[tick.symbol] = level;
  if (level == prev_level) return {};

  std::vector<OrderIntent> intents;
  if (level > prev_level) {
    // 上穿：卖出（需要库存）
    if (ctx.position_qty >= order_qty_) {
      intents.push_back({tick.symbol, "sell", order_qty_, "grid_up",
                         "grid " + std::to_string(prev_level) + "->" + std::to_string(level)});
    }
  } else {
    // 下穿：买入（受库存上限）
    if (max_inventory_ <= 0 || ctx.position_qty + order_qty_ <= max_inventory_) {
      intents.push_back({tick.symbol, "buy", order_qty_, "grid_down",
                         "grid " + std::to_string(prev_level) + "->" + std::to_string(level)});
    }
  }
  return intents;
}

// ------------------------------------------------------------------ //
// 突破触发器
// ------------------------------------------------------------------ //
BreakoutStrategy::BreakoutStrategy(const json& params) : TickStrategy(params) {
  upper_ = params.value("upper", 0.0);
  lower_ = params.value("lower", 0.0);
  qty_ = params.value("order_qty", 0.0);
  cooldown_s_ = params.value("cooldown_s", 60.0);
  if (qty_ <= 0) throw std::runtime_error("breakout.order_qty 必须大于 0");
}

// 状态机：触发后解除武装，价格回到阈值内侧才重新武装；冷却期防反复。
std::vector<OrderIntent> BreakoutStrategy::on_tick(const Tick& tick, const StrategyContext& ctx) {
  if (tick.last <= 0) return {};
  std::vector<OrderIntent> intents;
  double now_s = static_cast<double>(tick.ts_ns) / 1e9;
  if (now_s - static_cast<double>(last_fire_ns_) / 1e9 < cooldown_s_) return intents;

  if (upper_ > 0 && buy_armed_ && tick.last >= upper_) {
    intents.push_back({tick.symbol, "buy", qty_, "breakout_up",
                       "price " + std::to_string(tick.last) + " >= " + std::to_string(upper_)});
    buy_armed_ = false;
    last_fire_ns_ = tick.ts_ns;
  } else if (upper_ > 0 && !buy_armed_ && tick.last < upper_) {
    buy_armed_ = true;  // 回到内侧重新武装
  }

  if (lower_ > 0 && sell_armed_ && tick.last <= lower_) {
    if (ctx.position_qty > 0) {
      intents.push_back({tick.symbol, "sell", ctx.position_qty, "breakout_down",
                         "price " + std::to_string(tick.last) + " <= " + std::to_string(lower_)});
    }
    sell_armed_ = false;
    last_fire_ns_ = tick.ts_ns;
  } else if (lower_ > 0 && !sell_armed_ && tick.last > lower_) {
    sell_armed_ = true;
  }
  return intents;
}

// ------------------------------------------------------------------ //
// tick 动量
// ------------------------------------------------------------------ //
TickMomentumStrategy::TickMomentumStrategy(const json& params) : TickStrategy(params) {
  window_s_ = params.value("window_s", 30.0);
  entry_threshold_ = params.value("entry_threshold", 0.003);
  exit_threshold_ = params.value("exit_threshold", 0.0015);
  exit_drawdown_ = params.value("exit_drawdown", 0.01);
  qty_ = params.value("order_qty", 0.0);
  cooldown_s_ = params.value("cooldown_s", 30.0);
  if (window_s_ <= 0 || qty_ <= 0) throw std::runtime_error("momentum.window_s/order_qty 必须大于 0");
}

// 滚动窗口动量：momentum = last / window_start - 1。
// 顺势开仓；反向或回撤平仓；冷却期限制频率。
std::vector<OrderIntent> TickMomentumStrategy::on_tick(const Tick& tick, const StrategyContext& ctx) {
  if (tick.last <= 0) return {};
  window_.push_back({tick.ts_ns, tick.last});
  while (!window_.empty() &&
         static_cast<double>(tick.ts_ns - window_.front().ts_ns) / 1e9 > window_s_)
    window_.pop_front();

  std::vector<OrderIntent> intents;
  double now_s = static_cast<double>(tick.ts_ns) / 1e9;
  if (now_s - static_cast<double>(last_fire_ns_) / 1e9 < cooldown_s_) return intents;
  if (static_cast<int>(window_.size()) < 2) return intents;

  double base = window_.front().price;
  if (base <= 0) return intents;
  double momentum = tick.last / base - 1.0;

  bool flat = ctx.position_qty <= 0;
  if (flat) {
    if (momentum >= entry_threshold_) {
      intents.push_back({tick.symbol, "buy", qty_, "momentum_up",
                         "momentum " + std::to_string(momentum)});
      last_fire_ns_ = tick.ts_ns;
    }
  } else {
    double pnl = tick.last / ctx.avg_cost - 1.0;
    bool exit = momentum <= -exit_threshold_ || (exit_drawdown_ > 0 && pnl <= -exit_drawdown_);
    if (exit) {
      intents.push_back({tick.symbol, "sell", ctx.position_qty, "momentum_exit",
                         "momentum " + std::to_string(momentum)});
      last_fire_ns_ = tick.ts_ns;
    }
  }
  return intents;
}

std::unique_ptr<TickStrategy> make_strategy(const json& cfg, int64_t bar_sec) {
  std::string type = cfg.value("type", "");
  const json& params = cfg.contains("params") ? cfg["params"] : json::object();
  if (type == "grid") return std::make_unique<GridStrategy>(params);
  if (type == "breakout") return std::make_unique<BreakoutStrategy>(params);
  if (type == "tick_momentum") return std::make_unique<TickMomentumStrategy>(params);
  if (type == "bar_spec") return std::make_unique<BarSpecStrategy>(params, bar_sec);
  throw std::runtime_error("未知 tick 策略类型: " + type);
}

// ------------------------------------------------------------------ //
// 拼装策略（bar 收盘触发）
// ------------------------------------------------------------------ //
BarSpecStrategy::BarSpecStrategy(const json& params, int64_t bar_sec)
    : TickStrategy(params), bar_sec_(std::max<int64_t>(1, bar_sec)) {
  if (!params.contains("spec")) throw std::runtime_error("bar_spec 需要 params.spec（拼装策略 spec v1/v2）");
  order_qty_ = params.value("order_qty", 0.0);
  if (order_qty_ <= 0) throw std::runtime_error("bar_spec.order_qty 必须大于 0");
  spec_ = compile_spec(params.at("spec"));
}

std::vector<OrderIntent> BarSpecStrategy::on_tick(const Tick& tick, const StrategyContext& ctx) {
  if (tick.last <= 0) return {};
  const int64_t bar_ns = bar_sec_ * 1000000000LL;
  const int64_t bucket = tick.ts_ns - (tick.ts_ns % bar_ns);

  auto& hist = history_[tick.symbol];
  auto start_it = cur_start_.find(tick.symbol);
  Bar bar{tick.last, tick.last, tick.last, tick.last};
  bool new_bar = start_it == cur_start_.end() || start_it->second != bucket;
  if (!new_bar) {
    bar = cur_[tick.symbol];
    bar.high = std::max(bar.high, tick.last);
    bar.low = std::min(bar.low, tick.last);
    bar.close = tick.last;
  }
  cur_[tick.symbol] = bar;
  cur_start_[tick.symbol] = bucket;
  if (new_bar) return {};  // 新 bar 的第一个 tick：先开盘，等收盘再评估

  // 尚未收盘（无法预知下一 tick 是否换 bar）——网关模式下以“当前 bar 的实时状态”评估，
  // 与模拟盘 live 模式的语义一致（今日活 bar 每次更新都重新求值，SpecState 幂等保证一致）
  hist.push_back(bar);  // 追加当前（活）bar 作为最后一行
  size_t cursor = hist.size() - 1;

  // 平面化历史 → Columns
  std::vector<double> opens, highs, lows, closes, vols(hist.size(), 0.0), amts(hist.size(), 0.0);
  opens.reserve(hist.size());
  for (const auto& b : hist) {
    opens.push_back(b.open);
    highs.push_back(b.high);
    lows.push_back(b.low);
    closes.push_back(b.close);
  }
  Columns cols;
  cols.n = hist.size();
  cols.open = opens.data();
  cols.high = highs.data();
  cols.low = lows.data();
  cols.close = closes.data();
  cols.volume = vols.data();
  cols.amount = amts.data();

  auto& state = states_[tick.symbol];
  if (!state) state = std::make_unique<SpecState>(spec_);
  auto [entry, exit] = state->step_idempotent(cursor, cols);

  std::vector<OrderIntent> intents;
  bool flat = ctx.position_qty <= 0;
  if (flat && entry) {
    intents.push_back({tick.symbol, "buy", order_qty_, "spec_entry", "bar_spec entry"});
  } else if (!flat && ctx.position_qty > 0 && exit) {
    intents.push_back({tick.symbol, "sell", ctx.position_qty, "spec_exit", "bar_spec exit"});
  }
  return intents;
}

// ------------------------------------------------------------------ //
// TickEngine
// ------------------------------------------------------------------ //
TickEngine::TickEngine(const json& config, Account& account, RiskManager& risk)
    : account_(account), risk_(risk) {
  for (const auto& s : config.at("symbols"))
    symbols_.push_back(s.get<std::string>());
  bar_sec_ = config.value("bar_interval_sec", 60);
  auto strategy = config.at("strategy");
  for (const auto& s : symbols_) {
    strategies_[s] = make_strategy(strategy, bar_sec_);
    state_[s] = SymbolState{};
  }
}

std::string TickEngine::mirror_symbol(const std::string& ex_symbol) const {
  auto slash = ex_symbol.find('-');
  if (slash == std::string::npos) return ex_symbol;
  return "CRYPTO:" + ex_symbol.substr(0, slash) + "/" + ex_symbol.substr(slash + 1);
}

int64_t TickEngine::bar_start_of(int64_t ts_ns) const {
  int64_t bar_ns = bar_sec_ * 1000000000LL;
  return ts_ns - (ts_ns % bar_ns);
}

std::vector<OrderIntent> TickEngine::on_tick(const Tick& tick) {
  if (std::find(symbols_.begin(), symbols_.end(), tick.symbol) == symbols_.end()) return {};

  StrategyContext ctx;
  auto pit = account_.positions.find(mirror_symbol(tick.symbol));
  if (pit != account_.positions.end()) {
    ctx.position_qty = pit->second.quantity;
    ctx.avg_cost = pit->second.avg_cost;
  }
  ctx.cash = account_.available_cash();

  // 1) bar 聚合：整分钟对齐；换 bar 时产出上一根收盘事件由上层处理
  SymbolState& st = state_[tick.symbol];
  int64_t bar_start = bar_start_of(tick.ts_ns);
  std::vector<OrderIntent> intents;
  if (!st.bar_active || st.cur_bar_start_ns != bar_start) {
    st.cur_bar_start_ns = bar_start;
    st.open = st.high = st.low = st.close = tick.last;
    st.volume = 0;
    st.bar_active = true;
  } else {
    st.high = std::max(st.high, tick.last);
    st.low = std::min(st.low, tick.last);
    st.close = tick.last;
  }

  // 2) 每 tick：策略 + 风控
  auto strategy = strategies_.find(tick.symbol);
  if (strategy != strategies_.end()) {
    for (auto& intent : strategy->second->on_tick(tick, ctx)) {
      // 风控前置：构造 qcore::Order 走同一规则链
      Order o;
      o.symbol = mirror_symbol(tick.symbol);
      o.buy = intent.side == "buy";
      o.quantity = intent.qty;
      o.tif_day = false;  // 实盘市价 GTC
      o.created_ns = tick.ts_ns;
      std::string blocked = risk_.pre_trade(o, account_, tick.ts_ns,
                                            0 /* open_orders 由上层填 */, acct_holding_count());
      if (!blocked.empty()) {
        intents.push_back({tick.symbol, "log", 0.0, "risk_blocked", blocked});
        continue;
      }
      intents.push_back(std::move(intent));
    }
  }
  return intents;
}

void TickEngine::apply_fill(const std::string& symbol, bool buy, double qty, double price,
                            int64_t ts_ns) {
  // 简化镜像：直接按成交调整持仓（网关自有账本，不冻结资金）
  Position& pos = account_.get_position(mirror_symbol(symbol));
  double gross = price * qty;
  if (buy) {
    double new_qty = pos.quantity + qty;
    pos.avg_cost = (pos.cost_value() + gross) / new_qty;
    pos.quantity = new_qty;
    if (pos.opened_ns == 0) pos.opened_ns = ts_ns;
  } else {
    pos.realized_pnl += (price - pos.avg_cost) * qty;
    pos.quantity -= qty;
    if (pos.quantity <= 0) {
      pos.quantity = 0;
      pos.avg_cost = 0.0;
    }
  }
  pos.last_price = price;
  pos.available = pos.quantity;
}

int TickEngine::acct_holding_count() {
  int n = 0;
  for (const auto& [sym, pos] : account_.positions) n += pos.flat() ? 0 : 1;
  return n;
}

}  // namespace gw
