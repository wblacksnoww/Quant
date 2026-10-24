// 本地 TCP JSON 通道：事件流（网关→Python）+ 命令（Python→网关）。
// 127.0.0.1 only；长度前缀（4 字节小端）+ JSON。
#pragma once

#include <atomic>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

namespace gw {

using json = nlohmann::json;

class IpcServer {
 public:
  using CommandHandler = std::function<json(const json&)>;

  IpcServer(const std::string& bind_ip, int port, CommandHandler on_command);
  ~IpcServer();

  void start();
  void stop();
  void broadcast(const json& event);   // 发给所有已连接客户端
  bool running() const { return running_; }

 private:
  void accept_loop();
  void client_loop(int fd);
  void send_to(int fd, const json& event);
  void drop_client(int fd);

  std::string bind_ip_;
  int port_;
  CommandHandler on_command_;
  int listen_fd_ = -1;
  std::atomic<bool> running_{false};
  std::mutex clients_mutex_;
  std::map<int, std::thread> clients_;
  std::vector<std::thread> threads_;
};

}  // namespace gw
