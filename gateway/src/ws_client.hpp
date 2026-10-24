// OKX WebSocket 客户端：公共行情 / 私有下单通用。
// Beast 阻塞读循环（每连接一线程）+ 心跳线程 + 指数退避重连 + 重连后重订阅。
// 发送采用待发队列：send() 线程安全，在读循环每次阻塞前清空（活动标的消息间隔毫秒级）。
#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace gw {

class WsClient {
 public:
  using MessageHandler = std::function<void(const std::string&)>;
  // 连接建立（含重连）后回调一次，参数为发送函数（订阅消息用它发出）
  using ConnectedHandler = std::function<void(const std::function<void(const std::string&)>&)>;

  WsClient(std::string host, std::string port, std::string path, MessageHandler on_message);
  ~WsClient();

  void start();
  void stop();
  void send(const std::string& text);  // 线程安全；在下一次读循环前排空
  bool connected() const;

  ConnectedHandler on_connected;

 private:
  friend struct Impl;
  struct Impl;
  std::unique_ptr<Impl> impl_;
  std::thread thread_;
};

}  // namespace gw
