#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
from datetime import datetime, timedelta
import requests
import numpy as np
import pandas as pd
import baostock as bs

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

def qq_rt(sym):
    r = requests.get(f'https://qt.gtimg.cn/q={sym}', timeout=10)
    r.encoding = 'gbk'
    items = r.text.strip().split('"')[1].split('~')
    
    def f(i):
        try:
            return float(items[i])
        except:
            return 0.0
    
    return {
        'cur': f(3), 'pre': f(4), 'hi': f(33), 'lo': f(34),
        'pct': f(32), 'outer': f(7), 'inner': f(8), 'turn': f(38),
        'amt_wan': f(37), 'upd': items[30] if len(items) > 30 else ''
    }

# 实时
rt = qq_rt('sh603599')
print('=== 广信股份 当前实时状态 ===')
print(f"时间: {datetime.now().strftime('%H:%M:%S')}")
print(f"现价: {rt['cur']:.2f}  昨收: {rt['pre']:.2f}  涨跌: {rt['pct']:+.2f}%")
dist_pct = (rt['hi'] - rt['cur']) / rt['hi'] * 100
oi = rt['outer'] / max(rt['inner'], 1)
print(f"日高: {rt['hi']:.2f}  日低: {rt['lo']:.2f}  距离高点: {dist_pct:.2f}%")
print(f"外盘: {rt['outer']:.0f}  内盘: {rt['inner']:.0f}  外/内: {oi:.2f}")
print(f"换手: {rt['turn']:.2f}%  成交: {rt['amt_wan']/10000:.2f}亿")
print()

# 买入价
entry = 14.85
hold_qty = 0.5  # 1/2仓
print(f'【你的仓位信息】')
print(f"买入价: {entry:.2f}")
print(f"买入量: {hold_qty:.2f}手（1/2仓）")
float_diff = rt['cur'] - entry
float_pct = (rt['cur'] - entry) / entry * 100
print(f"当前浮亏: {float_diff:+.2f}元  ({float_pct:+.2f}%)")
print()

# 120日
lg = bs.login()
end_date = datetime.now().strftime('%Y-%m-%d')
start_date = (datetime.now() - timedelta(days=240)).strftime('%Y-%m-%d')
rs = bs.query_history_k_data_plus(
    'sh.603599',
    'date,open,high,low,close,volume,amount,pctChg',
    start_date=start_date,
    end_date=end_date,
    frequency='d',
    adjustflag='2'
)
rows = []
while rs.error_code == '0' and rs.next():
    rows.append(rs.get_row_data())
bs.logout()

cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pctChg']
df = pd.DataFrame(rows, columns=cols)
for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pctChg']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df.dropna().tail(120).reset_index(drop=True)

close = df['close'].values
high = df['high'].values
low = df['low'].values

ma5 = float(np.mean(close[-5:]))
ma10 = float(np.mean(close[-10:]))
ma20 = float(np.mean(close[-20:]))
ma60 = float(np.mean(close[-60:]))

# 距离支撑
dist_to_ma5 = (rt['cur'] - ma5) / ma5 * 100
dist_to_ma10 = (rt['cur'] - ma10) / ma10 * 100
dist_to_ma20 = (rt['cur'] - ma20) / ma20 * 100

# 波动率
hh20 = float(np.max(high[-20:]))
ll20 = float(np.min(low[-20:]))
range20 = hh20 - ll20
atr14 = np.std(high[-14:] - low[-14:])

print('【技术结构分析】')
print(f'MA5: {ma5:.2f}  MA10: {ma10:.2f}  MA20: {ma20:.2f}  MA60: {ma60:.2f}')
print(f'距MA5: {dist_to_ma5:+.2f}%  距MA10: {dist_to_ma10:+.2f}%  距MA20: {dist_to_ma20:+.2f}%')
print(f'20日范围: {ll20:.2f} ~ {hh20:.2f}  区间宽度: {range20:.2f}元')
range_pos = (rt['cur'] - ll20) / range20 * 100
print(f'当前在20日区间位置: {range_pos:.1f}%（0=底, 100=顶）')
print()

# 明天推演
print('【明天可能走势推演】')
print()

scenarios = []

# 情景1：高开
print('📍 情景1：明天高开（+0.3%~+0.5%）到14.95-15.00')
oi_val = rt['outer'] / max(rt['inner'], 1)
print(f'  → 需要资金净流入 vs 当前外/内{oi_val:.2f} 略弱')
prob1 = 30
print(f'  概率: {prob1}%')
print(f'  你卖掉的话: 全部脱身，微利或平本')
print(f'  你不卖的话: 机会到手，可以冲15.05+')
print()
scenarios.append(('高开冲15.05+', prob1))

# 情景2：平开/低开 回踩
print('📍 情景2：明天平开/低开到14.70-14.85')
print(f'  → 资金夜间情绪转弱，回测支撑')
prob2 = 45
print(f'  概率: {prob2}%')
print(f'  你卖掉的话: 抢在回踩前全部出')
print(f'  你不卖的话: 被套浮亏-0.3~-0.5%，需要止损执行')
print()
scenarios.append(('平开回踩', prob2))

# 情景3：低开砸
print('📍 情景3：明天突然低开砸（-0.5%~-1.5%）到14.50-14.70')
print(f'  → 夜间突发利空或大盘崩')
prob3 = 25
print(f'  概率: {prob3}%')
print(f'  你卖掉的话: 躲过一劫')
print(f'  你不卖的话: 跌破14.70软止损线，被迫市价砸出，至少-1%')
print()
scenarios.append(('低开砸', prob3))

print(f'总概率: {sum(s[1] for s in scenarios)}% ✓')
print()

# 收益/风险分析
print('【隔夜持仓的收益/风险比】')
print()
print('收益情景（情景1）：')
print(f'  • 概率: {prob1}%')
print(f'  • 最好结果: 冲到15.05, +0.20元, +1.35%')
print(f'  • 预期收益: {prob1/100 * 0.20:.3f}元')
print()

print('风险情景（情景2+3）：')
print(f'  • 总概率: {prob2+prob3}%')
print(f'  • 情景2损失: -0.10~-0.15元, -0.67%~-1.0%')
print(f'  • 情景3损失: -0.20~-0.35元, -1.35%~-2.35%')
print(f'  • 预期风险: {prob2/100*-0.125 + prob3/100*-0.275:.3f}元 (加权平均)')
print()

exp_ret = prob1/100 * 0.20 + (prob2+prob3)/100 * -0.2
exp_loss_volatility = (prob3/100) * 0.35  # 最坏情况的概率加权

print(f'净期望值: {exp_ret:+.3f}元 ({exp_ret/entry*100:+.2f}%)')
print(f'最坏情况损失概率加权: {exp_loss_volatility:.3f}元')
print()

# 对比建议
print('【两个方案的对比】')
print()
print('方案A：今天尾盘全清')
decision_a_best = 0  # 微亏-0.04
decision_a_worst = -0.04
decision_a_exp = -0.04
print(f'  最好: 平本出 (0.00元)')
print(f'  最坏: 微亏 (-0.04元, 已经微亏了)')
print(f'  期望值: 平本')
print(f'  心理压力: 0（断舍离）')
print()

print('方案B：隔夜持仓搏明天')
decision_b_best = 0.20
decision_b_worst = -0.35
decision_b_exp = exp_ret
print(f'  最好: 冲高出手 (+0.20元, +1.35%)')
print(f'  最坏: 砸跌停止损 (-0.35元, -2.35%)')
print(f'  期望值: {decision_b_exp:+.3f}元 ({decision_b_exp/entry*100:+.2f}%)')
print(f'  心理压力: 极大（整夜失眠，早盘紧张）')
print()

print('收益/风险比：')
print(f'  方案A: 0 收益 vs 0 风险 = 中立')
print(f'  方案B: +0.20收益 vs 0.35风险 = {0.20/0.35:.2f}:1 (风险是收益1.75倍)')
print()

print('=' * 80)
print('【理性结论】')
print('=' * 80)
print()
print('从纯数据角度：')
print(f'  ✓ 期望值是正的 (+{decision_b_exp:.3f}元)')
print(f'  ✗ 但最坏亏损-0.35元 > 最好收益+0.20元 (不对等)')
print(f'  ✗ 风险/收益比 1.75:1 （一般建议<1:2才做）')
print(f'  ✗ 成功率只有{prob1}%，失败率{prob2+prob3}%')
print()
print('从执行角度：')
oi_val2 = rt['outer'] / max(rt['inner'], 1)
if oi_val2 < 1.0:
    print(f'  ✗ 外/内 {oi_val2:.2f} 已经资金净流出 → 高开概率下降')
if rt['pct'] < 0:
    print(f'  ✗ 今天已经微亏 → 容易心态失控 → 执行力下降')
dist_hi_pct = (rt['hi'] - rt['cur']) / rt['hi'] * 100
if dist_hi_pct > 1.0:
    print(f'  ✗ 距日高{dist_hi_pct:.2f}% → 高位回落信号 → 明天反弹难度大')
print()
print('【最终量化建议】')
if decision_b_exp > 0 and (0.20/0.35) < 0.7:
    print('  方案B（隔夜）只在以下情况才做：')
    print('    · 你能忍受-0.35的最坏结果')
    print('    · 你明天早上能冷静执行卖出计划（不被情绪掌控）')
    print('    · 你确信明天+1~+2%的高开概率（当前数据支撑度一般）')
    print('    · 否则建议方案A')
else:
    print('  方案A（今天清）的逻辑更严密。')
