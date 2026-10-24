// pybind11 壳：py::dict → json 转换与 SpecEvaluator 供 _core 扩展使用。
#include "strategy_py.hpp"

#include <pybind11/eval.h>

namespace qcore {

// py::dict → nlohmann::json：经 Python 标准库 json.dumps（避免手写类型分支的
// handle 递归转换在 pybind 各版本间的行为差异）
CompiledSpec compile_spec_py(const py::dict& spec) {
  py::object dumps = py::module_::import("json").attr("dumps");
  std::string text = py::cast<std::string>(dumps(spec));
  return compile_spec(json::parse(text));
}

}  // namespace qcore
