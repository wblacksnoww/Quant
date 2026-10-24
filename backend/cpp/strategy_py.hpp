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

  // 窗口重喂：reset 后评估 [cursor-window+1, cursor]，与 Python
  // history(symbol, min_rows) 滑窗口径完全一致（ewm 类指标需要）。
  std::pair<bool, bool> evaluate_window(size_t cursor, size_t window, const Columns& cols) {
    state_.reset();
    size_t begin = window > cursor ? 0 : cursor - window + 1;
    std::pair<bool, bool> out{false, false};
    for (size_t i = begin; i <= cursor; ++i) out = state_.step(i, cols);
    return out;
  }

 private:
  CompiledSpec spec_;
  SpecState state_;
};

}  // namespace qcore
