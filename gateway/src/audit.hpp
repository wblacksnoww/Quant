// JSONL 审计日志：订单/成交逐行追加，崩溃安全（行级写入）。
#pragma once

#include <mutex>
#include <string>

#include <nlohmann/json.hpp>

namespace gw {

using json = nlohmann::json;

class AuditLog {
 public:
  explicit AuditLog(const std::string& path);
  ~AuditLog();
  void append(const json& event);
  bool enabled() const { return file_ != nullptr; }

 private:
  std::string path_;
  std::FILE* file_ = nullptr;
  std::mutex mutex_;
};

}  // namespace gw
