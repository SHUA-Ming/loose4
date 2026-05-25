#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from sina_kline import fetch_kline
import pandas as pd
import numpy as np
from datetime import datetime

code = 'sh.600226'
name = '亨通股份'
days = 120
df = fetch_kline(code, days=days)

print(f'=== {name} ({code}) 近{days}天K线 ===')
print(f'数据区间: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}, 共{len(df)}个交易日')
print()

# 打印全部数据
for _, row in df.iterrows():
    vol_s = int(row['volume']) if pd.notna(row['volume']) else 0
    amt_w = row['amount'] / 10000
    print(f'{row["date"]} O:{row["open"]:.2f} C:{row["close"]:.2f} H:{row["high"]:.2f} L:{row["low"]:.2f} Chg:{row["pctChg"]:+.2f}% Vol:{vol_s:,} Amt:{amt_w:.0f}w Turn:{row["turn"]:.2f}%')

closes = df['close'].values
opens = df['open'].values
highs = df['high'].values
lows = df['low'].values
vols = df['volume'].values
turns = df['turn'].values
pcts = df['pctChg'].values

# 均线
ma5 = np.mean(closes[-5:])
ma10 = np.mean(closes[-10:])
ma20 = np.mean(closes[-20:])
ma60 = np.mean(closes[-60:]) if len(closes)>=60 else np.mean(closes)
ma120 = np.mean(closes) if len(closes)>=120 else np.mean(closes)

# 近期量能
avg_vol_5 = np.mean(vols[-5:])
avg_vol_10 = np.mean(vols[-10:])
avg_vol_20 = np.mean(vols[-20:])
vol_ratio = vols[-1]/avg_vol_5 if avg_vol_5 > 0 else 0

# 支撑阻力
recent_high = max(closes[-20:])
recent_low = min(closes[-20:])
high_120 = max(closes)
low_120 = min(closes)

print()
print('='*80)
print('TECHNICAL SUMMARY')
print('='*80)
print(f'Latest close: {closes[-1]:.2f}')
print(f'MA5={ma5:.3f}  MA10={ma10:.3f}  MA20={ma20:.3f}  MA60={ma60:.3f}  MA120={ma120:.3f}')

if closes[-1]>ma5>ma10>ma20:
    ma_status = 'BULLISH (多头排列)'
elif closes[-1]<ma5<ma10<ma20:
    ma_status = 'BEARISH (空头排列)'
else:
    ma_status = 'MIXED (震荡交叉)'
print(f'MA alignment: {ma_status}')
print(f'Price vs MA5: {(closes[-1]/ma5-1)*100:+.2f}%')
print(f'Price vs MA10: {(closes[-1]/ma10-1)*100:+.2f}%')
print(f'Price vs MA20: {(closes[-1]/ma20-1)*100:+.2f}%')
print(f'Price vs MA60: {(closes[-1]/ma60-1)*100:+.2f}%')

print(f'Avg Vol 5d: {avg_vol_5:,.0f}  10d: {avg_vol_10:,.0f}  20d: {avg_vol_20:,.0f}')
print(f'Latest vol ratio(vs 5d avg): {vol_ratio:.2f}')
print(f'Recent 20d high: {recent_high:.2f}  low: {recent_low:.2f}')
print(f'120d high: {high_120:.2f}  low: {low_120:.2f}')
print(f'Sum pctChg 5d: {sum(pcts[-5:]):.2f}%')
print(f'Sum pctChg 10d: {sum(pcts[-10:]):.2f}%')
print(f'Sum pctChg 20d: {sum(pcts[-20:]):.2f}%')

# MACD
ema12 = pd.Series(closes).ewm(span=12).mean().values
ema26 = pd.Series(closes).ewm(span=26).mean().values
dif = ema12 - ema26
dea = pd.Series(dif).ewm(span=9).mean().values
macd = 2*(dif - dea)
print(f'MACD: DIF={dif[-1]:.4f} DEA={dea[-1]:.4f} MACD={macd[-1]:.4f}')
if dif[-1]>dea[-1] and dif[-2]<=dea[-2]:
    print('*** MACD金叉! ***')
elif dif[-1]<dea[-1] and dif[-2]>=dea[-2]:
    print('*** MACD死叉! ***')

# RSI
delta = pd.Series(pcts)
gain = delta.where(delta>0, 0).rolling(14).mean()
loss = (-delta.where(delta<0, 0)).rolling(14).mean()
rs_val = gain / loss.replace(0, np.nan)
rsi14 = 100 - (100/(1+rs_val))
print(f'RSI14: {rsi14.iloc[-1]:.1f}')

# KDJ
low_9 = pd.Series(lows).rolling(9).min()
high_9 = pd.Series(highs).rolling(9).max()
rsv = (closes[-1] - low_9.iloc[-1]) / (high_9.iloc[-1] - low_9.iloc[-1]) * 100
print(f'RSV9: {rsv:.1f}')

# 连涨/连跌天数
streak = 0
if pcts[-1] > 0:
    for i in range(len(pcts)-1, -1, -1):
        if pcts[i] > 0: streak += 1
        else: break
    print(f'连涨天数: {streak}')
else:
    for i in range(len(pcts)-1, -1, -1):
        if pcts[i] <= 0: streak += 1
        else: break
    print(f'连跌天数: {streak}')

# 最近15天K线形态
print()
print('=== RECENT 15 DAYS DETAIL ===')
for i in range(-15, 0):
    idx = len(df)+i
    row = df.iloc[idx]
    body = row['close'] - row['open']
    upper = row['high'] - max(row['close'], row['open'])
    lower = min(row['close'], row['open']) - row['low']
    real_body = abs(body)
    if real_body < 0.02:
        bar_type = 'DOJI'
    elif body > 0:
        bar_type = 'BULL'
    else:
        bar_type = 'BEAR'
    print(f'{row["date"]} C:{row["close"]:.2f} Chg:{row["pctChg"]:+.2f}% Vol:{int(row["volume"]):,} Turn:{row["turn"]:.2f}% {bar_type} Body:{real_body:.3f} UpShadow:{upper:.3f} DnShadow:{lower:.3f}')

# 关键价位
print()
print('=== KEY LEVELS ===')
# 整数关口
for level in [5.0, 5.5, 6.0, 4.5]:
    dist = (level - closes[-1])/closes[-1]*100
    print(f'  {level:.1f} ({dist:+.1f}%)')

# 前高前低
print(f'  20d high: {recent_high:.2f} ({(recent_high-closes[-1])/closes[-1]*100:+.1f}%)')
print(f'  20d low: {recent_low:.2f} ({(recent_low-closes[-1])/closes[-1]*100:+.1f}%)')
print(f'  120d high: {high_120:.2f} ({(high_120-closes[-1])/closes[-1]*100:+.1f}%)')
print(f'  120d low: {low_120:.2f} ({(low_120-closes[-1])/closes[-1]*100:+.1f}%)')
