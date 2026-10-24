#include "rest_okx.hpp"

#include <openssl/hmac.h>

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <boost/beast/ssl.hpp>

#include <chrono>
#include <cstring>
#include <stdexcept>

namespace gw {

namespace beast = boost::beast;
namespace net = boost::asio;
namespace ssl = boost::asio::ssl;
namespace http = beast::http;
using tcp = net::ip::tcp;

std::string hmac_sha256_b64(const std::string& key, const std::string& data) {
  unsigned char digest[EVP_MAX_MD_SIZE];
  unsigned int len = 0;
  HMAC(EVP_sha256(), key.data(), static_cast<int>(key.size()),
       reinterpret_cast<const unsigned char*>(data.data()), data.size(), digest, &len);
  // base64
  static const char* table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  out.reserve((len + 2) / 3 * 4);
  for (size_t i = 0; i < len; i += 3) {
    unsigned b0 = digest[i], b1 = i + 1 < len ? digest[i + 1] : 0, b2 = i + 2 < len ? digest[i + 2] : 0;
    out += table[b0 >> 2];
    out += table[((b0 & 3) << 4) | (b1 >> 4)];
    out += (i + 1 < len) ? table[((b1 & 15) << 2) | (b2 >> 6)] : '=';
    out += (i + 2 < len) ? table[b2 & 63] : '=';
  }
  return out;
}

std::string iso_timestamp_utc() {
  auto now = std::chrono::system_clock::now();
  std::time_t t = std::chrono::system_clock::to_time_t(now);
  std::tm tm{};
  gmtime_r(&t, &tm);
  char buf[40];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return buf;
}

OkxRest::OkxRest(std::string host, bool sandbox, std::string api_key, std::string secret_key,
                 std::string passphrase)
    : host_(std::move(host)), sandbox_(sandbox), api_key_(std::move(api_key)),
      secret_key_(std::move(secret_key)), passphrase_(std::move(passphrase)) {}

json OkxRest::request(const std::string& method, const std::string& path, const json* body) {
  net::io_context ioc;
  ssl::context ssl_ctx(ssl::context::tls_client);
  tcp::resolver resolver(ioc);
  ssl::stream<tcp::socket> stream(ioc, ssl_ctx);
  auto const results = resolver.resolve(host_, "443");
  net::connect(stream.next_layer(), results);
  ssl_ctx.set_verify_mode(ssl::verify_peer);
  ssl_ctx.set_default_verify_paths();
  stream.handshake(ssl::stream_base::client);

  std::string body_str = body ? body->dump() : "";
  std::string target = path + (method == "GET" && body_str.empty() ? "" : "");
  http::verb verb = method == "GET" ? http::verb::get : http::verb::post;
  http::request<http::string_body> req{verb, target, 11};
  req.set(http::field::host, host_);
  req.set(http::field::user_agent, "quant-gateway/1.0");
  req.set(http::field::content_type, "application/json");
  if (!api_key_.empty()) {
    std::string ts = iso_timestamp_utc();
    std::string sign_data = ts + method + path + body_str;
    req.set("OK-ACCESS-KEY", api_key_);
    req.set("OK-ACCESS-SIGN", hmac_sha256_b64(secret_key_, sign_data));
    req.set("OK-ACCESS-TIMESTAMP", ts);
    req.set("OK-ACCESS-PASSPHRASE", passphrase_);
  }
  if (sandbox_) req.set("x-simulated-trading", "1");
  if (body) {
    req.set(http::field::content_length, std::to_string(body_str.size()));
    req.body() = body_str;
  }
  http::write(stream, req);

  beast::flat_buffer buffer;
  http::response<http::string_body> resp;
  beast::error_code ec;
  http::read(stream, buffer, resp, ec);
  beast::error_code shutdown_ec;
  stream.shutdown(shutdown_ec);
  if (ec) throw std::runtime_error("REST 请求失败: " + ec.message());
  if (resp.result_int() != 200) throw std::runtime_error("REST HTTP " + std::to_string(resp.result_int()));
  return json::parse(resp.body());
}

json OkxRest::unwrap(const json& resp) {
  if (resp.value("code", "1") != "0") {
    std::string msg = "OKX API 错误 " + resp.value("code", "?");
    if (resp.contains("data") && resp["data"].is_array() && !resp["data"].empty()) {
      const auto& first = resp["data"][0];
      if (first.contains("sMsg")) msg += ": " + first["sMsg"].get<std::string>();
    } else if (resp.contains("msg")) {
      msg += ": " + resp["msg"].get<std::string>();
    }
    throw std::runtime_error(msg);
  }
  return resp.value("data", json::array());
}

json OkxRest::get(const std::string& path) { return request("GET", path, nullptr); }
json OkxRest::post(const std::string& path, const json& body) { return request("POST", path, &body); }

json OkxRest::fetch_balance() { return unwrap(get("/api/v5/account/balance")); }
json OkxRest::fetch_pending_orders() { return unwrap(get("/api/v5/trade/orders-pending")); }
json OkxRest::place_order(const json& args) { return unwrap(post("/api/v5/trade/order", args)); }
json OkxRest::cancel_order(const std::string& inst_id, const std::string& ord_id) {
  return unwrap(post("/api/v5/trade/cancel-order",
                     {{"instId", inst_id}, {"ordId", ord_id}}));
}

}  // namespace gw
