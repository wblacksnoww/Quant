// 增量指标状态机：与 quant.strategy.indicators 的 pandas 实现数值对齐。
// 所有 ewm 均为 adjust=False + 忽略 NaN（pandas 默认），min_periods 按非 NaN 计数。
#pragma once
#include "qcore.hpp"
#include <deque>

namespace qcore {

struct Ema {
  double alpha;
  int min_periods;
  double y = NAN_;
  long count = 0;
  Ema(double alpha_, int mp) : alpha(alpha_), min_periods(mp) {}
  void update(double x) {
    if (is_nan(x)) return;
    count++;
    y = (count == 1) ? x : (1.0 - alpha) * y + alpha * x;
  }
  double value() const { return count >= min_periods ? y : NAN_; }
};

// 滚动窗口（仅存非 NaN 值；前导 NaN 情形下与 pandas 行窗口等价）
struct Roll {
  int period;
  std::deque<double> buf;
  explicit Roll(int p) : period(p) {}
  void update(double x) {
    if (!is_nan(x)) {
      buf.push_back(x);
      if (static_cast<int>(buf.size()) > period) buf.pop_front();
    }
  }
  bool ready() const { return static_cast<int>(buf.size()) >= period; }
  double sma() const {
    double s = 0;
    for (double v : buf) s += v;
    return s / buf.size();
  }
  double std_pop() const {  // ddof=0
    if (buf.empty()) return NAN_;
    double mean = sma(), s = 0;
    for (double v : buf) s += (v - mean) * (v - mean);
    return std::sqrt(s / buf.size());
  }
  double high() const {
    double m = NAN_;
    for (double v : buf) m = is_nan(m) ? v : std::max(m, v);
    return m;
  }
  double low() const {
    double m = NAN_;
    for (double v : buf) m = is_nan(m) ? v : std::min(m, v);
    return m;
  }
};

// 一个指标实例：kind + 参数 + 内部状态。aux 提供栏位（ATR 用）。
struct Indicator {
  enum class Kind { SMA, EMA, STD, RSI, ATR, ROLL_HIGH, ROLL_LOW, MOMENTUM, BOLL_L, BOLL_M, BOLL_U, MACD_DIF, MACD_DEA, MACD_HIST };
  Kind kind;
  int period = 0, fast = 0, slow = 0, signal = 0;
  double width = 2.0;
  Roll roll{1}, roll2{1};
  Ema ema{0.0, 1}, ema2{0.0, 1}, ema3{0.0, 1};
  double prev_x = NAN_;  // 上一根 parent 值（RSI delta 用）
  double cur = NAN_, prev = NAN_;

  Indicator(Kind k, int period_ = 0, double width_ = 2.0, int fast_ = 0, int slow_ = 0, int signal_ = 0);

  // x = 输入序列当前值（一般是 close）；atr 用 high/low/close 全套
  void update(double x, double high, double low, double close, double close_prev);
  double value() const { return cur; }
  double previous() const { return prev; }
};

}  // namespace qcore
