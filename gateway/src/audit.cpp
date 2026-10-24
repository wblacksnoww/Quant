#include "audit.hpp"

#include <cstdio>

namespace gw {

AuditLog::AuditLog(const std::string& path) : path_(path) {
  if (path_.empty()) return;
  file_ = std::fopen(path_.c_str(), "a");
}

AuditLog::~AuditLog() {
  if (file_ != nullptr) std::fclose(file_);
}

void AuditLog::append(const json& event) {
  if (file_ == nullptr) return;
  std::lock_guard<std::mutex> lock(mutex_);
  std::string line = event.dump();
  line.push_back('\n');
  std::fwrite(line.data(), 1, line.size(), file_);
  std::fflush(file_);
}

}  // namespace gw
