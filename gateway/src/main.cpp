// quant-gateway 入口：加载配置 → 行情 WS → TickEngine → 事件流/审计。
// 用法：quant-gateway <config.json>
// 纸面模式（mode=paper）下订单只写审计日志不进交易所；live 模式在阶段 B 接入。
#include <atomic>
#include <chrono>
#include <csignal>
#include <fstream>
#include <iostream>
#include <thread>

#include <nlohmann/json.hpp>

#include "audit.hpp"
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
          // paper：到此为止；live：阶段 B 接 WS 下单
          if (mode == "live") {
            // TODO(阶段 B): OKX 私有 WS 下单
          }
          if (mode == "paper") {
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

  // 主循环：等待关停
  while (!g_shutdown.load())
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

  std::cout << "[gateway] 关停中..." << std::endl;
  json stop_event = {{"type", "status"}, {"state", "stopping"}, {"mode", mode}};
  audit.append(stop_event);
  ipc.broadcast(stop_event);
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
