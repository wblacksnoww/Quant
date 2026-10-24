#include "ws_client.hpp"

#include <iostream>

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/ssl.hpp>
#include <boost/beast/websocket.hpp>
#include <boost/beast/websocket/ssl.hpp>

namespace gw {

namespace beast = boost::beast;
namespace net = boost::asio;
namespace ssl = boost::asio::ssl;
namespace websocket = beast::websocket;
using tcp = net::ip::tcp;

struct WsClient::Impl {
  WsClient* owner;
  std::string host, port, path;
  MessageHandler handler;
  std::atomic<bool> running{false};
  std::atomic<bool> connected{false};
  int backoff_ms = 1000;
  std::mutex pending_mutex;
  std::vector<std::string> pending;
  std::mutex write_mutex;  // ws 写串行化（读循环排空 / 心跳）

  Impl(WsClient* owner_, std::string h, std::string p, std::string path_, MessageHandler handler_)
      : owner(owner_), host(std::move(h)), port(std::move(p)), path(std::move(path_)),
        handler(std::move(handler_)) {}

  void queue_send(const std::string& text) {
    std::lock_guard<std::mutex> lock(pending_mutex);
    pending.push_back(text);
  }

  std::vector<std::string> drain_pending() {
    std::lock_guard<std::mutex> lock(pending_mutex);
    return std::move(pending);
  }

  void run() {
    while (running.load()) {
      try {
        connect_and_read();
      } catch (const std::exception& exc) {
        if (running.load()) std::cerr << "[ws] " << host << " 断开: " << exc.what() << std::endl;
      }
      connected.store(false);
      if (!running.load()) return;
      int delay = std::min(backoff_ms, 30000);
      backoff_ms *= 2;
      for (int i = 0; i < delay / 100 && running.load(); ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }

  void connect_and_read() {
    net::io_context ioc;
    ssl::context ssl_ctx(ssl::context::tls_client);
    tcp::resolver resolver(ioc);
    websocket::stream<ssl::stream<tcp::socket>> ws(ioc, ssl_ctx);

    auto const results = resolver.resolve(host, port);
    net::connect(ws.next_layer().next_layer(), results);
    ssl_ctx.set_verify_mode(ssl::verify_peer);
    ssl_ctx.set_default_verify_paths();
    ws.next_layer().handshake(ssl::stream_base::client);
    ws.handshake(host, path);

    connected.store(true);
    backoff_ms = 1000;
    std::cerr << "[ws] 已连接 " << host << path << std::endl;

    // 心跳：OKX 要求 <30s 一次文本 "ping"
    std::thread heartbeat([this, &ws] {
      while (connected.load() && running.load()) {
        for (int i = 0; i < 200 && connected.load() && running.load(); ++i)
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
        if (!connected.load() || !running.load()) return;
        try {
          std::lock_guard<std::mutex> lock(write_mutex);
          beast::error_code ec;
          ws.write(net::const_buffer("ping", 4), ec);
          if (ec) return;
        } catch (...) {
          return;
        }
      }
    });

    // 连接回调：重连后重订阅（订阅消息进队列，由本循环排空）
    if (owner->on_connected) {
      auto sender = [this](const std::string& text) { queue_send(text); };
      owner->on_connected(sender);
    }

    beast::flat_buffer buffer;
    while (running.load()) {
      // 排空待发消息（订阅 / 下单）
      for (const auto& text : drain_pending()) {
        std::lock_guard<std::mutex> lock(write_mutex);
        ws.write(net::const_buffer(text.data(), text.size()));
      }
      beast::error_code ec;
      ws.read(buffer, ec);
      if (ec) {
        if (heartbeat.joinable()) heartbeat.join();
        throw std::runtime_error(ec.message());
      }
      std::string msg = beast::buffers_to_string(buffer.data());
      buffer.consume(buffer.size());
      if (msg == "pong") continue;
      if (handler) handler(msg);
    }
    if (heartbeat.joinable()) heartbeat.join();
  }
};

WsClient::WsClient(std::string host, std::string port, std::string path, MessageHandler on_message)
    : impl_(std::make_unique<Impl>(this, std::move(host), std::move(port), std::move(path),
                                   std::move(on_message))) {}

WsClient::~WsClient() { stop(); }

void WsClient::start() {
  if (impl_->running.load()) return;
  impl_->running.store(true);
  thread_ = std::thread([this] { impl_->run(); });
}

void WsClient::stop() {
  impl_->running.store(false);
  if (thread_.joinable()) thread_.join();
  impl_->connected.store(false);
}

void WsClient::send(const std::string& text) { impl_->queue_send(text); }

bool WsClient::connected() const { return impl_->connected.load(); }

}  // namespace gw
