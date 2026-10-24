// 轻量测试运行器（无依赖，assert 风格）。
#include <cmath>
#include <iostream>
#include <vector>

#include "registry.hpp"

int main() {
  int failed = 0;
  for (const auto& [name, fn] : all_tests()) {
    try {
      fn();
      std::cout << "PASS " << name << std::endl;
    } catch (const std::exception& exc) {
      std::cout << "FAIL " << name << ": " << exc.what() << std::endl;
      failed++;
    }
  }
  std::cout << (failed == 0 ? "ALL TESTS PASSED" : "FAILURES") << std::endl;
  return failed == 0 ? 0 : 1;
}
