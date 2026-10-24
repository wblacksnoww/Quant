#include "risk.hpp"

namespace gw {

using qcore::RiskRuleCfg;

std::vector<RiskRuleCfg> risk_rules_from_json(const json& rules) {
  std::vector<RiskRuleCfg> out;
  if (!rules.is_array()) return out;
  for (const auto& entry : rules) {
    RiskRuleCfg cfg;
    if (!entry.contains("id")) continue;
    cfg.id = entry.value("id", "");
    cfg.enabled = entry.value("enabled", true);
    cfg.p_limit = entry.value("limit", 0.0);
    cfg.p_max_orders = static_cast<int>(entry.value("max_orders", 0.0));
    cfg.p_window = entry.value("window_seconds", 60.0);
    if (entry.contains("symbols") && entry["symbols"].is_array())
      for (const auto& s : entry["symbols"]) cfg.p_symbols.push_back(s.get<std::string>());
    out.push_back(cfg);
  }
  return out;
}

}  // namespace gw
