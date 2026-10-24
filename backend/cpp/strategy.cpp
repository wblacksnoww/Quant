#include "strategy.hpp"
#include <algorithm>
#include <map>
#include <stdexcept>

namespace py = pybind11;

namespace qcore {

double Columns::at(const std::string& field, size_t i) const {
  if (i >= n) return NAN_;
  if (field == "close") return close[i];
  if (field == "open") return open[i];
  if (field == "high") return high[i];
  if (field == "low") return low[i];
  if (field == "volume") return volume[i];
  if (field == "amount") return amount[i];
  auto it = extra.find(field);
  return it != extra.end() ? it->second[i] : NAN_;
}

namespace {

Indicator::Kind indicator_kind(const std::string& name) {
  if (name == "sma") return Indicator::Kind::SMA;
  if (name == "ema") return Indicator::Kind::EMA;
  if (name == "rsi") return Indicator::Kind::RSI;
  if (name == "std") return Indicator::Kind::STD;
  if (name == "rolling_high") return Indicator::Kind::ROLL_HIGH;
  if (name == "rolling_low") return Indicator::Kind::ROLL_LOW;
  if (name == "momentum") return Indicator::Kind::MOMENTUM;
  if (name == "atr") return Indicator::Kind::ATR;
  if (name == "boll_lower") return Indicator::Kind::BOLL_L;
  if (name == "boll_mid") return Indicator::Kind::BOLL_M;
  if (name == "boll_upper") return Indicator::Kind::BOLL_U;
  if (name == "macd_dif") return Indicator::Kind::MACD_DIF;
  if (name == "macd_dea") return Indicator::Kind::MACD_DEA;
  if (name == "macd_hist") return Indicator::Kind::MACD_HIST;
  throw std::runtime_error("未知指标 " + name);
}

bool is_indicator(const std::string& name) {
  try {
    indicator_kind(name);
    return true;
  } catch (const std::runtime_error&) {
    return false;
  }
}

int as_int(const py::handle& h, int def) {
  if (h.is_none()) return def;
  return static_cast<int>(py::cast<double>(h));
}
double as_double(const py::handle& h, double def) {
  if (h.is_none()) return def;
  return py::cast<double>(h);
}

void fill_params(SpecNode& node, const py::dict& params) {
  node.period = as_int(params.contains("period") ? params["period"] : py::none(), 0);
  node.width = as_double(params.contains("width") ? params["width"] : py::none(), 2.0);
  node.fast = as_int(params.contains("fast") ? params["fast"] : py::none(), 12);
  node.slow = as_int(params.contains("slow") ? params["slow"] : py::none(), 26);
  node.signal = as_int(params.contains("signal") ? params["signal"] : py::none(), 9);
}

SpecNode make_indicator_node(const std::string& name, const py::dict& params) {
  SpecNode node;
  node.type = name;
  fill_params(node, params);
  return node;
}

int add_node(CompiledSpec& spec, SpecNode node) {
  spec.nodes.push_back(std::move(node));
  return static_cast<int>(spec.nodes.size()) - 1;
}

int operand_node(CompiledSpec& spec, const py::object& op, int close_idx,
                 std::map<std::string, int>& cache) {
  py::dict d = py::cast<py::dict>(op);
  std::string kind = py::cast<std::string>(d["kind"]);
  if (kind == "const") {
    double value = py::cast<double>(d["value"]);
    std::string key = "const:" + std::to_string(value);
    auto it = cache.find(key);
    if (it != cache.end()) return it->second;
    SpecNode node;
    node.type = "const";
    node.const_value = value;
    int idx = add_node(spec, node);
    cache[key] = idx;
    return idx;
  }
  if (kind == "field") {
    std::string field = py::cast<std::string>(d["field"]);
    std::string key = "field:" + field;
    auto it = cache.find(key);
    if (it != cache.end()) return it->second;
    SpecNode node;
    node.type = "data";
    node.field = field;
    int idx = add_node(spec, node);
    cache[key] = idx;
    return idx;
  }
  std::string name = py::cast<std::string>(d["name"]);
  py::dict params = py::cast<py::dict>(d.contains("params") ? d["params"] : py::dict());
  std::string key = name + ":" + py::cast<std::string>(py::repr(params));
  auto it = cache.find(key);
  if (it != cache.end()) return it->second;
  SpecNode node = make_indicator_node(name, params);
  node.inputs.push_back(close_idx);  // 指标默认吃 close；ATR 内部直接用栏位
  int idx = add_node(spec, std::move(node));
  cache[key] = idx;
  return idx;
}

}  // namespace

StrategyKind strategy_kind(const std::string& id) {
  if (id == "dual_ma") return StrategyKind::DualMa;
  if (id == "momentum_rotation") return StrategyKind::MomentumRotation;
  if (id == "rsi_reversion") return StrategyKind::RsiReversion;
  if (id == "buy_hold") return StrategyKind::BuyHold;
  return StrategyKind::CustomRule;
}

CompiledSpec compile_spec(const py::dict& spec) {
  CompiledSpec out;
  int version = as_int(spec.contains("version") ? py::object(spec["version"]) : py::object(py::int_(1)), 1);
  out.position_pct = as_double(spec.contains("position_pct") ? py::object(spec["position_pct"]) : py::object(py::float_(0.9)), 0.9);
  out.stop_loss = as_double(spec.contains("stop_loss") ? py::object(spec["stop_loss"]) : py::object(py::float_(0.08)), 0.08);
  out.take_profit = as_double(spec.contains("take_profit") ? py::object(spec["take_profit"]) : py::object(py::float_(0.0)), 0.0);

  if (version == 1) {
    out.is_graph = false;
    int close_idx = add_node(out, SpecNode{.type = "data", .field = "close"});
    std::map<std::string, int> cache;
    auto parse_side = [&](py::object list) {
      std::vector<Condition> conds;
      for (auto item : py::cast<py::list>(list)) {
        py::dict d = py::cast<py::dict>(item);
        Condition cond;
        cond.type = py::cast<std::string>(d["type"]);
        cond.left = operand_node(out, py::object(d["left"]), close_idx, cache);
        cond.right = operand_node(out, py::object(d["right"]), close_idx, cache);
        conds.push_back(cond);
      }
      return conds;
    };
    out.entry = parse_side(spec["entry"]);
    out.exit = parse_side(spec["exit"]);
    return out;
  }

  // v2 蓝图
  out.is_graph = true;
  struct Raw {
    std::string id, type;
    py::dict d;
  };
  std::vector<Raw> raws;
  std::map<std::string, std::vector<std::pair<std::string, std::string>>> inputs;  // to → (port, from)
  for (auto item : py::cast<py::list>(spec["nodes"])) {
    py::dict d = py::cast<py::dict>(item);
    raws.push_back({py::cast<std::string>(d["id"]), py::cast<std::string>(d["type"]), d});
  }
  for (auto item : py::cast<py::list>(spec["edges"])) {
    py::dict d = py::cast<py::dict>(item);
    std::string from = py::cast<std::string>(d["from"]);
    std::string to = py::cast<std::string>(d["to"]);
    std::string port = d.contains("to_port") && !d["to_port"].is_none()
                           ? py::cast<std::string>(d["to_port"])
                           : "in";
    inputs[to].emplace_back(port, from);
  }
  // Kahn 拓扑（就绪队列按原始插入序，保证确定性）
  std::map<std::string, int> indeg;
  std::map<std::string, std::vector<std::string>> children;
  for (auto& r : raws) indeg[r.id] = 0;
  for (auto& [to, list] : inputs) {
    if (indeg.find(to) == indeg.end()) indeg[to] = 0;  // 防御
    for (auto& [port, from] : list) {
      indeg[to] += 1;
      children[from].push_back(to);
    }
  }
  std::deque<std::string> ready;
  for (auto& r : raws)
    if (indeg[r.id] == 0) ready.push_back(r.id);
  std::vector<std::string> order;
  while (!ready.empty()) {
    std::string id = ready.front();
    ready.pop_front();
    order.push_back(id);
    for (auto& child : children[id])
      if (--indeg[child] == 0) ready.push_back(child);
  }

  std::map<std::string, int> by_id;
  for (auto& id : order) {
    py::dict d;
    for (auto& r : raws)
      if (r.id == id) { d = r.d; break; }
    SpecNode node;
    std::string type = py::cast<std::string>(d["type"]);
    if (type == "const") {
      node.type = "const";
      node.const_value = as_double(d.contains("value") ? py::object(d["value"]) : py::object(py::float_(0.0)), 0.0);
    } else if (type == "data" || !is_indicator(type)) {
      // 数据源节点（close/open/.../news_*）：无输入连线
      bool has_inputs = inputs.count(id) > 0;
      if (!has_inputs) {
        node.type = "data";
        node.field = type;
      } else {
        node.type = type;  // 罕见：有输入的非指标节点按原类型走（编译期报错更清晰）
      }
    } else {
      node = make_indicator_node(type, py::cast<py::dict>(d.contains("params") ? py::object(d["params"]) : py::dict()));
    }
    by_id[id] = add_node(out, std::move(node));
  }
  // 连线
  for (auto& id : order) {
    int idx = by_id[id];
    if (!inputs.count(id)) continue;
    auto list = inputs[id];
    std::sort(list.begin(), list.end(), [](auto& a, auto& b) { return a.first < b.first; });
    for (auto& [port, from] : list) out.nodes[idx].inputs.push_back(by_id[from]);
  }
  py::dict outputs = py::cast<py::dict>(spec["outputs"]);
  out.entry_idx = by_id[py::cast<std::string>(outputs["entry"])];
  out.exit_idx = by_id[py::cast<std::string>(outputs["exit"])];
  return out;
}

// ------------------------------------------------------------------ //
// 逐 bar 求值
// ------------------------------------------------------------------ //
namespace {

bool cmp(const std::string& type, double l, double r) {
  // NaN 参与比较一律 False（与 pandas 一致）
  if (type == "gt") return l > r;
  if (type == "gte") return l >= r;
  if (type == "lt") return l < r;
  return l <= r;
}

bool cross_signal(const std::string& type, double l, double r, double l_prev, double r_prev) {
  // pandas crossover：diff > 0 且 prev_diff <= 0（NaN 一律无信号）
  if (is_nan(l) || is_nan(r) || is_nan(l_prev) || is_nan(r_prev)) return false;
  double diff = l - r, prev = l_prev - r_prev;
  if (type == "cross_above") return diff > 0 && prev <= 0;
  return diff < 0 && prev >= 0;
}

}  // namespace

std::pair<bool, bool> SpecState::step(size_t cursor, const Columns& cols) {
  if (!inited_) {
    st_.resize(spec_.nodes.size());
    for (size_t i = 0; i < spec_.nodes.size(); ++i) {
      const SpecNode& n = spec_.nodes[i];
      if (is_indicator(n.type)) {
        st_[i].ind = std::make_unique<Indicator>(indicator_kind(n.type), n.period, n.width, n.fast, n.slow, n.signal);
      }
    }
    inited_ = true;
  }
  double close_prev = cursor > 0 ? cols.close[cursor - 1] : NAN_;

  for (size_t i = 0; i < spec_.nodes.size(); ++i) {
    const SpecNode& n = spec_.nodes[i];
    NodeState& s = st_[i];
    s.prev = s.val;
    if (n.type == "data") {
      s.val = cols.at(n.field, cursor);
    } else if (n.type == "const") {
      s.val = n.const_value;
    } else if (is_indicator(n.type)) {
      double x = n.inputs.empty() ? NAN_ : st_[n.inputs[0]].val;
      if (n.type == "atr")
        s.ind->update(x, cols.high[cursor], cols.low[cursor], cols.close[cursor], close_prev);
      else
        s.ind->update(x, NAN_, NAN_, NAN_, NAN_);
      s.val = s.ind->value();
    } else if (n.type == "add" || n.type == "sub" || n.type == "mul" || n.type == "div") {
      double l = st_[n.inputs[0]].val, r = st_[n.inputs[1]].val;
      if (is_nan(l) || is_nan(r)) s.val = NAN_;
      else if (n.type == "add") s.val = l + r;
      else if (n.type == "sub") s.val = l - r;
      else if (n.type == "mul") s.val = l * r;
      else s.val = (r == 0.0) ? NAN_ : l / r;  // 除零 → NaN（pandas mask）
    } else if (n.type == "not") {
      s.bval = !st_[n.inputs[0]].bval;
    } else if (n.type == "and" || n.type == "or") {
      bool l = st_[n.inputs[0]].bval, r = st_[n.inputs[1]].bval;
      s.bval = n.type == "and" ? (l && r) : (l || r);
    } else {
      // 比较/交叉：bool 输出
      if (n.type == "cross_above" || n.type == "cross_below") {
        s.bval = cross_signal(n.type, st_[n.inputs[0]].val, st_[n.inputs[1]].val,
                              st_[n.inputs[0]].prev, st_[n.inputs[1]].prev);
      } else {
        s.bval = cmp(n.type, st_[n.inputs[0]].val, st_[n.inputs[1]].val);
      }
    }
    if (n.type == "not" || n.type == "and" || n.type == "or" || n.type.rfind("cross_", 0) == 0 ||
        n.type == "gt" || n.type == "gte" || n.type == "lt" || n.type == "lte") {
      // 仅布尔节点同步数值影子（data/const 的数值必须保留）
      s.val = s.bval ? 1.0 : 0.0;
    }
  }

  auto bool_of = [&](int idx) { return st_[idx].bval; };
  if (spec_.is_graph) return {bool_of(spec_.entry_idx), bool_of(spec_.exit_idx)};

  auto cond_ok = [&](const Condition& c) {
    const NodeState& l = st_[c.left];
    const NodeState& r = st_[c.right];
    if (c.type == "cross_above" || c.type == "cross_below")
      return cross_signal(c.type, l.val, r.val, l.prev, r.prev);
    return cmp(c.type, l.val, r.val);
  };
  bool entry = true;
  for (const auto& c : spec_.entry) entry = entry && cond_ok(c);
  bool exit = false;
  for (const auto& c : spec_.exit) exit = exit || cond_ok(c);
  return {entry, exit};
}

}  // namespace qcore
