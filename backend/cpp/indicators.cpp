#include "indicators.hpp"

namespace qcore {

Indicator::Indicator(Kind k, int period_, double width_, int fast_, int slow_, int signal_)
    : kind(k), period(period_), fast(fast_), slow(slow_), signal(signal_), width(width_),
      roll(std::max(1, kind == Kind::MOMENTUM ? period_ + 1 : period_)), roll2(std::max(1, signal_)),
      ema(2.0 / (std::max(2, period_) + 1), std::max(2, period_)),
      ema2(2.0 / (std::max(2, signal_) + 1), std::max(2, signal_)),
      ema3(2.0 / (std::max(2, signal_) + 1), std::max(2, signal_)) {
  if (kind == Kind::MACD_DIF || kind == Kind::MACD_DEA || kind == Kind::MACD_HIST) {
    ema = Ema(2.0 / (fast + 1.0), fast);
    ema2 = Ema(2.0 / (slow + 1.0), slow);
    ema3 = Ema(2.0 / (signal + 1.0), signal);
  }
  if (kind == Kind::RSI) {
    ema = Ema(1.0 / period, period);      // avg_gain
    ema2 = Ema(1.0 / period, period);     // avg_loss
  }
  if (kind == Kind::ATR) {
    ema = Ema(1.0 / period, period);
  }
}

void Indicator::update(double x, double high, double low, double close, double close_prev) {
  prev = cur;
  switch (kind) {
    case Kind::SMA:
      roll.update(x);
      cur = roll.ready() ? roll.sma() : NAN_;
      break;
    case Kind::STD:
      roll.update(x);
      cur = roll.ready() ? roll.std_pop() : NAN_;
      break;
    case Kind::ROLL_HIGH:
      roll.update(x);
      cur = roll.ready() ? roll.high() : NAN_;
      break;
    case Kind::ROLL_LOW:
      roll.update(x);
      cur = roll.ready() ? roll.low() : NAN_;
      break;
    case Kind::MOMENTUM: {
      // pandas pct_change(period)：x / x_{t-period} - 1（自然除零 → inf）
      roll.update(x);
      if (static_cast<int>(roll.buf.size()) > period)
        cur = x / roll.buf.front() - 1.0;
      else
        cur = NAN_;
      break;
    }
    case Kind::EMA:
      ema.update(x);
      cur = ema.value();
      break;
    case Kind::RSI: {
      double delta = (is_nan(prev_x) || is_nan(x)) ? NAN_ : x - prev_x;
      double gain = is_nan(delta) ? NAN_ : std::max(delta, 0.0);
      double loss = is_nan(delta) ? NAN_ : std::max(-delta, 0.0);
      ema.update(gain);
      ema2.update(loss);
      if (ema2.count < ema2.min_periods)
        cur = NAN_;
      else if (ema2.y == 0.0)
        cur = 100.0;  // rs=NaN → fillna(100)，avg_loss 非空保留
      else
        cur = 100.0 - 100.0 / (1.0 + ema.y / ema2.y);
      break;
    }
    case Kind::ATR: {
      double tr1 = std::abs(high - low);
      double tr2 = is_nan(close_prev) ? NAN_ : std::abs(high - close_prev);
      double tr3 = is_nan(close_prev) ? NAN_ : std::abs(low - close_prev);
      double tr = std::max(tr1, std::max(is_nan(tr2) ? tr1 : tr2, is_nan(tr3) ? tr1 : tr3));
      ema.update(tr);
      cur = ema.value();
      break;
    }
    case Kind::BOLL_L:
    case Kind::BOLL_M:
    case Kind::BOLL_U: {
      roll.update(x);
      if (!roll.ready()) { cur = NAN_; break; }
      double mid = roll.sma(), dev = roll.std_pop();
      cur = kind == Kind::BOLL_L ? mid - width * dev : (kind == Kind::BOLL_M ? mid : mid + width * dev);
      break;
    }
    case Kind::MACD_DIF:
    case Kind::MACD_DEA:
    case Kind::MACD_HIST: {
      ema.update(x);   // fast
      ema2.update(x);  // slow
      double dif = (ema.count >= ema.min_periods && ema2.count >= ema2.min_periods) ? ema.y - ema2.y : NAN_;
      if (kind == Kind::MACD_DIF) { cur = dif; break; }
      ema3.update(dif);  // dea = ema(dif, signal)，NaN 穿透
      if (kind == Kind::MACD_DEA) { cur = ema3.value(); break; }
      double dea = ema3.value();
      cur = (is_nan(dif) || is_nan(dea)) ? NAN_ : (dif - dea) * 2.0;
      break;
    }
  }
  prev_x = x;
}

}  // namespace qcore
