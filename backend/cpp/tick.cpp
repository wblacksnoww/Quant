// tick 级回测实现。
//
// 性能纪律：所有 tick 在同一个 C++ 循环内处理完，不回调 Python。
// 下面的 run_tick_backtest_one 从第一根 tick 跑到最后一根，
// 中途只有可选的进度回调（每 N 个 tick 一次，不是每 tick 一次）。

#include "tick.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace qcore {

namespace {
constexpr int64_t kNsPerDay = 86400000000000LL;
constexpr double kNan = std::numeric_limits<double>::quiet_NaN();

double round_lot(double qty, double lot) {
  if (lot <= 0) return qty;
  return std::floor(qty / lot) * lot;
}

// 买入手续费：佣金（有最低值）+ 过户费视为已含在佣金率里
double buy_fee(double amount, const TickConfig& cfg) {
  return std::max(amount * cfg.fee_rate, cfg.min_fee);
}

// 卖出手续费：佣金 + 印花税
double sell_fee(double amount, const TickConfig& cfg) {
  return std::max(amount * cfg.fee_rate, cfg.min_fee) + amount * cfg.stamp_tax;
}
}  // namespace

size_t TickSeries::index_at_or_before(int64_t ts) const {
  if (ts_ns.empty()) return 0;
  auto it = std::upper_bound(ts_ns.begin(), ts_ns.end(), ts);
  if (it == ts_ns.begin()) return 0;
  return static_cast<size_t>(it - ts_ns.begin()) - 1;
}

void TickSeries::append_as_bar(size_t begin, size_t end, std::vector<double>& out_open,
                               std::vector<double>& out_high, std::vector<double>& out_low,
                               std::vector<double>& out_close,
                               std::vector<double>& out_volume) const {
  if (begin >= end || end > size()) return;
  double o = price[begin];
  double h = *std::max_element(price.begin() + begin, price.begin() + end);
  double l = *std::min_element(price.begin() + begin, price.begin() + end);
  double c = price[end - 1];
  double v = 0.0;
  for (size_t i = begin; i < end; ++i) v += volume[i];
  out_open.push_back(o);
  out_high.push_back(h);
  out_low.push_back(l);
  out_close.push_back(c);
  out_volume.push_back(v);
}

void TickAccount::apply_buy(double qty, double price, double fee, int64_t day) {
  double cost = qty * price + fee;
  // 现金闸门：绝不允许负现金（与 bar 级 apply_fill 同一纪律）
  if (cost > cash + 1e-6) return;
  double new_qty = position + qty;
  avg_cost = new_qty > 0 ? (avg_cost * position + qty * price) / new_qty : 0.0;
  position = new_qty;
  cash -= cost;
  total_fees += fee;
  last_buy_day = day;
  // T+1：当日买入不可卖，available 不变
}

void TickAccount::apply_sell(double qty, double price, double fee) {
  if (qty > available + 1e-9) return;
  double proceeds = qty * price - fee;
  realized_pnl += (price - avg_cost) * qty - fee;
  position -= qty;
  available -= qty;
  cash += proceeds;
  total_fees += fee;
  if (position <= 1e-9) {
    position = 0.0;
    available = 0.0;
    avg_cost = 0.0;
  }
}

TickSeriesResult run_tick_backtest_one(const TickSeries& series, const CompiledSpec& spec,
                                        const TickConfig& cfg) {
  TickSeriesResult out;
  const size_t n = series.size();
  if (n == 0) return out;

  TickAccount acct(cfg.initial_cash);
  SpecState state(spec);

  // 重采样窗口宽度：默认 1 分钟
  const int64_t bar_width = cfg.bar_width_ns > 0 ? cfg.bar_width_ns : 60LL * 1000000000LL;
  const int need_bars = cfg.bars_for_signal > 0 ? cfg.bars_for_signal : std::max(2, spec.lookback + 2);

  // 重采样缓冲：滚动保留最近 need_bars 根 bar
  std::vector<double> b_open, b_high, b_low, b_close, b_vol;
  b_open.reserve(need_bars + 1);
  b_high.reserve(need_bars + 1);
  b_low.reserve(need_bars + 1);
  b_close.reserve(need_bars + 1);
  b_vol.reserve(need_bars + 1);

  size_t cur_bar_begin = 0;
  int64_t cur_bar_id = series.ts_ns[0] / bar_width;
  int64_t last_day = series.ts_ns[0] / kNsPerDay;

  bool has_entry = false;  // 上一根重采样 bar 的入场信号
  bool has_exit = false;

  const double lot = cfg.lot > 0 ? cfg.lot : 1.0;
  // 每多少 tick 记一次权益快照（避免 snapshots 爆炸）
  const size_t snap_stride = std::max<size_t>(1, n / 2000);

  for (size_t i = 0; i < n; ++i) {
    const int64_t ts = series.ts_ns[i];
    const double px = series.price[i];
    const int64_t day = ts / kNsPerDay;

    if (day != last_day) {
      acct.on_new_session(day);
      last_day = day;
    }
    acct.mark(px);

    // --- 1) 撮合：用上一根 bar 产生的信号，在当前 tick 价格上成交 ---
    //    这保证"决策在 t 收盘、成交在 t 之后"，与 bar 级时序纪律一致。
    if (has_exit && acct.available > 0) {
      double qty = acct.available;
      // 成交量约束：单个 tick 最多吃掉 volume * pct
      double cap = series.volume[i] * cfg.volume_limit_pct;
      if (cap > 0 && qty > cap) qty = round_lot(cap, lot);
      if (qty > 0) {
        double fill_px = px;
        if (cfg.slippage_bps > 0) fill_px *= (1.0 - cfg.slippage_bps / 10000.0);
        double fee = sell_fee(qty * fill_px, cfg);
        double before_pos = acct.position;
        acct.apply_sell(qty, fill_px, fee);
        if (acct.position < before_pos - 1e-9) {
          TickFill f;
          f.ts_ns = ts;
          f.price = fill_px;
          f.quantity = qty;
          f.fee = fee;
          f.is_buy = false;
          out.fills.push_back(f);
        }
      }
      has_exit = false;
    } else if (has_entry && acct.position <= 1e-9) {
      // 目标仓位 = 权益 * position_pct，换算出股数
      double budget = acct.equity() * std::max(0.0, std::min(1.0, cfg.position_pct));
      double fill_px = px;
      if (cfg.slippage_bps > 0) fill_px *= (1.0 + cfg.slippage_bps / 10000.0);
      if (fill_px > 0) {
        double qty = round_lot(budget / fill_px, lot);
        double cap = series.volume[i] * cfg.volume_limit_pct;
        if (cap > 0 && qty > cap) qty = round_lot(cap, lot);
        // 现金上限（含费用余量）
        double affordable = round_lot(acct.cash / (fill_px * (1.0 + cfg.fee_rate + 0.002)), lot);
        if (qty > affordable) qty = affordable;
        if (qty > 0) {
          double fee = buy_fee(qty * fill_px, cfg);
          acct.apply_buy(qty, fill_px, fee, day);
          TickFill f;
          f.ts_ns = ts;
          f.price = fill_px;
          f.quantity = qty;
          f.fee = fee;
          f.is_buy = true;
          out.fills.push_back(f);
        }
      }
      has_entry = false;
    }

    // --- 2) 止损 / 止盈：tick 级检查（这是 tick 回测相对 bar 级的核心优势） ---
    if (acct.available > 0 && acct.avg_cost > 0) {
      double pnl = px / acct.avg_cost - 1.0;
      bool hit_stop = cfg.stop_loss > 0 && pnl <= -cfg.stop_loss;
      bool hit_take = cfg.take_profit > 0 && pnl >= cfg.take_profit;
      if (hit_stop || hit_take) {
        double qty = acct.available;
        double cap = series.volume[i] * cfg.volume_limit_pct;
        if (cap > 0 && qty > cap) qty = round_lot(cap, lot);
        if (qty > 0) {
          double fee = sell_fee(qty * px, cfg);
          acct.apply_sell(qty, px, fee);
          TickFill f;
          f.ts_ns = ts;
          f.price = px;
          f.quantity = qty;
          f.fee = fee;
          f.is_buy = false;
          out.fills.push_back(f);
        }
      }
    }

    // --- 3) 重采样：一根 bar 结束时（tick 跨入下一根），用新 bar 求值出信号 ---
    bool bar_closed = (i + 1 == n) || (series.ts_ns[i + 1] / bar_width) != cur_bar_id;
    if (bar_closed) {
      series.append_as_bar(cur_bar_begin, i + 1, b_open, b_high, b_low, b_close, b_vol);
      cur_bar_begin = i + 1;
      cur_bar_id = (i + 1 < n) ? series.ts_ns[i + 1] / bar_width : cur_bar_id;

      // 只保留最近 need_bars 根
      while (b_close.size() > static_cast<size_t>(need_bars)) {
        b_open.erase(b_open.begin());
        b_high.erase(b_high.begin());
        b_low.erase(b_low.begin());
        b_close.erase(b_close.begin());
        b_vol.erase(b_vol.begin());
      }

      if (b_close.size() >= static_cast<size_t>(need_bars) && need_bars >= 2) {
        Columns cols;
        cols.n = b_close.size();
        cols.open = b_open.data();
        cols.high = b_high.data();
        cols.low = b_low.data();
        cols.close = b_close.data();
        cols.volume = b_vol.data();
        cols.amount = nullptr;
        cols.prev_close = nullptr;
        cols.limit_up = nullptr;
        cols.limit_down = nullptr;

        // 窗口重喂：与 bar 级的 SpecEvaluator::evaluate_window 同一口径
        state.reset();
        std::pair<bool, bool> sig{false, false};
        for (size_t k = 0; k < cols.n; ++k) sig = state.step(k, cols);
        has_entry = sig.first;
        has_exit = sig.second;
      } else {
        out.ticks_skipped_warmup++;
        has_entry = false;
        has_exit = false;
      }
    }

    // --- 4) 权益快照 ---
    if (i % snap_stride == 0 || i + 1 == n) {
      out.snap_ts.push_back(ts);
      out.snap_equity.push_back(acct.equity());
      out.snap_cash.push_back(acct.cash);
      out.snap_position.push_back(acct.position);
      out.snap_price.push_back(px);
    }

    out.ticks_processed++;
  }

  return out;
}

TickPortfolioResult run_tick_backtest(const std::vector<std::string>& symbols,
                                       const std::vector<TickSeries>& series,
                                       const CompiledSpec& spec, const TickConfig& cfg) {
  // 阶段一：各标的独立回测。真正的跨标的资金共享需要全局组合层，
  // 这里先保持"每标的一份独立资金"，与单标的回测口径一致；
  // 组合层的资金共享留给 bar 级引擎（它已有完整 Account）。
  TickPortfolioResult out;
  double per_symbol_cash = symbols.empty() ? cfg.initial_cash
                                           : cfg.initial_cash / static_cast<double>(symbols.size());
  for (size_t s = 0; s < symbols.size() && s < series.size(); ++s) {
    TickConfig sub = cfg;
    sub.initial_cash = per_symbol_cash;
    out.per_symbol.emplace_back(symbols[s], run_tick_backtest_one(series[s], spec, sub));
  }
  return out;
}

}  // namespace qcore
