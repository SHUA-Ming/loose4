#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测：A股大盘大涨后次日表现统计
拉取上证指数近10年日K线，统计所有"单日涨幅>2%"后次日走势
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests
import json
import numpy as np

def fetch_index_kline_sina(symbol, datalen=1500):
    """用新浪接口拉取指数日K线，最多约1500条"""
    url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}")
    resp = requests.get(url, timeout=15)
    return resp.json()

print("=" * 70)
print("  A股大涨后次日表现回测（基于真实历史数据）")
print("=" * 70)

# 拉取上证指数
print("\n[1] 拉取上证指数日K线...")
klines = fetch_index_kline_sina('sh000001', datalen=2000)

if not klines:
    print("无法获取数据")
    sys.exit(1)

print(f"  获取到 {len(klines)} 个交易日数据")
print(f"  时间范围: {klines[0]['day']} ~ {klines[-1]['day']}")

# 解析数据
dates = []
opens = []
closes = []
highs = []
lows = []
for k in klines:
    dates.append(k['day'])
    opens.append(float(k['open']))
    closes.append(float(k['close']))
    highs.append(float(k['high']))
    lows.append(float(k['low']))

dates = np.array(dates)
opens = np.array(opens)
closes = np.array(closes)
highs = np.array(highs)
lows = np.array(lows)

# 计算每日涨跌幅
pct = np.zeros(len(closes))
pct[1:] = (closes[1:] - closes[:-1]) / closes[:-1] * 100

# ====================================
# 统计分析
# ====================================
print("\n" + "=" * 70)
print("  统计结果")
print("=" * 70)

for threshold in [2.0, 2.5, 3.0]:
    print(f"\n{'─' * 60}")
    print(f"  当日涨幅 > {threshold}% 后，次日表现")
    print(f"{'─' * 60}")
    
    # 找出所有大涨日（排除最后一天因为没有次日数据）
    big_days = []
    for i in range(1, len(pct) - 1):
        if pct[i] > threshold:
            big_days.append(i)
    
    if not big_days:
        print(f"  没有找到涨幅>{threshold}%的交易日")
        continue
    
    print(f"  共 {len(big_days)} 次大涨日")
    
    # 次日表现
    next_pct = []          # 次日涨跌幅（收盘vs前收）
    next_open_pct = []     # 次日开盘涨跌幅
    next_high_pct = []     # 次日最高点涨跌幅
    next_intra = []        # 次日日内走势（收盘-开盘）
    
    high_open_fall = 0     # 高开低走（开盘涨，收盘跌或收盘<开盘）
    up_count = 0           # 次日收涨
    down_count = 0         # 次日收跌
    gap_up = 0             # 次日高开（开盘>前收）
    gap_up_then_down = 0   # 次日高开但收盘<开盘（冲高回落）
    
    for i in big_days:
        j = i + 1  # 次日
        prev_close = closes[i]
        
        np_ = (closes[j] - prev_close) / prev_close * 100
        nop = (opens[j] - prev_close) / prev_close * 100
        nhp = (highs[j] - prev_close) / prev_close * 100
        ni = (closes[j] - opens[j]) / opens[j] * 100
        
        next_pct.append(np_)
        next_open_pct.append(nop)
        next_high_pct.append(nhp)
        next_intra.append(ni)
        
        if np_ > 0:
            up_count += 1
        else:
            down_count += 1
        
        if nop > 0:
            gap_up += 1
            if closes[j] < opens[j]:
                gap_up_then_down += 1
        
        if nop > 0.3 and ni < -0.3:
            high_open_fall += 1
    
    next_pct = np.array(next_pct)
    next_open_pct = np.array(next_open_pct)
    next_high_pct = np.array(next_high_pct)
    
    total = len(big_days)
    
    print(f"\n  次日收涨: {up_count}次 ({up_count/total*100:.1f}%)")
    print(f"  次日收跌: {down_count}次 ({down_count/total*100:.1f}%)")
    print(f"  次日高开: {gap_up}次 ({gap_up/total*100:.1f}%)")
    print(f"  次日高开低走(开盘涨→收盘<开盘): {gap_up_then_down}次 ({gap_up_then_down/total*100:.1f}%)")
    print(f"  次日冲高回落(高开>0.3%且日内跌>0.3%): {high_open_fall}次 ({high_open_fall/total*100:.1f}%)")
    
    print(f"\n  次日涨跌幅统计:")
    print(f"    平均: {np.mean(next_pct):+.2f}%")
    print(f"    中位数: {np.median(next_pct):+.2f}%")
    print(f"    最大涨: {np.max(next_pct):+.2f}%")
    print(f"    最大跌: {np.min(next_pct):+.2f}%")
    
    print(f"\n  次日盘中最高点(相对前收):")
    print(f"    平均冲高: {np.mean(next_high_pct):+.2f}%")
    print(f"    中位数: {np.median(next_high_pct):+.2f}%")
    print(f"    最高冲到: {np.max(next_high_pct):+.2f}%")
    
    # 分段统计
    print(f"\n  次日涨跌分布:")
    bins = [(-999, -3), (-3, -1), (-1, 0), (0, 1), (1, 3), (3, 999)]
    labels = ['跌>3%', '跌1-3%', '跌0-1%', '涨0-1%', '涨1-3%', '涨>3%']
    for (lo, hi), label in zip(bins, labels):
        cnt = np.sum((next_pct > lo) & (next_pct <= hi))
        bar = '█' * int(cnt / total * 40)
        print(f"    {label:>8s}: {cnt:>3d}次 ({cnt/total*100:>5.1f}%) {bar}")

# ====================================
# 额外：连续大涨分析（和今天的情况更相关：前一天大跌后大涨）
# ====================================
print(f"\n{'=' * 70}")
print(f"  特殊场景：暴跌后报复性反弹（前日跌>2%，当日涨>2%）次日表现")
print(f"{'=' * 70}")

bounce_days = []
for i in range(2, len(pct) - 1):
    if pct[i-1] < -2 and pct[i] > 2:
        bounce_days.append(i)

if bounce_days:
    print(f"  共 {len(bounce_days)} 次此类反弹")
    
    b_next_pct = []
    b_high_pct = []
    b_up = 0
    b_hof = 0
    
    for i in bounce_days:
        j = i + 1
        pc = closes[i]
        np_ = (closes[j] - pc) / pc * 100
        nhp = (highs[j] - pc) / pc * 100
        nop = (opens[j] - pc) / pc * 100
        ni = (closes[j] - opens[j]) / opens[j] * 100
        b_next_pct.append(np_)
        b_high_pct.append(nhp)
        if np_ > 0: b_up += 1
        if nop > 0.3 and ni < -0.3: b_hof += 1
    
    b_next_pct = np.array(b_next_pct)
    b_high_pct = np.array(b_high_pct)
    total_b = len(bounce_days)
    
    print(f"  次日收涨: {b_up}次 ({b_up/total_b*100:.1f}%)")
    print(f"  次日收跌: {total_b-b_up}次 ({(total_b-b_up)/total_b*100:.1f}%)")
    print(f"  冲高回落: {b_hof}次 ({b_hof/total_b*100:.1f}%)")
    print(f"  次日平均涨跌: {np.mean(b_next_pct):+.2f}%")
    print(f"  次日盘中平均冲高: {np.mean(b_high_pct):+.2f}%")
    print(f"  次日中位数涨跌: {np.median(b_next_pct):+.2f}%")
else:
    print("  未找到此类交易日")

# 最近5次大涨日列表
print(f"\n{'=' * 70}")
print(f"  最近10次大涨(>2%)及次日表现")
print(f"{'=' * 70}")
print(f"  {'大涨日':>12s}  {'当日涨':>7s}  {'次日开':>7s}  {'次日高':>7s}  {'次日收':>7s}  {'走势'}")
print(f"  {'-'*60}")

recent_big = [i for i in range(1, len(pct)-1) if pct[i] > 2.0]
for i in recent_big[-10:]:
    j = i + 1
    pc = closes[i]
    d_pct = pct[i]
    n_open = (opens[j] - pc) / pc * 100
    n_high = (highs[j] - pc) / pc * 100
    n_close = (closes[j] - pc) / pc * 100
    
    if n_open > 0.3 and n_close < n_open - 0.5:
        pattern = "冲高回落 ⬇️"
    elif n_close > 1:
        pattern = "继续大涨 🚀"
    elif n_close > 0:
        pattern = "小幅收涨 ↗️"
    elif n_close > -1:
        pattern = "小幅收跌 ↘️"
    else:
        pattern = "大幅回落 ⬇️⬇️"
    
    print(f"  {dates[i]:>12s}  {d_pct:>+6.2f}%  {n_open:>+6.2f}%  {n_high:>+6.2f}%  {n_close:>+6.2f}%  {pattern}")

print(f"\n完成！")
