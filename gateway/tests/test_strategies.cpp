#include "../src/tick_engine.hpp"
#include "registry.hpp"

#include <cmath>
#include <stdexcept>

using namespace gw;

namespace {

Tick make_tick(const std::string& symbol, double price, int64_t ts_ms) {
  return Tick{symbol, price, price, price, ts_ms * 1000000LL};
}

StrategyContext flat_ctx() { return StrategyContext{}; }

GW_TEST(grid_buys_on_downcross_and_sells_on_upcross) {
  json params = {{"step_pct", 0.01}, {"order_qty", 1.0}, {"max_inventory", 5.0}};
  GridStrategy grid(params);
  const std::string sym = "BTC-USDT";
  int64_t t = 1700000000000LL;

  // 首 tick 定锚 100
  auto intents = grid.on_tick(make_tick(sym, 100.0, t), flat_ctx());
  if (!intents.empty()) throw std::runtime_error("首 tick 不应交易");

  // 下穿一格 → buy
  StrategyContext holding;  holding.position_qty = 0.0;
  intents = grid.on_tick(make_tick(sym, 98.9, t + 1000), holding);
  if (intents.size() != 1 || intents[0].side != "buy" || intents[0].qty != 1.0)
    throw std::runtime_error("下穿一格应买入 1 份");

  // 再下穿 → 再买（但库存已达上限 5 之前允许）
  intents = grid.on_tick(make_tick(sym, 97.8, t + 2000), holding);
  if (intents.size() != 1 || intents[0].side != "buy")
    throw std::runtime_error("再下穿应继续买入");

  // 上穿 → 卖出（需要库存）
  StrategyContext has_position;  has_position.position_qty = 5.0;
  intents = grid.on_tick(make_tick(sym, 100.0, t + 3000), has_position);
  if (intents.size() != 1 || intents[0].side != "sell")
    throw std::runtime_error("上穿应卖出");
}

GW_TEST(grid_respects_inventory_cap) {
  json params = {{"step_pct", 0.01}, {"order_qty", 1.0}, {"max_inventory", 1.0}};
  GridStrategy grid(params);
  const std::string sym = "BTC-USDT";
  int64_t t = 1700000000000LL;
  grid.on_tick(make_tick(sym, 100.0, t), flat_ctx());

  StrategyContext full;  full.position_qty = 1.0;  // 已达上限
  auto intents = grid.on_tick(make_tick(sym, 98.9, t + 1000), full);
  if (!intents.empty()) throw std::runtime_error("库存达上限时下穿不应再买");
}

GW_TEST(grid_respects_bounds) {
  json params = {{"step_pct", 0.01}, {"order_qty", 1.0}, {"upper", 101.0}, {"lower", 99.0}};
  GridStrategy grid(params);
  const std::string sym = "BTC-USDT";
  int64_t t = 1700000000000LL;
  grid.on_tick(make_tick(sym, 100.0, t), flat_ctx());
  StrategyContext ctx = flat_ctx();
  // 区间外不交易
  auto intents = grid.on_tick(make_tick(sym, 101.5, t + 1000), ctx);
  if (!intents.empty()) throw std::runtime_error("区间上界之外不应交易");
  intents = grid.on_tick(make_tick(sym, 98.5, t + 2000), ctx);
  if (!intents.empty()) throw std::runtime_error("区间下界之外不应交易");
}

GW_TEST(breakout_fires_once_until_rearm) {
  json params = {{"upper", 110.0}, {"lower", 90.0}, {"order_qty", 2.0}, {"cooldown_s", 0}};
  BreakoutStrategy brk(params);
  const std::string sym = "BTC-USDT";
  int64_t t = 1700000000000LL;

  auto intents = brk.on_tick(make_tick(sym, 111.0, t), flat_ctx());
  if (intents.size() != 1 || intents[0].side != "buy")
    throw std::runtime_error("上破应买入");

  // 未回到内侧 → 不再触发
  intents = brk.on_tick(make_tick(sym, 112.0, t + 1000), flat_ctx());
  if (!intents.empty()) throw std::runtime_error("武装解除后不应重复触发");

  // 回到内侧 → 重新武装 → 再上破再触发
  brk.on_tick(make_tick(sym, 109.0, t + 2000), flat_ctx());
  intents = brk.on_tick(make_tick(sym, 111.0, t + 3000), flat_ctx());
  if (intents.size() != 1) throw std::runtime_error("重新武装后应再触发");
}

GW_TEST(breakout_sell_uses_position) {
  json params = {{"upper", 110.0}, {"lower", 90.0}, {"order_qty", 2.0}, {"cooldown_s", 0}};
  BreakoutStrategy brk(params);
  const std::string sym = "BTC-USDT";
  int64_t t = 1700000000000LL;

  StrategyContext holding;  holding.position_qty = 3.0;
  auto intents = brk.on_tick(make_tick(sym, 89.0, t), holding);
  if (intents.size() != 1 || intents[0].side != "sell" || intents[0].qty != 3.0)
    throw std::runtime_error("下破应清仓卖出全部持仓");
}

GW_TEST(momentum_enters_on_threshold) {
  json params = {{"window_s", 10}, {"entry_threshold", 0.01}, {"order_qty", 1.0},
                 {"cooldown_s", 0}, {"exit_threshold", 0.005}, {"exit_drawdown", 0.02}};
  TickMomentumStrategy mom(params);
  const std::string sym = "BTC-USDT";
  int64_t base = 1700000000000LL;

  // 100 → 101.5 (1.5% 动量) 触发买入
  StrategyContext ctx = flat_ctx();
  mom.on_tick(make_tick(sym, 100.0, base), ctx);
  auto intents = mom.on_tick(make_tick(sym, 101.5, base + 5000), ctx);
  if (intents.size() != 1 || intents[0].side != "buy")
    throw std::runtime_error("动量超阈值应买入");
}

GW_TEST(momentum_exits_on_reverse) {
  json params = {{"window_s", 10}, {"entry_threshold", 0.01}, {"order_qty", 1.0},
                 {"cooldown_s", 0}, {"exit_threshold", 0.005}, {"exit_drawdown", 0.02}};
  TickMomentumStrategy mom(params);
  const std::string sym = "BTC-USDT";
  int64_t base = 1700000000000LL;

  // 先建仓
  StrategyContext holding;  holding.position_qty = 1.0;  holding.avg_cost = 101.5;
  mom.on_tick(make_tick(sym, 100.0, base), holding);
  mom.on_tick(make_tick(sym, 101.5, base + 5000), holding);
  mom.on_tick(make_tick(sym, 101.0, base + 6000), holding);  // 平稳
  // 反向动量（-0.6% <= -0.5%）→ 平仓
  auto intents = mom.on_tick(make_tick(sym, 99.4, base + 9000), holding);
  if (intents.size() != 1 || intents[0].side != "sell")
    throw std::runtime_error("反向动量应平仓");
}

GW_TEST(momentum_cooldown_blocks) {
  json params = {{"window_s", 10}, {"entry_threshold", 0.01}, {"order_qty", 1.0},
                 {"cooldown_s", 60}, {"exit_threshold", 0.005}, {"exit_drawdown", 0.02}};
  TickMomentumStrategy mom(params);
  const std::string sym = "BTC-USDT";
  int64_t base = 1700000000000LL;
  StrategyContext ctx = flat_ctx();
  mom.on_tick(make_tick(sym, 100.0, base), ctx);
  auto intents = mom.on_tick(make_tick(sym, 101.5, base + 5000), ctx);
  if (intents.size() != 1) throw std::runtime_error("首次应触发");
  // 冷却期内反向也不触发
  intents = mom.on_tick(make_tick(sym, 99.0, base + 6000), ctx);
  if (!intents.empty()) throw std::runtime_error("冷却期内不应触发");
}

GW_TEST(bad_params_rejected) {
  bool threw = false;
  try {
    GridStrategy bad({{"step_pct", 0.7}, {"order_qty", 1.0}});
  } catch (const std::runtime_error&) {
    threw = true;
  }
  if (!threw) throw std::runtime_error("非法 step_pct 应抛错");
}

}  // namespace

namespace {

GW_TEST(bar_spec_enters_and_exits) {
  // spec：close > 50 买入 / close < 45 卖出（逐 bar 评估，活 bar 实时更新）
  json params = json::parse(R"({
    "spec": {
      "version": 1,
      "entry": [{"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 50.0}}],
      "exit":  [{"type": "lt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 45.0}}],
      "position_pct": 0.9, "stop_loss": 0.0, "take_profit": 0.0
    },
    "order_qty": 1.0
  })");
  BarSpecStrategy strat(params, 60);
  const std::string sym = "BTC-USDT";
  int64_t base = 1700000000000LL;  // ms

  StrategyContext flat;
  auto intents = strat.on_tick(make_tick(sym, 40.0, base), flat);
  if (!intents.empty()) throw std::runtime_error("新 bar 首 tick 不应交易");
  intents = strat.on_tick(make_tick(sym, 52.0, base + 30000), flat);
  if (intents.size() != 1 || intents[0].side != "buy")
    throw std::runtime_error("close>50 应产生买入信号");

  StrategyContext holding;
  holding.position_qty = 1.0;
  strat.on_tick(make_tick(sym, 44.0, base + 61000), holding);  // 新 bar 开盘
  intents = strat.on_tick(make_tick(sym, 43.0, base + 90000), holding);
  if (intents.size() != 1 || intents[0].side != "sell" || intents[0].qty != 1.0)
    throw std::runtime_error("close<45 应清仓");
}

GW_TEST(bar_spec_missing_spec_rejected) {
  bool threw = false;
  try {
    BarSpecStrategy bad({{"order_qty", 1.0}}, 60);
  } catch (const std::runtime_error&) {
    threw = true;
  }
  if (!threw) throw std::runtime_error("缺 spec 应抛错");
}

}  // namespace
