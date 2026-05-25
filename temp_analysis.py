#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from sina_kline import fetch_kline
import pandas as pd
from datetime import datetime

code = 'sh.603599'
name = '广信股份'
days = 120

print(f'正在获取 {name}({code}) 近{days}天行情数据...')

df = fetch_kline(code, days=days)

if df.empty:
    print("未获取到数据")
    sys.exit(1)

df = df.sort_values('date').reset_index(drop=True)

print("="*80)
print(f"【{name}({code})】近120天K线数据分析")
print("="*80)
print(f"\n📊 基础统计：")
print(f"  数据行数: {len(df)}")
print(f"  价格范围: {df['low'].min():.2f} ~ {df['high'].max():.2f}")
print(f"  当前价: {df.iloc[-1]['close']:.2f}")
print(f"  120日最低: {df['low'].min():.2f}")
print(f"  120日最高: {df['high'].max():.2f}")

df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
df['ma20'] = df['close'].rolling(20).mean()
df['ma60'] = df['close'].rolling(60).mean()

print(f"\n📈 均线位置（截止{df.iloc[-1]['date']}）:")
print(f"  MA5:   {df.iloc[-1]['ma5']:.2f}")
print(f"  MA10:  {df.iloc[-1]['ma10']:.2f}")
print(f"  MA20:  {df.iloc[-1]['ma20']:.2f}")
print(f"  MA60:  {df.iloc[-1]['ma60']:.2f}")

print(f"\n📊 近5日走势:")
for i in range(max(0, len(df)-5), len(df)):
    row = df.iloc[i]
    print(f"  {row['date']}: {row['close']:.2f} {row['pctChg']:+.2f}% 成交额{row['amount']:.0f}万")

print(f"\n📊 成交量统计:")
print(f"  近5天平均成交额: {df.iloc[-5:]['amount'].mean():.0f}万")
print(f"  近20天平均成交额: {df.iloc[-20:]['amount'].mean():.0f}万")
print(f"  近5天平均换手率: {df.iloc[-5:]['turn'].mean():.2f}%")

print(f"\n📊 波幅分析:")
print(f"  近5天最高-最低幅度: {(df.iloc[-5:]['high'].max() - df.iloc[-5:]['low'].min()):.2f}")
print(f"  近20天最高-最低幅度: {(df.iloc[-20:]['high'].max() - df.iloc[-20:]['low'].min()):.2f}")

print(f"\n📊 近期涨跌统计:")
up_count = (df.iloc[-30:]['pctChg'] > 0).sum()
down_count = (df.iloc[-30:]['pctChg'] < 0).sum()
print(f"  近30天上涨日: {up_count}天, 下跌日: {down_count}天")

print(f"\n【完整K线数据（最后20条）】")
print(df.iloc[-20:][['date', 'open', 'high', 'low', 'close', 'pctChg', 'volume', 'amount', 'turn']].to_string(index=False))

# 保存数据到CSV用于后续分析
df.to_csv('gx_analysis.csv', index=False)
print(f"\n✅ 数据已保存到 gx_analysis.csv")
