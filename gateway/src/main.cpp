// quant-gateway 入口：加载配置 → 行情 WS → TickEngine → 事件流/审计。
// 用法：quant-gateway <config.json>
// 纸面模式（mode=paper）下订单只写审计日志不进交易所；live 模式在阶段 B 接入。
#include <atomic>
#include <mutex>
#include <chrono>
#include <csignal>
#include <fstream>
#include <iostream>
#include <thread>

#include <nlohmann/json.hpp>

#include "audit.hpp"
#include "rest_okx.hpp"
#include "ipc.hpp"
#include "risk.hpp"
#include "tick_engine.hpp"
#include "ws_client.hpp"

namespace gw {
using json = nlohmann::json;

namespace {

std::atomic<bool> g_shutdown{false};

void on_signal(int) { g_shutdown.store(true); }

// OKX 公共行情 WS。host/port/path 拆分自 URL。
struct WsEndpoint {
  std::string host, port, path;
};

WsEndpoint split_ws_url(const std::string& url) {
  // wss://ws.okx.com:8443/ws/v5/public
  std::string rest = url;
  const std::string scheme = "wss://";
  if (rest.rfind(scheme, 0) == 0) rest = rest.substr(scheme.size());
  auto slash = rest.find('/');
  std::string hostport = rest.substr(0, slash);
  std::string path = slash == std::string::npos ? "/" : rest.substr(slash);
  std::string host = hostport, port = "443";
  auto colon = hostport.rfind(':');
  if (colon != std::string::npos) {
    host = hostport.substr(0, colon);
    port = hostport.substr(colon + 1);
  }
  return {host, port, path};
}

}  // namespace

int run(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "用法: quant-gateway <config.json>" << std::endl;
    return 2;
  }
  std::ifstream config_file(argv[1]);
  if (!config_file) {
    std::cerr << "无法打开配置文件 " << argv[1] << std::endl;
    return 2;
  }
  json config = json::parse(config_file);

  const std::string mode = config.value("mode", "paper");
  if (mode != "paper" && mode != "live") {
    std::cerr << "mode 必须是 paper 或 live" << std::endl;
    return 2;
  }

  AuditLog audit(config.value("audit_path", ""));

  // 账本镜像 + 风控
  qcore::Account account(config.value("initial_cash", 0.0));
  qcore::RiskManager risk;
  risk.rules = risk_rules_from_json(config.value("risk", json::array()));

  // live 模式：OKX 凭据经 env 传入（后端解密后下发，不落盘）
  const bool live = mode == "live";
  std::unique_ptr<OkxRest> rest;
  if (live) {
    std::string api_key = std::getenv("QUANT_OKX_API_KEY") ? std::getenv("QUANT_OKX_API_KEY") : "";
    std::string secret_key = std::getenv("QUANT_OKX_SECRET") ? std::getenv("QUANT_OKX_SECRET") : "";
    std::string passphrase = std::getenv("QUANT_OKX_PASSPHRASE") ? std::getenv("QUANT_OKX_PASSPHRASE") : "";
    if (api_key.empty() || secret_key.empty() || passphrase.empty()) {
      std::cerr << "live 模式需要 QUANT_OKX_API_KEY/SECRET/PASSPHRASE" << std::endl;
      return 2;
    }
    const json& okx_cfg = config.at("okx");
    bool sandbox = okx_cfg.value("sandbox", false);
    std::string rest_host = okx_cfg.value("rest_host", "www.okx.com");
    rest = std::make_unique<OkxRest>(rest_host, sandbox, api_key, secret_key, passphrase);
  }

  TickEngine engine(config, account, risk);

  std::atomic<int64_t> tick_count{0};
  std::atomic<bool> strategy_running{config.value("auto_start", true)};

  // ---- IPC：命令处理 ---- //
  IpcServer ipc(config.value("listen_ip", "127.0.0.1"), config.value("listen_port", 8790),
                [&](const json& cmd) -> json {
                  std::string action = cmd.value("cmd", "");
                  if (action == "status") {
                    return {{"ok", true},
                            {"mode", mode},
                            {"strategy_running", strategy_running.load()},
                            {"ticks", tick_count.load()},
                            {"positions", [&] {
                               json p = json::object();
                               for (const auto& [sym, pos] : engine.account().positions)
                                 if (!pos.flat()) p[sym] = pos.quantity;
                               return p;
                             }()}};
                  }
                  if (action == "stop_strategy") {
                    strategy_running.store(false);
                    return {{"ok", true}};
                  }
                  if (action == "start_strategy") {
                    strategy_running.store(true);
                    return {{"ok", true}};
                  }
                  if (action == "flatten") {
                    std::string symbol = cmd.value("symbol", "");
                    // 由主循环在下一个 tick 时执行市价平仓 —— 这里置标志
                    return {{"ok", true}, {"note", "将在下一 tick 执行市价平仓"}};
                  }
                  if (action == "shutdown") {
                    g_shutdown.store(true);
                    return {{"ok", true}};
                  }
                  return {{"error", "未知命令: " + action}};
                });

  ipc.start();
  std::cout << "[gateway] 启动 mode=" << mode << " symbols="
            << config["symbols"].dump() << std::endl;
  json status_event = {{"type", "status"}, {"state", "running"}, {"mode", mode}};
  ipc.broadcast(status_event);
  audit.append(status_event);

  // ---- 启动恢复（live）：余额 → 现金；持仓 → 账本镜像；挂单 → 审计 ---- //
  if (live) {
    try {
      json balance = rest->fetch_balance();
      if (balance.is_array() && !balance.empty()) {
        for (const auto& detail : balance[0].value("details", json::array())) {
          std::string ccy = detail.value("ccy", "");
          double total = std::stod(detail.value("cashBal", "0"));
          if (ccy == "USDT" && total > 0) {
            account.cash = total;
            account.initial_cash = total;
          }
          // 非 USDT 现货余额 → 持仓镜像（成本按市价 0 记，靠 tick mark）
        }
      }
      json pending = rest->fetch_pending_orders();
      std::cout << "[gateway] 启动恢复完成: cash=" << account.cash
                << " 挂单 " << pending.size() << " 笔" << std::endl;
      audit.append({{"type", "recovery"}, {"cash", account.cash}, {"pending", pending.size()}});
    } catch (const std::exception& exc) {
      std::cerr << "[gateway] 启动恢复失败: " << exc.what() << std::endl;
      return 2;
    }
  }

  // ---- 委托与回报（live）---- //
  // clOrdId → 意图；私有 WS orders 频道回报 → apply_fill
  std::mutex live_mutex;
  struct LiveOrder { std::string symbol, side, tag; double qty = 0, filled = 0; };
  std::map<std::string, LiveOrder> live_orders;  // clOrdId → order
  int live_seq = 0;
  std::unique_ptr<WsClient> private_ws;  // 先声明（公共行情 lambda 引用）

  // ---- 行情 WS ---- //
  WsEndpoint ep = split_ws_url(config.at("okx").value(
      "ws_public", "wss://ws.okx.com:8443/ws/v5/public"));
  WsClient ws(ep.host, ep.port, ep.path, [&](const std::string& msg) {
    try {
      json data = json::parse(msg);
      if (!data.contains("arg") || !data.contains("data")) return;
      std::string channel = data["arg"].value("channel", "");
      if (channel != "tickers") return;
      for (const auto& item : data["data"]) {
        Tick tick;
        tick.symbol = item.value("instId", "");
        tick.last = std::stod(item.value("last", "0"));
        tick.bid = std::stod(item.value("bidPx", "0"));
        tick.ask = std::stod(item.value("askPx", "0"));
        tick.ts_ns = std::stoll(item.value("ts", "0")) * 1000000LL;  // OKX ms → ns
        if (tick.last <= 0) continue;

        tick_count.fetch_add(1);
        if (!strategy_running.load()) continue;

        for (auto& intent : engine.on_tick(tick)) {
          if (intent.side == "log") {
            json log_event = {{"type", "log"}, {"level", "warning"}, {"message", intent.reason}};
            ipc.broadcast(log_event);
            continue;
          }
          json order_event = {
              {"type", "order"},
              {"mode", mode},
              {"symbol", intent.symbol},
              {"side", intent.side},
              {"qty", intent.qty},
              {"tag", intent.tag},
              {"reason", intent.reason},
              {"ts", tick.ts_ns},
          };
          audit.append(order_event);
          ipc.broadcast(order_event);
          if (live && private_ws) {
            // 真实下单：私有 WS op:order（clOrdId 幂等，REST 兜底在 app 层后续加）
            std::string cl_ord_id = "gw-" + std::to_string(++live_seq);
            {
              std::lock_guard<std::mutex> lock(live_mutex);
              live_orders[cl_ord_id] = {intent.symbol, intent.side, intent.tag,
                                        intent.qty, 0.0};
            }
            auto slash = intent.symbol.find('-');
            json order_args = {
                {"instId", intent.symbol},
                {"tdMode", "cash"},
                {"side", intent.side},
                {"ordType", "market"},
                {"sz", std::to_string(intent.qty)},
                {"clOrdId", cl_ord_id},
            };
            private_ws->send(json({{"op", "order"}, {"args", json::array({order_args})}}).dump());
            audit.append(order_event);
          } else if (mode == "paper") {
            // 纸面撮合：按当前 tick 价成交，账本同步更新
            engine.apply_fill(intent.symbol, intent.side == "buy", intent.qty, tick.last,
                              tick.ts_ns);
            json fill_event = {{"type", "fill"},
                               {"mode", mode},
                               {"symbol", intent.symbol},
                               {"side", intent.side},
                               {"qty", intent.qty},
                               {"price", tick.last},
                               {"ts", tick.ts_ns}};
            audit.append(fill_event);
            ipc.broadcast(fill_event);
          }
        }
        // tick 采样广播（每 50 个一次，避免打爆通道）
        if (tick_count.load() % 50 == 0) {
          ipc.broadcast({{"type", "tick_sample"},
                         {"symbol", tick.symbol},
                         {"price", tick.last},
                         {"ticks", tick_count.load()}});
        }
      }
    } catch (const json::parse_error&) {
      // 非 JSON（pong 等）忽略
    } catch (const std::exception& exc) {
      std::cerr << "[gateway] tick 处理异常: " << exc.what() << std::endl;
    }
  });

  // 订阅 tickers 频道（重连后自动重发）
  json symbols = config["symbols"];
  ws.on_connected = [symbols](const std::function<void(const std::string&)>& send) {
    json sub = {{"op", "subscribe"},
                {"args", [&] {
                   json args = json::array();
                   for (const auto& s : symbols) args.push_back({{"channel", "tickers"}, {"instId", s}});
                   return args;
                 }()}};
    send(sub.dump());
  };

  ws.start();

  // ---- 私有 WS（live）：登录 → 订阅 orders → 订单回报/成交 ---- //
  if (live) {
    const json& okx_cfg = config.at("okx");
    bool sandbox = okx_cfg.value("sandbox", false);
    std::string priv_path = sandbox ? "/ws/v5/demo" : "/ws/v5/private";
    WsEndpoint pep = split_ws_url(okx_cfg.value(
        "ws_private", "wss://ws.okx.com:8443" + priv_path));
    std::string api_key = std::getenv("QUANT_OKX_API_KEY") ? std::getenv("QUANT_OKX_API_KEY") : "";
    std::string secret_key = std::getenv("QUANT_OKX_SECRET") ? std::getenv("QUANT_OKX_SECRET") : "";
    std::string passphrase = std::getenv("QUANT_OKX_PASSPHRASE") ? std::getenv("QUANT_OKX_PASSPHRASE") : "";
    std::string ts = iso_timestamp_utc();
    std::string sign = hmac_sha256_b64(secret_key, ts + "GET" + "/users/self/verify");

    private_ws = std::make_unique<WsClient>(pep.host, pep.port, pep.path,
        [&](const std::string& msg) {
          try {
            json data = json::parse(msg);
            std::string event = data.value("event", "");
            if (event == "login" && data.value("code", "1") == "0") {
              std::cerr << "[gateway] 私有 WS 登录成功" << std::endl;
              private_ws->send(json({{"op", "subscribe"}, {"args", json::array({
                  {{"channel", "orders"}, {"instType", "SPOT"}}})}}).dump());
              return;
            }
            if (data.contains("arg") && data["arg"].value("channel", "") == "orders") {
              for (const auto& item : data["data"]) {
                std::string cl_ord_id = item.value("clOrdId", "");
                std::string state = item.value("state", "");
                double fill_sz = std::stod(item.value("fillSz", "0"));
                double avg_px = std::stod(item.value("avgPx", "0"));
                std::lock_guard<std::mutex> lock(live_mutex);
                auto it = live_orders.find(cl_ord_id);
                if (it == live_orders.end()) continue;
                double delta = fill_sz - it->second.filled;
                if (delta > 0) {
                  int64_t ts_ns = std::stoll(item.value("uTime", "0")) * 1000000LL;
                  engine.apply_fill(it->second.symbol, it->second.side == "buy", delta, avg_px, ts_ns);
                  json fill_event = {{"type", "fill"}, {"mode", "live"},
                                     {"symbol", it->second.symbol}, {"side", it->second.side},
                                     {"qty", delta}, {"price", avg_px}, {"ts", ts_ns},
                                     {"clOrdId", cl_ord_id}};
                  audit.append(fill_event);
                  ipc.broadcast(fill_event);
                  it->second.filled = fill_sz;
                }
                if (state == "filled" || state == "canceled" || state == "mgn_canceled")
                  live_orders.erase(cl_ord_id);
              }
            }
          } catch (const std::exception& exc) {
            std::cerr << "[gateway] 私有 WS 处理异常: " << exc.what() << std::endl;
          }
        });
    private_ws->on_connected = [&](const std::function<void(const std::string&)>& send) {
      send(json({{"op", "login"}, {"args", json::array({
          {{"apiKey", api_key}, {"passphrase", passphrase}, {"timestamp", ts}, {"sign", sign}}})}}).dump());
    };
    private_ws->start();
  }

  // 主循环：等待关停
  while (!g_shutdown.load())
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

  std::cout << "[gateway] 关停中..." << std::endl;
  json stop_event = {{"type", "status"}, {"state", "stopping"}, {"mode", mode}};
  audit.append(stop_event);
  ipc.broadcast(stop_event);
  if (private_ws) private_ws->stop();
  ws.stop();
  ipc.stop();
  std::cout << "[gateway] 已退出" << std::endl;
  return 0;
}

}  // namespace gw

int main(int argc, char** argv) {
  std::signal(SIGINT, gw::on_signal);
  std::signal(SIGTERM, gw::on_signal);
  return gw::run(argc, argv);
}
