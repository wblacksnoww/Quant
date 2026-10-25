#include "strategy_py.hpp"
#include "tick.hpp"
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
    py::function progress, int max_order_age_days);

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
        py::arg("limit_downs"), py::arg("extras"), py::arg("progress"),
        py::arg("max_order_age_days") = 30);

  // ---- tick 级回测 ---------------------------------------------------- //
  // 整段下沉：Python 只交数据、收结果，不参与逐 tick 循环。
  py::class_<TickConfig>(m, "TickConfig")
      .def(py::init<>())
      .def_readwrite("initial_cash", &TickConfig::initial_cash)
      .def_readwrite("fee_rate", &TickConfig::fee_rate)
      .def_readwrite("stamp_tax", &TickConfig::stamp_tax)
      .def_readwrite("min_fee", &TickConfig::min_fee)
      .def_readwrite("lot", &TickConfig::lot)
      .def_readwrite("volume_limit_pct", &TickConfig::volume_limit_pct)
      .def_readwrite("bars_for_signal", &TickConfig::bars_for_signal)
      .def_readwrite("bar_width_ns", &TickConfig::bar_width_ns)
      .def_readwrite("position_pct", &TickConfig::position_pct)
      .def_readwrite("stop_loss", &TickConfig::stop_loss)
      .def_readwrite("take_profit", &TickConfig::take_profit)
      .def_readwrite("slippage_bps", &TickConfig::slippage_bps);

  m.def(
      "run_tick_backtest",
      [](const py::dict& spec, const py::array_t<int64_t, py::array::c_style | py::array::forcecast>& ts,
         const py::array_t<double, py::array::c_style | py::array::forcecast>& price,
         const py::array_t<double, py::array::c_style | py::array::forcecast>& bid,
         const py::array_t<double, py::array::c_style | py::array::forcecast>& ask,
         const py::array_t<double, py::array::c_style | py::array::forcecast>& volume,
         const py::array_t<double, py::array::c_style | py::array::forcecast>& amount,
         const TickConfig& cfg) {
        TickSeries s;
        s.ts_ns.assign(ts.data(), ts.data() + ts.size());
        s.price.assign(price.data(), price.data() + price.size());
        s.bid.assign(bid.data(), bid.data() + bid.size());
        s.ask.assign(ask.data(), ask.data() + ask.size());
        s.volume.assign(volume.data(), volume.data() + volume.size());
        s.amount.assign(amount.data(), amount.data() + amount.size());

        CompiledSpec compiled = compile_spec_py(spec);
        TickSeriesResult r = run_tick_backtest_one(s, compiled, cfg);

        py::dict out;
        py::list fills;
        for (const auto& f : r.fills) {
          py::dict d;
          d["ts"] = f.ts_ns;
          d["price"] = f.price;
          d["quantity"] = f.quantity;
          d["fee"] = f.fee;
          d["side"] = f.is_buy ? "buy" : "sell";
          fills.append(d);
        }
        py::list curve;
        for (size_t i = 0; i < r.snap_ts.size(); ++i) {
          py::dict d;
          d["ts"] = r.snap_ts[i];
          d["equity"] = r.snap_equity[i];
          d["cash"] = r.snap_cash[i];
          d["position"] = r.snap_position[i];
          d["price"] = r.snap_price[i];
          curve.append(d);
        }
        out["fills"] = fills;
        out["equity_curve"] = curve;
        out["ticks_processed"] = r.ticks_processed;
        out["ticks_skipped_warmup"] = r.ticks_skipped_warmup;
        return out;
      },
      "tick 级回测（单标的）：策略以 spec 表达，整段在 C++ 内跑完", py::arg("spec"),
      py::arg("ts"), py::arg("price"), py::arg("bid"), py::arg("ask"), py::arg("volume"),
      py::arg("amount"), py::arg("cfg"));
}

}  // namespace qcore
