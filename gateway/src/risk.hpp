// 风控规则 JSON → qcore RiskManager 的桥接。
#pragma once

#include <nlohmann/json.hpp>
#include <vector>

#include "qcore.hpp"

namespace gw {

using json = nlohmann::json;

// rules: [{"id":"max_position_pct","enabled":true,"limit":0.98,"max_orders":120,
//          "window_seconds":60,"symbols":[...]}]
std::vector<qcore::RiskRuleCfg> risk_rules_from_json(const json& rules);

}  // namespace gw
