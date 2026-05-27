# 候选 ETF 流程

这里维护长期ETF仓位的候选池、评分规则、数据更新、持仓跟踪和复盘流程。

当前入口：

- [ETF筛选规则.md](ETF筛选规则.md)：V1稳健收益筛选规则，包含ETF分类、硬过滤、评分模型、买入/卖出/再平衡规则。
- [etf_pool.py](etf_pool.py)：ETF基础池维护，拉取列表、合并可用行情/净值/规模字段，并自动分类。
- [etf_screener.py](etf_screener.py)：ETF稳健收益评分器，计算回撤、趋势、收益效率和执行质量，输出A/B/C/D候选。
- [etf_position_tracker.py](etf_position_tracker.py)：ETF持仓/建仓跟踪器，合并持仓、评分和当前价格，输出补仓/暂停/再平衡建议。
- [ETF操作提示词.md](ETF操作提示词.md)：日常问AI筛ETF、决定买点、管理持仓时使用的提示词。

## 系统目标

ETF系统服务于长期仓位，不服务于短波交易。目标是在可承受回撤内提高长期收益效率：核心仓求稳，增强仓求弹性。

## 建设步骤

1. 规则文档：先固定分类、硬过滤、评分、买卖和再平衡规则。已完成V1。
2. ETF池维护：拉取全市场ETF列表，补充分类、规模、费率、跟踪指数。已完成V1基础池。
3. 历史行情：拉取ETF日K/NAV，计算收益、波动、回撤、均线和成交额。已完成V1行情指标。
4. 评分脚本：按分类输出A/B/C/D候选池，不同类别分开排名。已完成V1评分器。
5. 持仓跟踪：记录目标仓位、已买批次、成本、再平衡提醒。已完成V1持仓跟踪器。
6. 复盘校准：每月记录入选ETF表现，半年调整权重和阈值。

## 运行命令

查看ETF基础池：

```bash
python3 工具脚本/60_候选ETF/etf_pool.py --top 30
```

只看红利/高股息/低波类核心池：

```bash
python3 工具脚本/60_候选ETF/etf_pool.py --category CORE_DIVIDEND --top 30
```

运行稳健收益评分器，默认按成交额取前160只扫描：

```bash
python3 工具脚本/60_候选ETF/etf_screener.py --top-per-category 8
```

只扫描红利核心类ETF：

```bash
python3 工具脚本/60_候选ETF/etf_screener.py --category CORE_DIVIDEND --limit 80 --top-per-category 12
```

保存完整结果时显式指定CSV路径：

```bash
python3 工具脚本/60_候选ETF/etf_screener.py --csv 每日复盘/ETF评分结果.csv
```

初始化ETF持仓表：

```bash
python3 工具脚本/60_候选ETF/etf_position_tracker.py init --csv 每日复盘/ETF持仓.csv
```

根据评分结果生成候选ETF首批建仓计划：

```bash
python3 工具脚本/60_候选ETF/etf_position_tracker.py buy-plan --scores 每日复盘/ETF评分结果.csv --portfolio-value 100000 --cash 30000 --top 8
```

检查已有ETF持仓是否补仓、暂停或再平衡：

```bash
python3 工具脚本/60_候选ETF/etf_position_tracker.py report --positions 每日复盘/ETF持仓.csv --scores 每日复盘/ETF评分结果.csv --cash 30000 --portfolio-value 100000
```

V1说明：东方财富现货接口在当前环境可能被代理挡住，脚本默认使用新浪ETF行情、东方财富/同花顺净值和ETF历史K线兜底。规模、费率、跟踪误差、估值分位属于V2增强字段；当前缺失时不会阻断评分，但会降低执行质量或限制评级。

## 复用边界

可复用能力优先从 `../00_公共核心/`、`../10_数据更新/`、`../20_市场环境/` 引入。

- 跨目录导入：先接入 `../00_公共核心/project_paths.py`。
- 市场模式：复用 `../20_市场环境/market_mode.py` 的 M1-M5 判断，只调整ETF买入节奏和增强仓比例。
- 数据连接：后续如果沉淀ETF历史行情或评分结果，优先复用 `../00_公共核心/db_cache.py` 的连接方式。
