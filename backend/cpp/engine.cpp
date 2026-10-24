// 回测主循环：严格镜像 engine.session.TradingSession.step 的时序。
#include "strategy.hpp"
#include <pybind11/functional.h>
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
namespace qcore {

namespace {

struct StrategyParams {
  // dual_ma / rsi / momentum / buy_hold / custom 共用
  int fast = 10, slow = 30, period = 14, lookback = 20, top_k = 1, rebalance_days = 0;
  double position_pct = 0.95, stop_loss = 0.08, take_profit = 0.0;
  double oversold = 30.0, overbought = 70.0, min_momentum = -1.0;
  CompiledSpec spec;
};

// 内置策略 / 拼装策略共用的下单辅助（镜像 strategy.base 的 _size 与 order_target_percent）
struct Engine {
  std::vector<std::string> symbols;
  std::vector<Columns> cols;                       // 每标的列
  std::vector<SymbolRules> rules;
  std::unordered_map<std::string, size_t> symbol_idx;
  Account acct;
  Broker broker;
  RiskManager risk;
  StrategyKind kind;
  StrategyParams p;
  std::string strategy_name;
  std::vector<std::string> logs;
  // 内置策略状态（每标的）
  std::unordered_map<std::string, Indicator> dual_fast, dual_slow, rsi_state;
  std::optional<int> last_rebalance_day;
  // 拼装策略状态（每标的）
  std::vector<std::optional<SpecState>> spec_states;

  Engine(const std::vector<std::string>& syms, double cash, const CostModel& cost, double vol_pct,
         StrategyKind k)
      : symbols(syms), acct(cash), broker(acct, cost, vol_pct, 0), kind(k) {
    for (size_t i = 0; i < syms.size(); ++i) symbol_idx[syms[i]] = i;
    rules.resize(syms.size());
    for (auto& r : rules) r = SymbolRules{};  // 占位，引擎稍后填
  }

  size_t idx_of(const std::string& s) const { return symbol_idx.at(s); }
  double price_at(size_t sym, size_t cursor) const { return cols[sym].close[cursor]; }

  // ctx.price：当前 bar 收盘价（mark 已在撮合阶段完成）
  double price_now(size_t sym, size_t cursor) const {
    if (cursor < cols[sym].n) return cols[sym].close[cursor];
    return acct.last_price_of(symbols[sym]);
  }

  bool has_open_order(size_t sym) const { return broker.has_open_order(symbols[sym]); }

  double round_lot(double qty, size_t sym) const {
    int lot = rules[sym].lot_size;
    if (lot <= 1) return qty;
    return std::floor(qty / lot) * lot;
  }

  double size_percent(size_t sym, bool buy, double percent, double price) const {
    // Python StrategyContext.cash = available_cash（扣除冻结），不是原始现金
    double budget = acct.equity() * std::max(0.0, std::min(1.0, percent));
    if (buy) budget = std::min(budget, acct.available_cash()) / 1.02;
    return round_lot(budget / price, sym);
  }

  void submit_order(size_t sym, bool buy, double qty, bool is_limit, double limit_price,
                    const std::string& tag, ClockNs now) {
    Order o;
    o.symbol = symbols[sym];
    o.buy = buy;
    o.quantity = qty;
    o.is_limit = is_limit;
    o.limit_price = limit_price;
    o.tif_day = true;
    o.strategy = strategy_name;
    o.tag = tag;
    o.created_ns = now;
    // 风控前置（StrategyContext.submit）
    std::string blocked = risk.pre_trade(o, acct, now, broker.open_order_count(), acct.holding_count());
    if (!blocked.empty()) {
      Order& recorded = broker.record(std::move(o));
      recorded.reject(blocked, now);
      return;
    }
    broker.submit(std::move(o));
  }

  void buy_percent(size_t sym, double percent, const std::string& tag, ClockNs now) {
    double price = price_now(sym, cursor_of(sym));
    if (price <= 0) return;
    double qty = size_percent(sym, true, percent, price);
    if (qty <= 0) return;
    submit_order(sym, true, qty, false, 0.0, tag, now);
  }

  void close_pos(size_t sym, const std::string& tag, ClockNs now, double fraction = 1.0) {
    auto it = acct.positions.find(symbols[sym]);
    if (it == acct.positions.end()) return;
    double available = it->second.available;
    if (available <= 0) return;
    double qty = fraction >= 1.0 ? available : round_lot(available * fraction, sym);
    if (qty <= 0) return;
    submit_order(sym, false, qty, false, 0.0, tag, now);
  }

  void order_target_percent(size_t sym, double target, const std::string& tag, ClockNs now) {
    double price = price_now(sym, cursor_of(sym));
    if (price <= 0) return;
    double target_value = acct.equity() * target;
    auto it = acct.positions.find(symbols[sym]);
    double mv = it != acct.positions.end() ? it->second.market_value() : 0.0;
    double diff = target_value - mv;
    if (std::abs(diff) < price * 100) return;  // 与 Python 相同的最小金额门槛
    if (diff > 0) {
      double qty = round_lot(diff / price, sym);
      if (qty > 0) submit_order(sym, true, qty, false, 0.0, tag, now);
    } else {
      double qty = round_lot(std::abs(diff) / price, sym);
      auto pit = acct.positions.find(symbols[sym]);
      double available = pit != acct.positions.end() ? pit->second.available : 0.0;
      qty = std::min(qty, available);
      if (qty > 0) submit_order(sym, false, qty, false, 0.0, tag, now);
    }
  }

  // ---- 内置策略 ---- //
  int cursor_of(size_t sym) const { return static_cast<int>(cursor_[sym]); }
  std::vector<size_t> cursor_;

  void ensure_builtin_states() {
    for (size_t s = 0; s < symbols.size(); ++s) {
      const std::string& sym = symbols[s];
      if (kind == StrategyKind::DualMa) {
        dual_fast.emplace(sym, Indicator(Indicator::Kind::SMA, p.fast));
        dual_slow.emplace(sym, Indicator(Indicator::Kind::SMA, p.slow));
      } else if (kind == StrategyKind::RsiReversion) {
        rsi_state.emplace(sym, Indicator(Indicator::Kind::RSI, p.period));
      }
    }
  }

  void update_builtin_states(size_t sym, size_t i) {
    const Columns& c = cols[sym];
    double close = c.close[i];
    double high = c.high[i], low = c.low[i];
    double close_prev = i > 0 ? c.close[i - 1] : NAN_;
    const std::string& sym_name = symbols[sym];
    if (kind == StrategyKind::DualMa) {
      dual_fast.at(sym_name).update(close, high, low, close, close_prev);
      dual_slow.at(sym_name).update(close, high, low, close, close_prev);
    } else if (kind == StrategyKind::RsiReversion) {
      rsi_state.at(sym_name).update(close, high, low, close, close_prev);
    }
  }

  // 内置策略逻辑（严格对应 Python on_bar/on_bars）
  void run_builtin(const std::vector<size_t>& bar_syms, const std::vector<size_t>& bar_idx, ClockNs now) {
    if (kind == StrategyKind::DualMa) {
      // on_bars 默认逐标的调 on_bar（symbols 顺序）
      for (size_t s : bar_syms) dual_ma_on_bar(s, bar_idx[s], now);
    } else if (kind == StrategyKind::RsiReversion) {
      for (size_t s : bar_syms) rsi_on_bar(s, bar_idx[s], now);
    } else if (kind == StrategyKind::BuyHold) {
      buy_hold_on_bars(bar_syms, bar_idx, now);
    } else if (kind == StrategyKind::MomentumRotation) {
      momentum_on_bars(bar_syms, bar_idx, now);
    }
  }

  void dual_ma_on_bar(size_t sym, size_t i, ClockNs now) {
    const std::string& sym_name = symbols[sym];
    if (cursor_[sym] + 1 < static_cast<size_t>(p.slow) + 2) return;  // len(history) < need
    double fast = dual_fast.at(sym_name).value();
    double slow = dual_slow.at(sym_name).value();
    double fast_prev = dual_fast.at(sym_name).previous();
    double slow_prev = dual_slow.at(sym_name).previous();
    double signal = 0.0;
    if (!is_nan(fast) && !is_nan(slow) && !is_nan(fast_prev) && !is_nan(slow_prev)) {
      double diff = fast - slow, prev = fast_prev - slow_prev;
      if (diff > 0 && prev <= 0) signal = 1.0;
      else if (diff < 0 && prev >= 0) signal = -1.0;
    }
    auto it = acct.positions.find(sym_name);
    bool flat = it == acct.positions.end() || it->second.flat();
    double available = it != acct.positions.end() ? it->second.available : 0.0;
    if (has_open_order(sym)) return;

    // 多个退出规则 → 唯一动作（与 apply_exit 相同的优先级：止损100 / 止盈80 / 信号50）
    int action = 0;  // 0=hold 1=stop 2=tp 3=signal
    double close = cols[sym].close[i];
    if (!flat && available > 0) {
      double cost = it->second.avg_cost;
      if (cost > 0) {
        double pnl = close / cost - 1.0;
        if (p.stop_loss > 0 && pnl <= -p.stop_loss) action = 1;
        if (p.take_profit > 0 && pnl >= p.take_profit && action == 0) action = 2;
      }
    }
    if (signal < 0 && !flat && action == 0) action = 3;
    if (action != 0) {
      close_pos(sym, action == 1 ? "stop_loss" : (action == 2 ? "take_profit" : "signal"), now);
      return;
    }
    if (signal > 0 && flat) {
      buy_percent(sym, p.position_pct, "golden_cross", now);
    }
  }

  void rsi_on_bar(size_t sym, size_t i, ClockNs now) {
    const std::string& sym_name = symbols[sym];
    if (cursor_[sym] + 1 < static_cast<size_t>(p.period) + 5) return;
    double current = rsi_state.at(sym_name).value();
    if (is_nan(current)) return;
    auto it = acct.positions.find(sym_name);
    bool flat = it == acct.positions.end() || it->second.flat();
    double available = it != acct.positions.end() ? it->second.available : 0.0;
    if (has_open_order(sym)) return;
    if (!flat && available > 0) {
      double cost = it->second.avg_cost;
      if (cost > 0 && p.stop_loss > 0 && cols[sym].close[i] / cost - 1.0 <= -p.stop_loss) {
        close_pos(sym, "stop_loss", now);
        return;
      }
    }
    if (current <= p.oversold && flat) {
      buy_percent(sym, p.position_pct, "rsi_oversold", now);
    } else if (current >= p.overbought && !flat) {
      close_pos(sym, "rsi_overbought", now);
    }
  }

  void buy_hold_on_bars(const std::vector<size_t>& bar_syms, const std::vector<size_t>& bar_idx, ClockNs now) {
    if (bar_syms.empty()) return;
    double weight = 1.0 / static_cast<double>(bar_syms.size());
    double max_weight = p.position_pct != 0.0 ? p.position_pct : 1.0;
    for (size_t s : bar_syms) {
      auto it = acct.positions.find(symbols[s]);
      bool flat = it == acct.positions.end() || it->second.flat();
      if (flat && !has_open_order(s)) {
        buy_percent(s, std::min(weight, max_weight) * 0.98, "buy_hold", now);
      }
    }
  }

  void momentum_on_bars(const std::vector<size_t>& bar_syms, const std::vector<size_t>& bar_idx, ClockNs now) {
    for (size_t s : bar_syms)
      if (cursor_[s] + 1 < static_cast<size_t>(p.lookback) + 1) return;
    int day_index = static_cast<int>(cursor_[bar_syms[0]]);
    if (last_rebalance_day.has_value() && day_index - *last_rebalance_day < p.rebalance_days) return;
    last_rebalance_day = day_index;

    std::vector<std::pair<size_t, double>> scores;
    for (size_t s : bar_syms) {
      size_t i = bar_idx[s];
      double momentum = cols[s].close[i] / cols[s].close[i - p.lookback] - 1.0;
      scores.emplace_back(s, momentum);
    }
    std::stable_sort(scores.begin(), scores.end(),
                     [](auto& a, auto& b) { return a.second > b.second; });
    std::vector<size_t> top;
    for (auto& [s, m] : scores) {
      if (m >= p.min_momentum) top.push_back(s);
      if (static_cast<int>(top.size()) >= p.top_k) break;
    }
    double weight = top.empty() ? 0.0 : p.position_pct / static_cast<double>(top.size());
    for (size_t s : bar_syms) {
      bool selected = std::find(top.begin(), top.end(), s) != top.end();
      auto it = acct.positions.find(symbols[s]);
      bool flat = it == acct.positions.end() || it->second.flat();
      if (!selected && !flat && !has_open_order(s)) close_pos(s, "rotation_out", now);
    }
    for (size_t s : top) {
      if (!has_open_order(s)) order_target_percent(s, weight, "rotation_in", now);
    }
  }

  // 拼装策略
  void run_custom(const std::vector<size_t>& bar_syms, const std::vector<size_t>& bar_idx, ClockNs now) {
    for (size_t k = 0; k < bar_syms.size(); ++k) {
      size_t s = bar_syms[k];
      size_t i = bar_idx[k];
      if (!spec_states[s].has_value()) spec_states[s].emplace(p.spec);
      auto [entry, exit] = spec_states[s]->step(i, cols[s]);
      const std::string& sym_name = symbols[s];
      auto it = acct.positions.find(sym_name);
      bool flat = it == acct.positions.end() || it->second.flat();
      double available = it != acct.positions.end() ? it->second.available : 0.0;
      if (has_open_order(s)) continue;

      if (!flat && available > 0 && it->second.avg_cost > 0) {
        double pnl = cols[s].close[i] / it->second.avg_cost - 1.0;
        if (p.stop_loss > 0 && pnl <= -p.stop_loss) {
          close_pos(s, "stop_loss", now);
          continue;
        }
        if (p.take_profit > 0 && pnl >= p.take_profit) {
          close_pos(s, "take_profit", now);
          continue;
        }
      }
      if (flat) {
        if (entry) buy_percent(s, p.spec.position_pct, "entry", now);
      } else if (available > 0) {
        if (exit) close_pos(s, "exit", now);
      }
    }
  }

};

struct Snapshot {
  ClockNs dt_ns;
  double cash, frozen_cash, market_value, total_equity, realized_pnl, unrealized, total_fees;
  int position_count;
};

}  // namespace

py::dict run_backtest_native(
    std::vector<std::string> symbols, std::string bar_interval, int64_t start_ns, int64_t end_ns,
    double initial_cash, double risk_free_rate, std::map<std::string, double> cost_dict,
    bool stamp_both_sides, double volume_limit_pct, py::list risk_rules, std::string strategy_id,
    py::dict params,
    std::vector<py::array_t<int64_t, py::array::c_style | py::array::forcecast>> dates,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> opens,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> highs,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> lows,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> closes,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> volumes,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> amounts,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> prev_closes,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> limit_ups,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> limit_downs,
    std::vector<std::map<std::string, py::array_t<double, py::array::c_style | py::array::forcecast>>> extras,
    py::function progress) {
  StrategyKind kind = strategy_kind(strategy_id);
  CostModel cost = cost_from_dict(cost_dict, stamp_both_sides);

  Engine eng(symbols, initial_cash, cost, volume_limit_pct, kind);
  eng.strategy_name = strategy_id;
  // 参数
  auto getf = [&](const char* k, double def) {
    if (params.contains(k) && !params[k].is_none()) return py::cast<double>(params[k]);
    return def;
  };
  auto geti = [&](const char* k, int def) {
    if (params.contains(k) && !params[k].is_none()) return static_cast<int>(py::cast<double>(params[k]));
    return def;
  };
  // 各策略的 ParamSpec 默认值（与 Python 内置策略一一对应）
  if (kind == StrategyKind::DualMa) {
    eng.p = {10, 30, 14, 20, 1, 5, 0.95, 0.08, 0.0, 30.0, 70.0, -1.0};
  } else if (kind == StrategyKind::RsiReversion) {
    eng.p = {10, 30, 14, 20, 1, 5, 0.90, 0.10, 0.0, 30.0, 70.0, -1.0};
  } else if (kind == StrategyKind::MomentumRotation) {
    eng.p = {10, 30, 14, 20, 1, 5, 0.95, 0.08, 0.0, 30.0, 70.0, -1.0};
  } else if (kind == StrategyKind::BuyHold) {
    eng.p = {10, 30, 14, 20, 1, 0, 1.00, 0.08, 0.0, 30.0, 70.0, -1.0};
  } else {
    eng.p = {10, 30, 14, 20, 1, 5, 0.95, 0.08, 0.0, 30.0, 70.0, -1.0};
  }
  eng.p.fast = geti("fast", eng.p.fast);
  eng.p.slow = geti("slow", eng.p.slow);
  eng.p.period = geti("period", eng.p.period);
  eng.p.lookback = geti("lookback", eng.p.lookback);
  eng.p.top_k = geti("top_k", eng.p.top_k);
  eng.p.rebalance_days = geti("rebalance_days", eng.p.rebalance_days);
  eng.p.position_pct = getf("position_pct", eng.p.position_pct);
  eng.p.stop_loss = getf("stop_loss", eng.p.stop_loss);
  eng.p.take_profit = getf("take_profit", eng.p.take_profit);
  eng.p.oversold = getf("oversold", eng.p.oversold);
  eng.p.overbought = getf("overbought", eng.p.overbought);
  eng.p.min_momentum = getf("min_momentum", eng.p.min_momentum);
  if (params.contains("spec") && !params["spec"].is_none())
    eng.p.spec = compile_spec(py::cast<py::dict>(params["spec"]));
  eng.spec_states.resize(symbols.size());

  // 数据
  eng.cols.resize(symbols.size());
  eng.cursor_.assign(symbols.size(), 0);
  std::vector<std::vector<int64_t>> date_s(symbols.size());
  for (size_t s = 0; s < symbols.size(); ++s) {
    Columns& c = eng.cols[s];
    auto rd = [](py::array_t<double> a) -> const double* {
      if (a.size() == 0) return nullptr;
      return a.data();
    };
    c.n = dates[s].size();
    date_s[s].resize(c.n);
    for (size_t i = 0; i < c.n; ++i) date_s[s][i] = dates[s].data()[i];
    c.open = rd(opens[s]);
    c.high = rd(highs[s]);
    c.low = rd(lows[s]);
    c.close = rd(closes[s]);
    c.volume = rd(volumes[s]);
    c.amount = rd(amounts[s]);
    c.prev_close = rd(prev_closes[s]);
    c.limit_up = limit_ups[s].size() ? rd(limit_ups[s]) : nullptr;
    c.limit_down = limit_downs[s].size() ? rd(limit_downs[s]) : nullptr;
    for (auto& [k, arr] : extras[s]) c.extra[k] = arr.size() ? arr.data() : nullptr;
    eng.rules[s] = rules_for(symbols[s]);
  }

  // 风控规则
  for (auto item : risk_rules) {
    py::dict d = py::cast<py::dict>(item);
    RiskRuleCfg cfg;
    cfg.id = py::cast<std::string>(d["id"]);
    cfg.enabled = d.contains("enabled") ? py::cast<bool>(d["enabled"]) : true;
    if (d.contains("limit") && !d["limit"].is_none()) cfg.p_limit = py::cast<double>(d["limit"]);
    if (d.contains("max_orders") && !d["max_orders"].is_none())
      cfg.p_max_orders = static_cast<int>(py::cast<double>(d["max_orders"]));
    if (d.contains("window_seconds") && !d["window_seconds"].is_none())
      cfg.p_window = py::cast<double>(d["window_seconds"]);
    if (d.contains("symbols") && !d["symbols"].is_none()) {
      for (auto s : py::cast<py::list>(d["symbols"])) cfg.p_symbols.push_back(py::cast<std::string>(s));
    }
    eng.risk.rules.push_back(cfg);
  }

  eng.ensure_builtin_states();

  // 时间戳并集（date 列存的是相对 ns 偏移 + start_ns）
  std::vector<int64_t> all;
  for (auto& v : date_s)
    for (int64_t x : v) all.push_back(x);
  std::sort(all.begin(), all.end());
  all.erase(std::unique(all.begin(), all.end()), all.end());

  std::vector<std::unordered_map<int64_t, size_t>> index_maps(symbols.size());
  for (size_t s = 0; s < symbols.size(); ++s)
    for (size_t i = 0; i < eng.cols[s].n; ++i) index_maps[s][date_s[s][i]] = i;

  size_t total = all.size();
  size_t done = 0;
  int64_t last_day = -1;
  ClockNs last_ns = start_ns;
  bool has_progress = !progress.is_none();
  auto report = [&](size_t step) {
    if (!has_progress) return;
    if (step % std::max<size_t>(1, total / 200) == 0 || step == total) {
      py::gil_scoped_acquire acquire;
      try {
        progress(static_cast<double>(step) / static_cast<double>(total),
                 std::to_string(step) + "/" + std::to_string(total));
      } catch (const py::error_already_set&) {
      }
    }
  };

  std::vector<Snapshot> snapshots;
  {
    py::gil_scoped_release release;  // 纯 C++ 状态推进，释放 GIL
  for (size_t step = 1; step <= total; ++step) {
    int64_t ts = all[step - 1];
    // 收集本时间戳有数据的标的
    std::vector<size_t> bar_syms, bar_idx;
    for (size_t s = 0; s < symbols.size(); ++s) {
      auto it = index_maps[s].find(ts);
      if (it != index_maps[s].end()) {
        bar_syms.push_back(s);
        bar_idx.push_back(it->second);
      }
    }
    if (bar_syms.empty()) { done++; continue; }

    ClockNs moment_ns = ts;
    last_ns = moment_ns;
    int64_t day = moment_ns / 86400000000000LL;
    if (day != last_day) {
      last_day = day;
      eng.acct.on_new_session();
    }

    // 1) 撮合上一根 bar 的订单 + 重估
    std::vector<BarView> views(bar_syms.size());
    for (size_t k = 0; k < bar_syms.size(); ++k) {
      size_t s = bar_syms[k], i = bar_idx[k];
      const Columns& c = eng.cols[s];
      views[k].symbol = &eng.symbols[s];
      views[k].dt_ns = moment_ns;
      views[k].open = c.open[i];
      views[k].high = c.high[i];
      views[k].low = c.low[i];
      views[k].close = c.close[i];
      views[k].volume = c.volume[i];
      views[k].amount = c.amount[i];
      views[k].prev_close = c.prev_close ? c.prev_close[i] : NAN_;
      views[k].limit_up = c.limit_up ? c.limit_up[i] : NAN_;
      views[k].limit_down = c.limit_down ? c.limit_down[i] : NAN_;
      eng.broker.on_bar(views[k]);
      eng.cursor_[s] = i;
      // 内置指标状态每根 bar 都要更新（即使策略稍后 warmup-return）
      eng.update_builtin_states(s, i);
    }
    // Python step 第 2 步：撮合后再按收盘价重估一遍（覆盖 apply_fill 的成交价 mark）
    for (size_t k = 0; k < bar_syms.size(); ++k) {
      size_t s = bar_syms[k], i = bar_idx[k];
      eng.acct.mark_price(eng.symbols[s], eng.cols[s].close[i]);
    }

    // 2) 重估 + 权益快照（与 Python step 顺序一致）
    {
      Snapshot snap;
      snap.dt_ns = moment_ns;
      snap.cash = eng.acct.cash;
      snap.frozen_cash = eng.acct.frozen_cash;
      snap.market_value = eng.acct.market_value();
      snap.total_equity = eng.acct.equity();
      snap.realized_pnl = eng.acct.realized_pnl;
      snap.unrealized = eng.acct.unrealized_pnl();
      snap.total_fees = eng.acct.total_fees;
      snap.position_count = eng.acct.holding_count();
      snapshots.push_back(snap);
    }

    // 3) 策略
    if (kind == StrategyKind::CustomRule)
      eng.run_custom(bar_syms, bar_idx, moment_ns);
    else
      eng.run_builtin(bar_syms, bar_idx, moment_ns);

    // 4) 日终（DAY 单过期）
    eng.broker.end_of_day(moment_ns);

    done++;
    report(step);
  }
  }  // gil_scoped_release

  report(total);
  // session.finish()：回测结束时撤销所有未成交挂单（TradingSession.finish）
  for (auto& [id, o] : eng.broker.orders) {
    if (o.is_open()) {
      o.status = "cancelled";
      o.reason = "回测结束";
      o.updated_ns = last_ns;
      if (o.frozen_cash != 0.0) {
        eng.acct.unfreeze(o.frozen_cash);
        o.frozen_cash = 0.0;
      }
    }
  }

  // ---- 结果组装 ---- //
  py::list equity;
  for (const auto& s : snapshots) {
    double total_return = initial_cash > 0 ? s.total_equity / initial_cash - 1.0 : 0.0;
    double cash_ratio = s.total_equity > 0 ? s.cash / s.total_equity : 0.0;
    py::dict d;
    d["dt"] = iso_from_ns(s.dt_ns);
    d["cash"] = py_round(s.cash, 2);
    d["frozen_cash"] = py_round(s.frozen_cash, 2);
    d["market_value"] = py_round(s.market_value, 2);
    d["total_equity"] = py_round(s.total_equity, 2);
    d["initial_cash"] = py_round(initial_cash, 2);
    d["realized_pnl"] = py_round(s.realized_pnl, 2);
    d["unrealized_pnl"] = py_round(s.unrealized, 2);
    d["total_fees"] = py_round(s.total_fees, 2);
    d["total_return"] = py_round(total_return, 6);
    d["cash_ratio"] = py_round(cash_ratio, 6);
    d["position_ratio"] = py_round(1.0 - cash_ratio, 6);
    d["position_count"] = s.position_count;
    equity.append(d);
  }

  py::list trades;
  for (const auto& f : eng.broker.fills) {
    py::dict d;
    double gross = std::abs(f.price * f.quantity);
    d["id"] = f.id;
    d["order_id"] = f.order_id;
    d["symbol"] = f.symbol;
    d["side"] = f.buy ? "buy" : "sell";
    d["quantity"] = f.quantity;
    d["price"] = py_round(f.price, 4);
    d["gross"] = py_round(gross, 4);
    py::dict fees;
    fees["commission"] = py_round(f.fees.commission, 4);
    fees["stamp_tax"] = py_round(f.fees.stamp_tax, 4);
    fees["transfer_fee"] = py_round(f.fees.transfer_fee, 4);
    fees["slippage"] = py_round(f.fees.slippage, 4);
    fees["total"] = py_round(f.fees.total(), 4);
    d["fees"] = fees;
    d["cash_flow"] = py_round((f.buy ? -1.0 : 1.0) * gross - f.fees.total(), 4);
    d["realized_pnl"] = py_round(f.realized_pnl, 4);
    d["dt"] = iso_from_ns(f.dt_ns);
    d["strategy"] = f.strategy;
    d["tag"] = f.tag;
    trades.append(d);
  }

  py::list orders;
  for (const auto& [id, o] : eng.broker.orders) {
    py::dict d;
    d["id"] = o.id;
    d["symbol"] = o.symbol;
    d["side"] = o.buy ? "buy" : "sell";
    d["quantity"] = o.quantity;
    d["order_type"] = o.is_limit ? "limit" : "market";
    d["limit_price"] = o.is_limit ? py::cast<py::object>(py::float_(o.limit_price)) : py::none();
    d["tif"] = o.tif_day ? "DAY" : "GTC";
    d["status"] = o.status;
    d["filled_quantity"] = o.filled_quantity;
    d["avg_fill_price"] = py_round(o.avg_fill_price, 4);
    py::dict fees;
    fees["commission"] = py_round(o.fees.commission, 4);
    fees["stamp_tax"] = py_round(o.fees.stamp_tax, 4);
    fees["transfer_fee"] = py_round(o.fees.transfer_fee, 4);
    fees["slippage"] = py_round(o.fees.slippage, 4);
    fees["total"] = py_round(o.fees.total(), 4);
    d["fees"] = fees;
    d["created_at"] = iso_from_ns(o.created_ns);
    d["submitted_at"] = o.submitted_ns ? py::cast<py::object>(py::str(iso_from_ns(o.submitted_ns))) : py::none();
    d["updated_at"] = o.updated_ns ? py::cast<py::object>(py::str(iso_from_ns(o.updated_ns))) : py::none();
    d["strategy"] = o.strategy;
    d["tag"] = o.tag;
    d["reason"] = o.reason;
    py::dict meta;
    if (o.frozen_cash != 0.0) meta["frozen_cash"] = o.frozen_cash;
    if (o.has_frozen_qty) meta["frozen_qty"] = o.remaining();
    if (!o.last_block.empty()) {
      meta["last_block"] = o.last_block;
      meta["last_block_detail"] = o.last_block_detail;
    }
    d["meta"] = meta;
    orders.append(d);
  }

  py::list positions;
  for (const auto& [sym, pos] : eng.acct.positions) {
    if (pos.flat()) continue;
    py::dict d;
    double mv = pos.market_value();
    double cost_value = pos.cost_value();
    d["symbol"] = sym;
    d["quantity"] = pos.quantity;
    d["available"] = pos.available;
    d["avg_cost"] = py_round(pos.avg_cost, 4);
    d["last_price"] = py_round(pos.last_price, 4);
    d["cost_value"] = py_round(cost_value, 2);
    d["market_value"] = py_round(mv, 2);
    d["unrealized_pnl"] = py_round(mv - cost_value, 2);
    d["unrealized_pct"] = py_round(cost_value != 0 ? (mv - cost_value) / cost_value : 0.0, 6);
    d["realized_pnl"] = py_round(pos.realized_pnl, 2);
    d["total_pnl"] = py_round(pos.realized_pnl + (mv - cost_value), 2);
    d["opened_at"] = pos.opened_ns ? py::cast<py::object>(py::str(iso_from_ns(pos.opened_ns))) : py::none();
    positions.append(d);
  }

  py::dict out;
  out["status"] = "finished";
  out["equity_curve"] = equity;
  out["trades"] = trades;
  out["orders"] = orders;
  out["positions"] = positions;
  out["steps"] = static_cast<uint64_t>(total);
  out["logs"] = py::list();
  return out;
}

}  // namespace qcore
