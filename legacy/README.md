# legacy/ — 历史学习版（归档，不再维护）

这里保存的是本项目**重写之前**的 backtrader 学习工程（约 527 行），
仅作参考与对照，**不参与任何构建、部署或测试**。

```
legacy/
├── src/quant/            # 旧版包：backtrader 接入层
│   ├── backtest/dual_ma.py    # 双均线策略（backtrader 版）
│   ├── broker/ashare.py       # A 股佣金/印花税 broker（覆盖 backtrader 结算）
│   ├── data/                  # akshare / tushare / 本地缓存数据源
│   └── config.py
├── config.yaml           # 旧版配置（数据源、成本、paper/backtest 段落）
└── pyproject.toml        # 旧版打包元数据（依赖 backtrader）
```

为什么归档而不是直接删除：它记录了 backtrader 1.9.78 的若干坑
（例如 `bt.indicators` 小写子模块、`PositionInfo` 的取得方式、自引用命名冲突），
这些经验已经被新内核以更可控的方式替代（见 `docs/ARCHITECTURE.md` §2）。

注意：

- 旧版代码与新系统**没有 import 关系**，两者可以独立存在。
- `data/cache/*.csv` 的行情种子仍被新系统作为离线数据源使用（`QUANT_DATA_PROVIDER=csv`），
  所以数据文件留在原处，没有挪进 legacy。
- 如需重新跑旧代码，请单独建虚拟环境并安装 `legacy/pyproject.toml` 的依赖；
  旧版依赖 akshare 的接口与当年可能已不兼容，仅供参考。
