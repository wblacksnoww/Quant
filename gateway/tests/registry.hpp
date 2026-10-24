#pragma once
#include <string>
#include <utility>
#include <vector>

using TestFn = void (*)();
inline std::vector<std::pair<const char*, TestFn>>& test_registry() {
  static std::vector<std::pair<const char*, TestFn>> registry;
  return registry;
}
struct TestRegistrar {
  TestRegistrar(const char* name, TestFn fn) { test_registry().emplace_back(name, fn); }
};
#define GW_TEST(name)                                        \
  static void name##_impl();                                 \
  static TestRegistrar name##_reg(#name, name##_impl);       \
  static void name##_impl()

inline const std::vector<std::pair<const char*, TestFn>>& all_tests() {
  return test_registry();
}
