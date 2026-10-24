// OKX REST 客户端（启动恢复 / 对账 / 下单兜底）。
// 签名：OK-ACCESS-SIGN = base64(HMAC-SHA256(secret, ts + METHOD + requestPath + body))。
// 测试网：REST 头 x-simulated-trading: 1。
#pragma once

#include <string>

#include <nlohmann/json.hpp>

namespace gw {

using json = nlohmann::json;

class OkxRest {
 public:
  OkxRest(std::string host, bool sandbox, std::string api_key, std::string secret_key,
          std::string passphrase);

  // 已签名请求（GET/POST）
  json get(const std::string& path);
  json post(const std::string& path, const json& body);

  // 便捷封装（返回 data 数组；失败抛 std::runtime_error）
  json fetch_balance();                                   // /api/v5/account/balance
  json fetch_pending_orders();                            // /api/v5/trade/orders-pending
  json place_order(const json& args);                     // /api/v5/trade/order
  json cancel_order(const std::string& inst_id, const std::string& ord_id);

 private:
  json request(const std::string& method, const std::string& path, const json* body);
  json unwrap(const json& resp);

  std::string host_;
  bool sandbox_;
  std::string api_key_, secret_key_, passphrase_;
};

std::string hmac_sha256_b64(const std::string& key, const std::string& data);
std::string iso_timestamp_utc();

}  // namespace gw
