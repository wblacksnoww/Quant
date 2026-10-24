// pybind11 壳：py::dict → json 转换与 SpecEvaluator 供 _core 扩展使用。
#include "strategy_py.hpp"

namespace qcore {

namespace {

// py::dict → nlohmann::json（compile_spec 的 pybind 壳用）
json py_to_json(const py::handle& obj) {
  if (obj.is_none()) return nullptr;
  if (py::isinstance<py::bool_>(obj)) return obj.cast<bool>();
  if (py::isinstance<py::int_>(obj)) return static_cast<double>(obj.cast<long long>());
  if (py::isinstance<py::float_>(obj)) return obj.cast<double>();
  if (py::isinstance<py::str>(obj)) return obj.cast<std::string>();
  if (py::isinstance<py::list>(obj)) {
    json arr = json::array();
    for (auto item : py::cast<py::list>(obj)) arr.push_back(py_to_json(item));
    return arr;
  }
  if (py::isinstance<py::dict>(obj)) {
    json obj_out = json::object();
    for (auto item : py::cast<py::dict>(obj))
      obj_out[py::cast<std::string>(item.first)] = py_to_json(item.second);
    return obj_out;
  }
  return nullptr;
}

}  // namespace


}  // namespace

CompiledSpec compile_spec_py(const py::dict& spec) { return compile_spec(py_to_json(spec)); }

}  // namespace qcore
