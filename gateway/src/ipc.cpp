#include "ipc.hpp"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstring>
#include <stdexcept>

namespace gw {

namespace {

bool send_all(int fd, const char* data, size_t len) {
  size_t sent = 0;
  while (sent < len) {
    ssize_t n = ::send(fd, data + sent, len - sent, MSG_NOSIGNAL);
    if (n <= 0) return false;
    sent += static_cast<size_t>(n);
  }
  return true;
}

bool send_frame(int fd, const std::string& payload) {
  uint32_t len = static_cast<uint32_t>(payload.size());
  char header[4];
  std::memcpy(header, &len, 4);  // 小端长度前缀
  if (!send_all(fd, header, 4)) return false;
  return send_all(fd, payload.data(), payload.size());
}

// 返回 false 表示连接已断开
bool read_frame(int fd, std::string& payload) {
  char header[4];
  size_t got = 0;
  while (got < 4) {
    ssize_t n = ::recv(fd, header + got, 4 - got, 0);
    if (n <= 0) return false;
    got += static_cast<size_t>(n);
  }
  uint32_t len;
  std::memcpy(&len, header, 4);
  if (len == 0 || len > 16 * 1024 * 1024) return false;  // 单帧上限 16MB
  payload.resize(len);
  got = 0;
  while (got < len) {
    ssize_t n = ::recv(fd, payload.data() + got, len - got, 0);
    if (n <= 0) return false;
    got += static_cast<size_t>(n);
  }
  return true;
}

}  // namespace

IpcServer::IpcServer(const std::string& bind_ip, int port, CommandHandler on_command)
    : bind_ip_(bind_ip), port_(port), on_command_(std::move(on_command)) {}

IpcServer::~IpcServer() { stop(); }

void IpcServer::start() {
  listen_fd_ = ::socket(AF_INET, SOCK_STREAM, 0);
  if (listen_fd_ < 0) throw std::runtime_error("socket() failed");
  int reuse = 1;
  ::setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port_));
  if (::inet_pton(AF_INET, bind_ip_.c_str(), &addr.sin_addr) != 1)
    throw std::runtime_error("非法绑定地址: " + bind_ip_);
  if (::bind(listen_fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0)
    throw std::runtime_error("bind 失败: " + bind_ip_ + ":" + std::to_string(port_));
  if (::listen(listen_fd_, 4) < 0) throw std::runtime_error("listen 失败");
  running_ = true;
  threads_.emplace_back([this] { accept_loop(); });
}

void IpcServer::stop() {
  running_ = false;
  if (listen_fd_ >= 0) {
    ::shutdown(listen_fd_, SHUT_RDWR);
    ::close(listen_fd_);
    listen_fd_ = -1;
  }
  {
    // 唤醒所有客户端读线程（shutdown 不 close，fd 关闭统一由 drop_client 完成）
    std::lock_guard<std::mutex> lock(clients_mutex_);
    for (auto& [fd, th] : clients_) ::shutdown(fd, SHUT_RDWR);
  }
  // accept 线程退出
  for (auto& th : threads_)
    if (th.joinable()) th.join();
  threads_.clear();
  // 客户端线程为 detached，退出后自行清理；此处仅清表
  std::lock_guard<std::mutex> lock(clients_mutex_);
  clients_.clear();
}

void IpcServer::accept_loop() {
  while (running_) {
    sockaddr_in peer{};
    socklen_t len = sizeof(peer);
    int fd = ::accept(listen_fd_, reinterpret_cast<sockaddr*>(&peer), &len);
    if (fd < 0) {
      if (!running_) return;
      continue;
    }
    if (ntohl(peer.sin_addr.s_addr) != INADDR_LOOPBACK) {
      ::close(fd);  // 只允许本机回环
      continue;
    }
    std::lock_guard<std::mutex> lock(clients_mutex_);
    clients_[fd] = std::thread([this, fd] { client_loop(fd); });
    clients_[fd].detach();  // 生命周期由 drop_client 收尾
  }
}

void IpcServer::client_loop(int fd) {
  std::string payload;
  while (running_) {
    if (!read_frame(fd, payload)) break;
    json reply;
    try {
      json cmd = json::parse(payload);
      reply = on_command_ ? on_command_(cmd) : json({{"error", "no handler"}});
    } catch (const json::parse_error& exc) {
      reply = {{"error", std::string("JSON 解析失败: ") + exc.what()}};
    } catch (const std::exception& exc) {
      reply = {{"error", exc.what()}};
    }
    if (!send_frame(fd, reply.dump())) break;
  }
  drop_client(fd);
}

void IpcServer::send_to(int fd, const json& event) {
  std::lock_guard<std::mutex> lock(clients_mutex_);
  auto it = clients_.find(fd);
  if (it != clients_.end() && !send_frame(fd, event.dump())) drop_client(fd);
}

void IpcServer::broadcast(const json& event) {
  std::string payload = event.dump();
  std::vector<int> dead;
  {
    std::lock_guard<std::mutex> lock(clients_mutex_);
    for (auto& [fd, th] : clients_)
      if (!send_frame(fd, payload)) dead.push_back(fd);
  }
  for (int fd : dead) drop_client(fd);  // 锁外关闭，避免死锁
}

void IpcServer::drop_client(int fd) {
  std::lock_guard<std::mutex> lock(clients_mutex_);
  auto it = clients_.find(fd);
  if (it != clients_.end()) {
    ::shutdown(fd, SHUT_RDWR);
    ::close(fd);
    clients_.erase(it);
  }
}

}  // namespace gw
