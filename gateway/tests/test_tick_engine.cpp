#include "../src/risk.hpp"
#include "../src/tick_engine.hpp"
#include "registry.hpp"

#include <iostream>
#include <stdexcept>

using namespace gw;
using qcore::Account;
using qcore::RiskManager;

namespace {

json base_config() {
  return {
      {"symbols", json::array({"BTC-USDT"})},
      {"bar_interval_sec", 60},
      {"strategy", {{"type", "grid"},
                     {"params", {{"step_pct", 0.01}, {"order_qty", 1.0}, {"max_inventory", 100.0}}}}},
      {"risk", json::array({
                   {{"id", "max_position_pct"}, {"enabled", true}, {"limit", 0.05}},
               })},
  };
}

Tick tick(double price, int64_t ts_ms) {
  return Tick{"BTC-USDT", price, price, price, ts_ms * 1000000LL};
}

GW_TEST(risk_blocks_oversized_position) {
  auto config = base_config();
  Account account(100000.0);
  RiskManager risk;
  risk.rules = risk_rules_from_json(config["risk"]);
  TickEngine engine(config, account, risk);

  int64_t t = 1700000000000LL;
  // max_position_pct=0.05 → 权益 10 万 → 目标仓位上限 5000；价格 100 → 50 份即超
  auto intents = engine.on_tick(tick(100.0, t));
  // 第一 tick 只定锚不交易
  if (!intents.empty()) throw std::runtime_error("首 tick 只定锚");
  intents = engine.on_tick(tick(98.9, t + 1000));
  // 网格想买 1 份 (100 USDT) —— 仓位 100/100000 = 0.1% < 5% → 允许
  if (intents.size() != 1 || intents[0].side != "buy")
    throw std::runtime_error("小额买入应放行");

  // 模拟已有大仓位 → 风控应拦截
  engine.apply_fill("BTC-USDT", true, 55.0, 100.0, t + 1500);  // 5500 → 5.5% > 5%
  intents = engine.on_tick(tick(97.8, t + 2000));
  bool blocked = false;
  for (const auto& intent : intents) {
    if (intent.side == "log" && intent.reason.find("max_position_pct") != std::string::npos)
      blocked = true;
  }
  if (!blocked) throw std::runtime_error("超仓位买入应被风控拦截");
}

GW_TEST(bar_aggregation) {
  auto config = base_config();
  config["bar_interval_sec"] = 60;
  Account account(100000.0);
  RiskManager risk;
  risk.rules = risk_rules_from_json(config["risk"]);
  TickEngine engine(config, account, risk);

  int64_t base = 1700000000000LL;  // 整分对齐无所谓，bar 按分钟取模
  // 同一分钟内的多个 tick 只更新 bar
  engine.on_tick(tick(100.0, base));
  engine.on_tick(tick(101.0, base + 10000));
  engine.on_tick(tick(99.5, base + 20000));
  // 无法直接读 bar（内部状态），通过不崩溃 + 交易行为间接验证
  // 下一分钟 → 新 bar
  engine.on_tick(tick(100.5, base + 61000));
}

GW_TEST(fill_updates_mirror) {
  auto config = base_config();
  Account account(10000.0);
  RiskManager risk;
  risk.rules = risk_rules_from_json(config["risk"]);
  TickEngine engine(config, account, risk);

  engine.apply_fill("BTC-USDT", true, 2.0, 100.0, 1700000000000LL);
  const auto& pos = engine.account().positions.at("CRYPTO:BTC/USDT");
  if (std::abs(pos.quantity - 2.0) > 1e-9) throw std::runtime_error("买入持仓数量错误");
  if (std::abs(pos.avg_cost - 100.0) > 1e-9) throw std::runtime_error("成本价错误");

  engine.apply_fill("BTC-USDT", false, 1.0, 110.0, 1700000001000LL);
  if (std::abs(pos.quantity - 1.0) > 1e-9) throw std::runtime_error("卖出后数量错误");
  if (std::abs(pos.realized_pnl - 10.0) > 1e-9) throw std::runtime_error("已实现盈亏错误");
}

}  // namespace
