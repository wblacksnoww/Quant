#include "ws_client.hpp"

#include <chrono>
#include <condition_variable>
#include <iostream>

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/ssl.hpp>
#include <boost/beast/websocket.hpp>

namespace gw {

namespace beast = boost::beast;
namespace net = boost::asio;
namespace ssl = boost::asio::ssl;
namespace websocket = beast::websocket;
using tcp = net::ip::tcp;

// 设计要点（延迟）：
// - 发送队列由专职写线程消费（条件变量唤醒），入队即写——下单延迟 µs 级，
//   不再依赖"下一条入站消息才排空"（私有频道安静时最坏要等 20s 心跳 pong）。
// - 读线程只管读；写线程与心跳线程经 write_mutex 串行化对同一 ws 的写。
struct WsClient::Impl {
  WsClient* owner;
  std::string host, port, path;
  MessageHandler handler;
  std::atomic<bool> running{false};
  std::atomic<bool> connected{false};
  int backoff_ms = 1000;

  std::mutex pending_mutex;
  std::condition_variable pending_cv;
  std::vector<std::string> pending;
  std::mutex write_mutex;  // ws 写串行化（写线程 / 心跳线程）

  Impl(WsClient* owner_, std::string h, std::string p, std::string path_, MessageHandler handler_)
      : owner(owner_), host(std::move(h)), port(std::move(p)), path(std::move(path_)),
        handler(std::move(handler_)) {}

  void queue_send(const std::string& text) {
    {
      std::lock_guard<std::mutex> lock(pending_mutex);
      pending.push_back(text);
    }
    pending_cv.notify_one();  // 立即唤醒写线程
  }

  void run() {
    while (running.load()) {
      try {
        connect_and_read();
      } catch (const std::exception& exc) {
        if (running.load()) std::cerr << "[ws] " << host << " 断开: " << exc.what() << std::endl;
      }
      connected.store(false);
      pending_cv.notify_all();  // 唤醒写线程退出
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

    // 写线程：条件变量唤醒，入队即写（订阅 / 下单都在这条路径上）
    std::thread writer([this, &ws] {
      while (connected.load() && running.load()) {
        std::unique_lock<std::mutex> lock(pending_mutex);
        pending_cv.wait(lock, [this] {
          return !pending.empty() || !connected.load() || !running.load();
        });
        if (!connected.load() || !running.load()) return;
        auto batch = std::move(pending);
        pending.clear();
        lock.unlock();
        for (const auto& text : batch) {
          std::lock_guard<std::mutex> wlock(write_mutex);
          beast::error_code ec;
          ws.write(net::const_buffer(text.data(), text.size()), ec);
          if (ec) return;
        }
      }
    });

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

    // 连接回调：重连后重订阅（经写线程立即发出）
    if (owner->on_connected) {
      auto sender = [this](const std::string& text) { queue_send(text); };
      owner->on_connected(sender);
    }

    beast::flat_buffer buffer;
    beast::error_code read_ec;
    while (running.load()) {
      beast::error_code ec;
      ws.read(buffer, ec);
      if (ec) {
        read_ec = ec;
        break;
      }
      std::string msg = beast::buffers_to_string(buffer.data());
      buffer.consume(buffer.size());
      if (msg == "pong") continue;
      if (handler) handler(msg);
    }

    connected.store(false);
    pending_cv.notify_all();  // 唤醒写线程退出
    if (writer.joinable()) writer.join();
    if (heartbeat.joinable()) heartbeat.join();
    if (read_ec) throw std::runtime_error(read_ec.message());
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
  impl_->connected.store(false);
  impl_->pending_cv.notify_all();
  if (thread_.joinable()) thread_.join();
}

void WsClient::send(const std::string& text) { impl_->queue_send(text); }

bool WsClient::connected() const { return impl_->connected.load(); }

}  // namespace gw
