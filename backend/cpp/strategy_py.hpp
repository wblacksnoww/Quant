// pybind11 壳：SpecEvaluator（模拟盘/实盘引擎从 Python 调用拼装策略求值）。
// 与 strategy.hpp 分离是为了让网关（无 Python）也能复用 spec 编译与求值。
#pragma once

#include <pybind11/pybind11.h>

#include "strategy.hpp"

namespace py = pybind11;

namespace qcore {

CompiledSpec compile_spec_py(const py::dict& spec);

class SpecEvaluator {
 public:
  explicit SpecEvaluator(const py::dict& spec) : spec_(compile_spec_py(spec)), state_(spec_) {}

  std::pair<bool, bool> step(size_t cursor, const Columns& cols) {
    return state_.step_idempotent(cursor, cols);
  }

 private:
  CompiledSpec spec_;
  SpecState state_;
};

}  // namespace qcore
