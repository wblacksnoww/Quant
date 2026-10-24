#include "strategy.hpp"
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

PYBIND11_MODULE(_core, m) {
  m.doc() = "Quant Terminal C++ 加速核心（回测热路径）";
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
