#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from sina_kline import fetch_kline
import pandas as pd
from datetime import datetime

code = 'sh.603496'
name = '恒为科技'
days = 120

df = fetch_kline(code, days=days)

df['amount_wan'] = df['amount'] / 10000
df['MA5'] = df['close'].rolling(5).mean()
df['MA10'] = df['close'].rolling(10).mean()
df['MA20'] = df['close'].rolling(20).mean()
df['MA60'] = df['close'].rolling(60).mean()
df['vol_ma5'] = df['volume'].rolling(5).mean()

print(f'股票: {name} ({code})')
print(f'数据区间: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}，共{len(df)}个交易日')
print()

header = f'{"日期":>12s} {"开盘":>8s} {"收盘":>8s} {"最高":>8s} {"最低":>8s} {"涨幅%":>8s} {"量(万手)":>10s} {"额(万)":>10s} {"换手%":>6s} {"MA5":>8s} {"MA10":>8s} {"MA20":>8s}'
print(header)
print('-' * 130)
for _, r in df.iterrows():
    print(f'{r["date"]:>12s} {r["open"]:8.2f} {r["close"]:8.2f} {r["high"]:8.2f} {r["low"]:8.2f} {r["pctChg"]:+8.2f} {r["volume"]/10000:10,.1f} {r["amount_wan"]:10,.0f} {r["turn"]:6.2f} {r["MA5"]:8.2f} {r["MA10"]:8.2f} {r["MA20"]:8.2f}')

print()
print('=== 关键统计 ===')
last = df.iloc[-1]
print(f'最新收盘: {last["close"]:.2f}')
print(f'MA5={last["MA5"]:.2f}, MA10={last["MA10"]:.2f}, MA20={last["MA20"]:.2f}, MA60={last["MA60"]:.2f}')
print(f'120天最高: {df["high"].max():.2f} ({df.loc[df["high"].idxmax(),"date"]})')
print(f'120天最低: {df["low"].min():.2f} ({df.loc[df["low"].idxmin(),"date"]})')

r20 = df.tail(20)
print(f'近20日均量: {r20["volume"].mean()/10000:,.1f}万手')
print(f'近5日均量: {df.tail(5)["volume"].mean()/10000:,.1f}万手')
print(f'近20日均额: {r20["amount_wan"].mean():,.0f}万')

r5 = df.tail(5)
print(f'近5日涨跌: {r5["pctChg"].sum():+.2f}%')
r10 = df.tail(10)
print(f'近10日涨跌: {r10["pctChg"].sum():+.2f}%')
print(f'近20日最高: {r20["high"].max():.2f}')
print(f'近20日最低: {r20["low"].min():.2f}')
print(f'距120天高点回撤: {(last["close"]/df["high"].max()-1)*100:.1f}%')

# MACD
ema12 = df['close'].ewm(span=12).mean()
ema26 = df['close'].ewm(span=26).mean()
df['DIF'] = ema12 - ema26
df['DEA'] = df['DIF'].ewm(span=9).mean()
df['MACD'] = 2 * (df['DIF'] - df['DEA'])
print(f'\n=== MACD ===')
print(f'DIF={df["DIF"].iloc[-1]:.3f}, DEA={df["DEA"].iloc[-1]:.3f}, MACD={df["MACD"].iloc[-1]:.3f}')
print(f'前日 DIF={df["DIF"].iloc[-2]:.3f}, DEA={df["DEA"].iloc[-2]:.3f}, MACD={df["MACD"].iloc[-2]:.3f}')

# RSI
delta = df['close'].diff()
gain = delta.where(delta > 0, 0)
loss = (-delta).where(delta < 0, 0)
avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()
rs_val = avg_gain / avg_loss.replace(0, 0.0001)
rsi = 100 - (100 / (1 + rs_val))
print(f'RSI(14)={rsi.iloc[-1]:.1f}')

# KDJ
low9 = df['low'].rolling(9).min()
high9 = df['high'].rolling(9).max()
rsv = (df['close'] - low9) / (high9 - low9) * 100
k = rsv.ewm(alpha=1/3, adjust=False).mean()
d = k.ewm(alpha=1/3, adjust=False).mean()
j = 3*k - 2*d
print(f'KDJ: K={k.iloc[-1]:.1f}, D={d.iloc[-1]:.1f}, J={j.iloc[-1]:.1f}')

# Bollinger Bands
bb_mid = df['close'].rolling(20).mean()
bb_std = df['close'].rolling(20).std()
bb_up = bb_mid + 2*bb_std
bb_dn = bb_mid - 2*bb_std
print(f'布林带: 上轨={bb_up.iloc[-1]:.2f}, 中轨={bb_mid.iloc[-1]:.2f}, 下轨={bb_dn.iloc[-1]:.2f}')

# Recent 15 days detail
print(f'\n=== 近15个交易日明细 ===')
for _, r in df.tail(15).iterrows():
    body = r['close'] - r['open']
    upper_shadow = r['high'] - max(r['close'], r['open'])
    lower_shadow = min(r['close'], r['open']) - r['low']
    vol_ratio = r['volume'] / r['vol_ma5'] if r['vol_ma5'] > 0 else 0
    ktype = '阳线' if body > 0 else ('阴线' if body < 0 else '十字')
    print(f'{r["date"]} {ktype} 涨跌{r["pctChg"]:+.2f}% 实体{abs(body):.2f} 上影{upper_shadow:.2f} 下影{lower_shadow:.2f} 量比{vol_ratio:.2f}')

# 连续涨跌统计
streak = 0
for i in range(len(df)-1, -1, -1):
    if streak == 0:
        streak = 1 if df.iloc[i]['pctChg'] >= 0 else -1
    elif streak > 0 and df.iloc[i]['pctChg'] >= 0:
        streak += 1
    elif streak < 0 and df.iloc[i]['pctChg'] < 0:
        streak -= 1
    else:
        break
print(f'\n当前连续: {"涨" if streak > 0 else "跌"}{abs(streak)}天')

# 近30天涨跌幅
r30 = df.tail(30)
print(f'近30日涨跌: {r30["pctChg"].sum():+.2f}%')
