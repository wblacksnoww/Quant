// 账本 / 费用 / 撮合 / 券商 / 风控 —— 与 Python 版逐点对齐。
#include "qcore.hpp"
#include <cstdio>
#include <cstring>

namespace qcore {

double py_round(double v, int digits) {
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.*f", digits, v);
  return std::atof(buf);
}

double round_price_half_up(double v, int digits) {
  // Decimal(str(value)).quantize(1e-digits, ROUND_HALF_UP) 的等价实现：
  // 先用 %.12f 拿到十进制字符串，再做半上舍入。
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.*f", digits + 6, v);
  // 解析小数点后 digits+6 位，第 digits+1 位起决定舍入
  const char* dot = std::strchr(buf, '.');
  if (dot == nullptr) return std::atof(buf);
  int keep = digits;
  const char* p = dot + 1 + keep;
  int next_digit = (*p) ? (*p - '0') : 0;
  double factor = 1.0;
  for (int i = 0; i < digits; ++i) factor *= 10.0;
  double scaled = std::atof(buf) * factor;
  // 用字符串做半上，避免浮点加 0.5 的边界
  std::string s(buf);
  std::size_t pos = s.find('.');
  std::string digits_str = s.substr(pos + 1);
  if (static_cast<int>(digits_str.size()) > keep && digits_str[keep] >= '5') {
    // 半上：第 keep 位后 >=5 则进位
    long long whole = 0;
    {
      std::string int_part = s.substr(0, pos);
      whole = std::atoll(int_part.c_str());
    }
    long long frac = 0;
    for (int i = 0; i < keep; ++i) {
      frac = frac * 10 + (i < static_cast<int>(digits_str.size()) ? digits_str[i] - '0' : 0);
    }
    frac += 1;  // half-up
    long long carry_limit = 1;
    for (int i = 0; i < keep; ++i) carry_limit *= 10;
    if (frac >= carry_limit) {
      whole += 1;
      frac -= carry_limit;
    }
    char out[64];
    if (keep == 0)
      std::snprintf(out, sizeof(out), "%lld", whole);
    else
      std::snprintf(out, sizeof(out), "%lld.%0*lld", whole, keep, frac);
    return std::atof(out);
  }
  return std::atof(s.substr(0, pos + 1 + (keep > 0 ? keep : 0)).c_str());
}

std::string iso_from_ns(ClockNs ns) {
  // naive UTC → "YYYY-MM-DDTHH:MM:SS"（Python datetime.isoformat() 无微秒时省略）
  int64_t secs = ns / 1000000000LL;
  int millis = static_cast<int>((ns % 1000000000LL) / 1000000LL);
  int64_t days = secs / 86400;
  int64_t rem = secs % 86400;
  if (rem < 0) { rem += 86400; days -= 1; }
  int hh = static_cast<int>(rem / 3600), mm = static_cast<int>((rem % 3600) / 60), ss = static_cast<int>(rem % 60);
  // civil days → Y/M/D（Howard Hinnant 算法）
  int64_t z = days + 719468;
  int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  int64_t doe = z - era * 146097;
  int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int64_t y = yoe + era * 400;
  int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  int64_t mp = (5 * doy + 2) / 153;
  int64_t d = doy - (153 * mp + 2) / 5 + 1;
  int64_t m = mp + (mp < 10 ? 3 : -9);
  y += (m <= 2);
  char buf[48];
  if (millis != 0)
    std::snprintf(buf, sizeof(buf), "%04lld-%02lld-%02dT%02d:%02d:%02d.%03d000",
                  static_cast<long long>(y), static_cast<long long>(m), static_cast<long long>(d), hh, mm, ss, millis);
  else
    std::snprintf(buf, sizeof(buf), "%04lld-%02lld-%02dT%02d:%02d:%02d",
                  static_cast<long long>(y), static_cast<long long>(m), static_cast<long long>(d), hh, mm, ss);
  return buf;
}

// ------------------------------------------------------------------ //
// 市场规则
// ------------------------------------------------------------------ //
SymbolRules rules_for(const std::string& symbol) {
  // symbol 形如 "MARKET:CODE"
  SymbolRules r;
  std::string market, code;
  auto colon = symbol.find(':');
  if (colon != std::string::npos) {
    market = symbol.substr(0, colon);
    code = symbol.substr(colon + 1);
  } else {
    code = symbol;
  }
  if (market == "US") { r = {1, false, 0.0, 2}; return r; }
  if (market == "HK") { r = {100, false, 0.0, 3}; return r; }
  if (market == "CRYPTO") { r = {1, false, 0.0, 8}; return r; }
  if (market == "FUND") { r = {1, false, 0.0, 4}; return r; }
  // CN：按代码前缀
  if (code.rfind("688", 0) == 0 || code.rfind("689", 0) == 0) { r = {200, true, 0.20, 2}; return r; }
  if (code.rfind("300", 0) == 0 || code.rfind("301", 0) == 0) { r = {100, true, 0.20, 2}; return r; }
  if (code.rfind("8", 0) == 0 || code.rfind("43", 0) == 0 || code.rfind("92", 0) == 0) { r = {100, true, 0.30, 2}; return r; }
  if (code.rfind("51", 0) == 0 || code.rfind("56", 0) == 0 || code.rfind("58", 0) == 0 ||
      code.rfind("15", 0) == 0 || code.rfind("16", 0) == 0 || code.rfind("588", 0) == 0 ||
      code.rfind("159", 0) == 0) { r = {100, true, 0.10, 2}; return r; }
  r = {100, true, 0.10, 2};
  return r;
}

// ------------------------------------------------------------------ //
// 费用
// ------------------------------------------------------------------ //
double CostModel::slippage_price(bool buy, double price) const {
  double rate = slippage_bps / 10000.0;
  if (rate == 0) return price;
  return buy ? price * (1.0 + rate) : price * (1.0 - rate);
}

FeeBreakdown CostModel::fees(bool buy, double price, double qty, double gross) const {
  double value = gross;
  FeeBreakdown f;
  if (value > 0) {
    f.commission = std::max(min_commission, commission_rate * value);
    if (!buy || stamp_both_sides) f.stamp_tax = stamp_tax_rate * value;
    f.transfer_fee = transfer_fee_rate * value;
  }
  return f;
}

double CostModel::estimate_buy_cost(double price, double qty) const {
  double value = std::abs(price * qty);
  FeeBreakdown f = fees(true, price, qty, value);
  return value + f.total();
}

CostModel cost_from_dict(const std::map<std::string, double>& d, bool stamp_both_sides) {
  CostModel c;
  c.stamp_both_sides = stamp_both_sides;
  auto get = [&](const char* k, double def) {
    auto it = d.find(k);
    return it != d.end() ? it->second : def;
  };
  if (!d.empty()) {
    c.commission_rate = get("commission_rate", c.commission_rate);
    c.min_commission = get("min_commission", c.min_commission);
    c.stamp_tax_rate = get("stamp_tax_rate", c.stamp_tax_rate);
    c.transfer_fee_rate = get("transfer_fee_rate", c.transfer_fee_rate);
    c.slippage_bps = get("slippage_bps", c.slippage_bps);
  }
  return c;
}

// ------------------------------------------------------------------ //
// 订单
// ------------------------------------------------------------------ //
bool Order::is_open() const {
  return status == "pending" || status == "submitted" || status == "partial";
}
bool Order::is_final() const {
  return status == "filled" || status == "cancelled" || status == "rejected" || status == "expired";
}
double Order::remaining() const { return std::max(0.0, quantity - filled_quantity); }

void Order::apply_fill(double qty, double price, const FeeBreakdown& f) {
  double total_qty = filled_quantity + qty;
  if (total_qty <= 0) return;
  avg_fill_price = (avg_fill_price * filled_quantity + price * qty) / total_qty;
  filled_quantity = total_qty;
  fees.commission += f.commission;
  fees.stamp_tax += f.stamp_tax;
  fees.transfer_fee += f.transfer_fee;
  fees.slippage += f.slippage;
  status = remaining() == 0 ? "filled" : "partial";
}

void Order::reject(const std::string& r, ClockNs now) { status = "rejected"; reason = r; updated_ns = now; }
void Order::cancel_order(ClockNs now) { status = "cancelled"; reason = "用户撤单"; updated_ns = now; }
void Order::expire_order(const std::string& r, ClockNs now) { status = "expired"; reason = r; updated_ns = now; }

// ------------------------------------------------------------------ //
// 账户
// ------------------------------------------------------------------ //
Account::Account(double initial) : initial_cash(initial), cash(initial) {}

double Account::market_value() const {
  double mv = 0.0;
  for (const auto& [sym, pos] : positions) mv += pos.market_value();
  return mv;
}
double Account::unrealized_pnl() const {
  double u = 0.0;
  for (const auto& [sym, pos] : positions) u += pos.unrealized();
  return u;
}
Position& Account::get_position(const std::string& symbol) { return positions[symbol]; }
double Account::last_price_of(const std::string& symbol) const {
  // 镜像 Python Account.last_price：优先取持仓的 last_price
  // （清仓后仍是最后一笔成交价的旧值），其次全局 mark 价。
  auto pit = positions.find(symbol);
  if (pit != positions.end() && pit->second.last_price != 0.0) return pit->second.last_price;
  auto it = last_prices.find(symbol);
  return it != last_prices.end() ? it->second : 0.0;
}
void Account::mark_price(const std::string& symbol, double price) {
  if (is_nan(price) || price <= 0) return;
  last_prices[symbol] = price;
  auto it = positions.find(symbol);
  if (it != positions.end() && !it->second.flat()) it->second.last_price = price;
}
void Account::freeze(double amount) {
  if (amount <= 0) return;
  if (amount > available_cash() + 1e-6) {
    char buf[128];
    std::snprintf(buf, sizeof(buf), "可用资金不足：需要 %.2f，可用 %.2f", amount, available_cash());
    throw std::runtime_error(buf);
  }
  frozen_cash += amount;
}
void Account::unfreeze(double amount) {
  if (amount <= 0) return;
  frozen_cash = std::max(0.0, frozen_cash - amount);
}
double Account::realized_pnl_for(const std::string& symbol, bool buy, double price, double qty, double fees_total) const {
  if (buy) return 0.0;
  auto it = positions.find(symbol);
  double avg = it != positions.end() ? it->second.avg_cost : 0.0;
  return (price - avg) * qty - fees_total;
}
void Account::apply_fill(const Fill& fill) {
  Position& pos = get_position(fill.symbol);
  double gross = std::abs(fill.price * fill.quantity);
  double fees_total = fill.fees.total();
  if (fill.buy) {
    double new_qty = pos.quantity + fill.quantity;
    double new_cost = pos.cost_value() + gross + fees_total;
    pos.avg_cost = new_cost / new_qty;
    pos.quantity = new_qty;
    SymbolRules rules = rules_for(fill.symbol);
    if (!rules.t_plus_1) pos.available += fill.quantity;
    if (pos.opened_ns == 0) pos.opened_ns = fill.dt_ns;
    cash -= gross + fees_total;
  } else {
    double gross_profit = (fill.price - pos.avg_cost) * fill.quantity;
    double realized = gross_profit - fees_total;
    pos.realized_pnl += realized;
    pos.quantity -= fill.quantity;
    cash += gross - fees_total;
    realized_pnl += realized;
    if (pos.quantity == 0) pos.avg_cost = 0.0;
  }
  pos.available = std::min(pos.available, pos.quantity);
  total_fees += fees_total;
  turnover += gross;
  trade_count += 1;
  mark_price(fill.symbol, fill.price);
}
void Account::on_new_session() {
  for (auto& [sym, pos] : positions) pos.available = pos.quantity;
}
int Account::holding_count() const {
  int n = 0;
  for (const auto& [sym, pos] : positions) if (!pos.flat()) n++;
  return n;
}

// ------------------------------------------------------------------ //
// 撮合
// ------------------------------------------------------------------ //
MatchOutcome Matcher::match(const Order& order, const BarView& bar) const {
  SymbolRules rules = rules_for(*bar.symbol);
  if (!is_nan(bar.volume) && bar.volume <= 0)
    return {false, 0, 0, "suspended", "当日无成交（停牌）"};
  if (bar.open <= 0) return {false, 0, 0, "no_price", "缺少开盘价"};

  double limit_up = bar.limit_up, limit_down = bar.limit_down;
  if (is_nan(limit_up) && is_nan(limit_down)) {
    if (bar.prev_close > 0 && rules.limit_pct > 0) {
      limit_up = round_price_half_up(bar.prev_close * (1 + rules.limit_pct), rules.price_digits);
      limit_down = round_price_half_up(bar.prev_close * (1 - rules.limit_pct), rules.price_digits);
    } else {
      limit_up = limit_down = NAN_;
    }
  }

  if (rules.limit_pct > 0) {
    char buf[96];
    if (order.buy && !is_nan(limit_up) && bar.low >= limit_up - 1e-6) {
      std::snprintf(buf, sizeof(buf), "一字涨停（%.2f）买不进", limit_up);
      return {false, 0, 0, "limit_up", buf};
    }
    if (!order.buy && !is_nan(limit_down) && bar.high <= limit_down + 1e-6) {
      std::snprintf(buf, sizeof(buf), "一字跌停（%.2f）卖不出", limit_down);
      return {false, 0, 0, "limit_down", buf};
    }
  }

  double base_price;
  if (!order.is_limit) {
    base_price = bar.open;
  } else {
    double lp = order.limit_price;
    if (lp <= 0) return {false, 0, 0, "no_price", "限价单缺少价格"};
    char buf[128];
    if (order.buy) {
      if (bar.low > lp + 1e-9) {
        std::snprintf(buf, sizeof(buf), "最低价 %.2f 未触及限价 %.2f", bar.low, lp);
        return {false, 0, 0, "price_not_touched", buf};
      }
      base_price = std::min(lp, bar.open);
    } else {
      if (bar.high < lp - 1e-9) {
        std::snprintf(buf, sizeof(buf), "最高价 %.2f 未触及限价 %.2f", bar.high, lp);
        return {false, 0, 0, "price_not_touched", buf};
      }
      base_price = std::max(lp, bar.open);
    }
  }

  double price = cost.slippage_price(order.buy, base_price);

  double qty = order.remaining();
  if (volume_limit_pct > 0 && bar.volume > 0) {
    double capacity = bar.volume * volume_limit_pct;
    if (capacity <= 0) {
      char buf[64];
      std::snprintf(buf, sizeof(buf), "成交量 %.0f 过小", bar.volume);
      return {false, 0, 0, "no_liquidity", buf};
    }
    qty = std::min(qty, capacity);
  }
  int lot = rules.lot_size;
  if (lot > 1 && order.buy) qty = std::floor(qty / lot) * lot;
  if (qty <= 0) return {false, 0, 0, "no_liquidity", "不足一个交易单位"};
  return {true, price, qty, "", ""};
}

FeeBreakdown Matcher::fees(const Order& order, double price, double qty, double base_price) const {
  double gross = price * qty;
  FeeBreakdown base = cost.fees(order.buy, price, qty, gross);
  double slip = std::abs(price - base_price) * qty;
  base.slippage = slip;
  return base;
}

// ------------------------------------------------------------------ //
// 券商
// ------------------------------------------------------------------ //
Broker::Broker(Account& a, const CostModel& cost, double vol_pct, ClockNs t0)
    : acct(a), matcher{cost, vol_pct}, now_ns_(t0) {}

std::string Broker::next_id(const char* prefix) {
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%s%016zx", prefix, static_cast<size_t>(id_seq_++));
  return buf;
}

Order& Broker::record(Order o) {
  if (o.id.empty()) o.id = next_id("ord_");
  return orders.emplace(o.id, std::move(o)).first->second;
}

size_t Broker::open_order_count() const {
  size_t n = 0;
  for (const auto& [id, o] : orders) if (o.is_open()) n++;
  return n;
}
bool Broker::has_open_order(const std::string& symbol) const {
  for (const auto& [id, o] : orders)
    if (o.symbol == symbol && o.is_open()) return true;
  return false;
}
std::vector<Fill> Broker::take_fills() { return std::move(fills); }

double Broker::estimate_cash(const Order& o) const {
  double price = o.is_limit && o.limit_price > 0 ? o.limit_price : acct.last_price_of(o.symbol);
  if (price <= 0) price = o.is_limit && o.limit_price > 0 ? o.limit_price : 1.0;
  double buffer = o.is_limit ? 1.0 : 1.02;
  return matcher.cost.estimate_buy_cost(price * buffer, o.quantity);
}

double Broker::sellable(const std::string& symbol, const std::string& exclude_id) const {
  auto it = acct.positions.find(symbol);
  double avail = it != acct.positions.end() ? it->second.available : 0.0;
  double pending = 0.0;
  for (const auto& [id, o] : orders) {
    if (o.symbol == symbol && !o.buy && o.is_open() && o.id != exclude_id)
      pending += o.quantity - o.filled_quantity;
  }
  return std::max(0.0, avail - pending);
}

std::optional<std::string> Broker::validate(const Order& o) {
  if (o.quantity <= 0) return std::string("委托数量必须大于 0");
  if (o.is_limit && o.limit_price <= 0) return std::string("限价单必须提供有效限价");
  SymbolRules rules = rules_for(o.symbol);
  if (rules.lot_size > 1 && o.buy) {
    double rounded = std::floor(o.quantity / rules.lot_size) * rules.lot_size;
    if (std::abs(o.quantity - rounded) > 1e-9)
      return "买入数量须为 " + std::to_string(rules.lot_size) + " 股的整数倍";
  }
  auto it = acct.positions.find(o.symbol);
  double pos_qty = it != acct.positions.end() ? it->second.quantity : 0.0;
  bool full_exit = !o.buy && std::abs(o.quantity - pos_qty) <= 1e-9;
  if (rules.lot_size > 1) {
    double rem = std::fmod(o.quantity, rules.lot_size);
    bool valid = rem <= 1e-9 || rules.lot_size - rem <= 1e-9 || full_exit;
    if (!valid) return std::string("委托数量不合法");
  }
  if (o.buy) {
    double need = estimate_cash(o);
    if (need > acct.available_cash() + 1e-6) {
      char buf[128];
      std::snprintf(buf, sizeof(buf), "可用资金不足：需要 %.2f，可用 %.2f", need, acct.available_cash());
      return std::string(buf);
    }
  } else {
    double s = sellable(o.symbol, o.id);
    if (o.quantity > s + 1e-9) {
      char buf[128];
      std::snprintf(buf, sizeof(buf), "可卖数量不足：可卖 %.0f，委托 %.0f", s, o.quantity);
      return std::string(buf);
    }
  }
  return std::nullopt;
}

void Broker::submit(Order o) {
  if (o.id.empty()) o.id = next_id("ord_");
  o.created_ns = now_ns_;
  auto [it, inserted] = orders.emplace(o.id, std::move(o));
  Order& order = it->second;

  auto reason = validate(order);
  if (reason) {
    order.reject(*reason, now_ns_);
    return;
  }
  if (order.buy) {
    double frozen = estimate_cash(order);
    try {
      acct.freeze(frozen);
    } catch (const std::exception& exc) {
      order.reject(exc.what(), now_ns_);
      return;
    }
    order.frozen_cash = frozen;
  } else {
    order.has_frozen_qty = true;
  }
  order.status = "submitted";
  order.submitted_ns = now_ns_;
  order.updated_ns = now_ns_;
  queued.push_back(order.id);
}

bool Broker::cancel(const std::string& id, ClockNs now) {
  auto it = orders.find(id);
  if (it == orders.end() || !it->second.is_open()) return false;
  it->second.cancel_order(now);
  release_reservation(it->second);
  drop(id);
  return true;
}

void Broker::release_reservation(Order& o) {
  if (o.frozen_cash != 0.0) {
    acct.unfreeze(o.frozen_cash);
    o.frozen_cash = 0.0;
  }
  o.has_frozen_qty = false;
}

void Broker::drop(const std::string& id) {
  auto rm = [&](std::vector<std::string>& v) {
    for (size_t i = 0; i < v.size(); ++i)
      if (v[i] == id) { v.erase(v.begin() + i); break; }
  };
  rm(live);
  rm(queued);
}

void Broker::on_bar(const BarView& bar) {
  now_ns_ = bar.dt_ns;
  acct.mark_price(*bar.symbol, bar.close);
  if (!queued.empty()) {
    for (const auto& id : queued) live.push_back(id);
    queued.clear();
  }
  // Python: for order in list(self._live) —— 快照迭代，drop 只影响之后的撮合
  std::vector<std::string> snapshot = live;
  for (const auto& id : snapshot) {
    auto it = orders.find(id);
    if (it == orders.end() || !it->second.is_open()) continue;
    Order& o = it->second;
    if (o.symbol != *bar.symbol) continue;
    try_match(o, bar);
  }
}

bool Broker::try_match(Order& o, const BarView& bar) {
  if (!o.is_open() || o.symbol != *bar.symbol) return false;
  MatchOutcome out = matcher.match(o, bar);
  if (!out.filled) {
    if (out.reason == "suspended" || out.reason == "limit_up" || out.reason == "limit_down") {
      o.last_block = out.reason;
      o.last_block_detail = out.detail;
    }
    return false;
  }
  double qty = out.qty;
  auto it = acct.positions.find(o.symbol);
  double pos_qty = it != acct.positions.end() ? it->second.quantity : 0.0;
  if (!o.buy && qty > pos_qty) qty = pos_qty;
  if (qty <= 0) return false;

  double price = out.price;
  FeeBreakdown fees = matcher.fees(o, price, qty, bar.open);
  double realized = acct.realized_pnl_for(o.symbol, o.buy, price, qty, fees.total());
  Fill fill;
  fill.id = next_id("fl_");
  fill.order_id = o.id;
  fill.symbol = o.symbol;
  fill.buy = o.buy;
  fill.quantity = qty;
  fill.price = price;
  fill.dt_ns = bar.dt_ns;
  fill.fees = fees;
  fill.realized_pnl = realized;
  fill.strategy = o.strategy;
  fill.tag = o.tag;

  release_reservation(o);
  acct.apply_fill(fill);
  o.apply_fill(qty, price, fees);

  if (o.is_open() && o.buy) {
    double refreeze = matcher.cost.estimate_buy_cost(price, o.remaining());
    try {
      acct.freeze(refreeze);
      o.frozen_cash = refreeze;
    } catch (const std::exception&) {
      o.expire_order("剩余数量资金不足", now_ns_);
    }
  }
  if (o.is_open() && !o.buy) o.has_frozen_qty = true;

  fills.push_back(std::move(fill));
  return true;
}

void Broker::end_of_day(ClockNs now) {
  now_ns_ = now;
  for (size_t k = 0; k < live.size(); ) {
    Order& o = orders.at(live[k]);
    if (o.is_open() && o.tif_day) {
      o.expire_order("当日未成交", now);
      release_reservation(o);
      drop(o.id);
    } else {
      k++;
    }
  }
}

// ------------------------------------------------------------------ //
// 风控
// ------------------------------------------------------------------ //
std::string RiskManager::pre_trade(const Order& o, Account& acct, ClockNs now_ns, size_t open_orders,
                                   int holding_count) {
  double equity = acct.equity();
  int64_t day = now_ns / 86400000000000LL;
  if (day != today_day) {
    today_day = day;
    day_start_equity = equity;
    // on_new_day：目前只有频率规则需要，用不到额外动作
  }
  peak_equity = std::max(peak_equity, equity);
  if (day_start_equity <= 0) day_start_equity = equity;

  double drawdown = peak_equity > 0 ? 1.0 - equity / peak_equity : 0.0;
  double daily_pnl = day_start_equity > 0 ? equity / day_start_equity - 1.0 : 0.0;
  double last_price = acct.last_price_of(o.symbol);
  double px = o.is_limit && o.limit_price > 0 ? o.limit_price : last_price;

  for (const auto& rule : rules) {
    if (!rule.enabled) continue;
    char buf[160];
    if (rule.id == "max_position_pct") {
      if (o.buy && equity > 0 && px > 0) {
        auto it = acct.positions.find(o.symbol);
        double mv = it != acct.positions.end() ? it->second.market_value() : 0.0;
        double projected = (mv + px * o.quantity) / equity;
        double limit = rule.p_limit;
        if (projected > limit + 1e-9) {
          std::snprintf(buf, sizeof(buf), "[风控:%s] %s 目标仓位 %.1f%% 超过上限 %.1f%%",
                        rule.id.c_str(), o.symbol.c_str(), projected * 100, limit * 100);
          return buf;
        }
      }
    } else if (rule.id == "max_order_value_pct") {
      if (equity > 0 && px > 0) {
        double value = px * o.quantity;
        if (value / equity > rule.p_limit + 1e-9) {
          std::snprintf(buf, sizeof(buf), "[风控:%s] 单笔金额 %.0f 元占权益 %.1f%%，超过 %.1f%%",
                        rule.id.c_str(), value, value / equity * 100, rule.p_limit * 100);
          return buf;
        }
      }
    } else if (rule.id == "max_positions") {
      if (o.buy) {
        auto it = acct.positions.find(o.symbol);
        bool opening = it == acct.positions.end() || it->second.flat();
        if (opening && holding_count >= static_cast<int>(rule.p_limit)) {
          std::snprintf(buf, sizeof(buf), "[风控:%s] 已持有 %d 只，达到上限 %d",
                        rule.id.c_str(), holding_count, static_cast<int>(rule.p_limit));
          return buf;
        }
      }
    } else if (rule.id == "daily_loss_limit") {
      if (o.buy && daily_pnl <= -rule.p_limit) {
        std::snprintf(buf, sizeof(buf), "[风控:%s] 当日亏损 %.2f%% 触及熔断线 %.2f%%，暂停开仓",
                      rule.id.c_str(), daily_pnl * 100, -rule.p_limit * 100);
        return buf;
      }
    } else if (rule.id == "max_drawdown") {
      if (o.buy && drawdown >= rule.p_limit) {
        std::snprintf(buf, sizeof(buf), "[风控:%s] 当前回撤 %.2f%% 超过阈值 %.2f%%，停止开仓",
                      rule.id.c_str(), drawdown * 100, rule.p_limit * 100);
        return buf;
      }
    } else if (rule.id == "order_rate_limit") {
      double now_s = static_cast<double>(now_ns) / 1e9;
      while (!rate_ts.empty() && now_s - rate_ts.front() > rule.p_window) rate_ts.pop_front();
      if (static_cast<int>(rate_ts.size()) >= rule.p_max_orders) {
        std::snprintf(buf, sizeof(buf), "[风控:%s] %.0f 秒内已下单 %zu 次，超过上限 %d",
                      rule.id.c_str(), rule.p_window, rate_ts.size(), rule.p_max_orders);
        return buf;
      }
      rate_ts.push_back(now_s);
    } else if (rule.id == "blacklist") {
      for (const auto& s : rule.p_symbols)
        if (s == o.symbol) {
          std::snprintf(buf, sizeof(buf), "[风控:%s] %s 在黑名单中", rule.id.c_str(), o.symbol.c_str());
          return buf;
        }
    } else if (rule.id == "lot_size") {
      if (o.buy) {
        SymbolRules sr = rules_for(o.symbol);
        if (sr.lot_size > 1) {
          double rounded = std::floor(o.quantity / sr.lot_size) * sr.lot_size;
          if (std::abs(o.quantity - rounded) > 1e-9) {
            std::snprintf(buf, sizeof(buf), "[风控:%s] 买入数量 %s 不是 %d 的整数倍",
                          rule.id.c_str(), std::to_string(o.quantity).c_str(), sr.lot_size);
            return buf;
          }
        }
      }
    }
  }
  return "";
}

}  // namespace qcore
