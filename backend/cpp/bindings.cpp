#include "strategy_py.hpp"
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
namespace qcore {

py::dict run_backtest_native(
    std::vector<std::string> symbols, std::string bar_interval, int64_t start_ns, int64_t end_ns,
    double initial_cash, double risk_free_rate, std::map<std::string, double> cost_dict,
    bool stamp_both_sides, double volume_limit_pct, py::list risk_rules, std::string strategy_id,
    py::dict params,
    std::vector<py::array_t<int64_t, py::array::c_style | py::array::forcecast>> dates,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> opens,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> highs,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> lows,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> closes,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> volumes,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> amounts,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> prev_closes,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> limit_ups,
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> limit_downs,
    std::vector<std::map<std::string, py::array_t<double, py::array::c_style | py::array::forcecast>>> extras,
    py::function progress);

namespace {

// numpy 列 → Columns（指针直接指向 numpy 缓冲，调用期间保持引用即可）
Columns columns_from_py(size_t n,
                       const py::array_t<double, py::array::c_style | py::array::forcecast>& open,
                       const py::array_t<double, py::array::c_style | py::array::forcecast>& high,
                       const py::array_t<double, py::array::c_style | py::array::forcecast>& low,
                       const py::array_t<double, py::array::c_style | py::array::forcecast>& close,
                       const py::array_t<double, py::array::c_style | py::array::forcecast>& volume,
                       const py::array_t<double, py::array::c_style | py::array::forcecast>& amount,
                       const py::dict& extras) {
  Columns cols;
  cols.n = n;
  cols.open = open.size() ? open.data() : nullptr;
  cols.high = high.size() ? high.data() : nullptr;
  cols.low = low.size() ? low.data() : nullptr;
  cols.close = close.size() ? close.data() : nullptr;
  cols.volume = volume.size() ? volume.data() : nullptr;
  cols.amount = amount.size() ? amount.data() : nullptr;
  for (auto item : extras) {
    std::string key = py::cast<std::string>(item.first);
    auto arr = py::cast<py::array_t<double, py::array::c_style | py::array::forcecast>>(item.second);
    if (arr.size()) cols.extra[key] = arr.data();
  }
  return cols;
}

}  // namespace

PYBIND11_MODULE(_core, m) {
  m.doc() = "Quant Terminal C++ 加速核心（回测热路径）";
  py::class_<SpecEvaluator>(m, "SpecEvaluator")
      .def(py::init([](const py::dict& spec) { return new SpecEvaluator(spec); }))
      .def(
          "step",
          [](SpecEvaluator& self, size_t cursor, size_t n,
             const py::array_t<double, py::array::c_style | py::array::forcecast>& open,
             const py::array_t<double, py::array::c_style | py::array::forcecast>& high,
             const py::array_t<double, py::array::c_style | py::array::forcecast>& low,
             const py::array_t<double, py::array::c_style | py::array::forcecast>& close,
             const py::array_t<double, py::array::c_style | py::array::forcecast>& volume,
             const py::array_t<double, py::array::c_style | py::array::forcecast>& amount,
             const py::dict& extras) {
            Columns cols = columns_from_py(n, open, high, low, close, volume, amount, extras);
            return self.step(cursor, cols);
          },
          py::arg("cursor"), py::arg("n"), py::arg("open"), py::arg("high"), py::arg("low"),
          py::arg("close"), py::arg("volume"), py::arg("amount"), py::arg("extras") = py::dict())
        .def(
            "evaluate_window",
            [](SpecEvaluator& self, size_t cursor, size_t window, size_t n,
               const py::array_t<double, py::array::c_style | py::array::forcecast>& open,
               const py::array_t<double, py::array::c_style | py::array::forcecast>& high,
               const py::array_t<double, py::array::c_style | py::array::forcecast>& low,
               const py::array_t<double, py::array::c_style | py::array::forcecast>& close,
               const py::array_t<double, py::array::c_style | py::array::forcecast>& volume,
               const py::array_t<double, py::array::c_style | py::array::forcecast>& amount,
               const py::dict& extras) {
              Columns cols = columns_from_py(n, open, high, low, close, volume, amount, extras);
              return self.evaluate_window(cursor, window, cols);
            },
            py::arg("cursor"), py::arg("window"), py::arg("n"), py::arg("open"), py::arg("high"),
            py::arg("low"), py::arg("close"), py::arg("volume"), py::arg("amount"),
            py::arg("extras") = py::dict());

  m.def("run_backtest", &run_backtest_native,
        "C++ 回测：与 Python 引擎逐点等价",
        py::arg("symbols"), py::arg("bar_interval"), py::arg("start_ns"), py::arg("end_ns"),
        py::arg("initial_cash"), py::arg("risk_free_rate"), py::arg("cost"), py::arg("stamp_both_sides"),
        py::arg("volume_limit_pct"), py::arg("risk_rules"), py::arg("strategy_id"), py::arg("params"),
        py::arg("dates"), py::arg("opens"), py::arg("highs"), py::arg("lows"), py::arg("closes"),
        py::arg("volumes"), py::arg("amounts"), py::arg("prev_closes"), py::arg("limit_ups"),
        py::arg("limit_downs"), py::arg("extras"), py::arg("progress"));
}

}  // namespace qcore
