// 拼装策略 spec（v1/v2）编译与逐 bar 求值。
#pragma once
#include "indicators.hpp"
#include <nlohmann/json.hpp>

using json = nlohmann::json;
#include <memory>
#include <unordered_map>

namespace qcore {

// 每只标的的列式数据（DataFrame → numpy 列，由绑定层转换）
struct Columns {
  size_t n = 0;
  const double* open = nullptr;
  const double* high = nullptr;
  const double* low = nullptr;
  const double* close = nullptr;
  const double* volume = nullptr;
  const double* amount = nullptr;
  const double* prev_close = nullptr;
  const double* limit_up = nullptr;
  const double* limit_down = nullptr;
  std::unordered_map<std::string, const double*> extra;  // 新闻因子等附加列

  double at(const std::string& field, size_t i) const;
};

struct SpecNode {
  std::string type;    // data / const / sma / ... / add / gt / and
  std::string field;   // data 节点的字段名
  double const_value = 0.0;
  int period = 0, fast = 0, slow = 0, signal = 0;
  double width = 2.0;
  std::vector<int> inputs;  // 父节点下标
};

struct Condition {
  std::string type;  // gt/gte/lt/lte/cross_above/cross_below
  int left = -1, right = -1;
};

struct CompiledSpec {
  bool is_graph = false;
  std::vector<SpecNode> nodes;  // 拓扑序
  std::vector<Condition> entry, exit;
  int entry_idx = -1, exit_idx = -1;  // v2 输出节点下标
  double position_pct = 0.9, stop_loss = 0.08, take_profit = 0.0;
  int lookback = 0;  // 指标最大周期（warmup = lookback + 2，对齐 Python spec_lookback）
};

CompiledSpec compile_spec(const json& spec);  // nlohmann json（网关 / Python 两侧共用）

enum class StrategyKind { DualMa, MomentumRotation, RsiReversion, BuyHold, CustomRule };
StrategyKind strategy_kind(const std::string& id);

// 每只标的一份：逐 bar 更新节点值并求 entry/exit 信号。
class SpecState {
 public:
  explicit SpecState(const CompiledSpec& spec) : spec_(spec) {}
  // 返回 (entry_signal, exit_signal)
  std::pair<bool, bool> step(size_t cursor, const Columns& cols);

  // 回到初始状态（窗口重喂用：与 Python history(n) 滑窗口径一致）
  void reset();

  // ---- live 幂等：同一根 bar 被重复 step（实时模式每秒更新今日 bar）时，
  // 先回滚到该 bar 首次求值前的状态再推进，保证结果一致。
  std::pair<bool, bool> step_idempotent(size_t cursor, const Columns& cols);

 private:
  struct NodeState {
    double val = NAN_, prev = NAN_;
    bool bval = false;
    std::unique_ptr<Indicator> ind;
  };
  std::vector<NodeState> clone_states() const;
  void restore_states(std::vector<NodeState> snapshot);

  const CompiledSpec& spec_;
  std::vector<NodeState> st_;
  bool inited_ = false;
  std::vector<NodeState> snapshot_;
  size_t snap_cursor_ = static_cast<size_t>(-1);
};



}  // namespace qcore
