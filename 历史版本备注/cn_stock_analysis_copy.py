#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 baostock 获取A股个股近120天行情数据并生成分析报告"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import baostock as bs
import pandas as pd
from datetime import datetime, timedelta

code = 'sh.603281'
name = '江瀚新材'
days = 120

end_date = datetime.now().strftime('%Y-%m-%d')
start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

print(f'正在获取 {name}({code}) 近{days}天行情数据...')

lg = bs.login()
print(f'baostock 登录: {lg.error_msg}')

rs = bs.query_history_k_data_plus(
    code,
    "date,open,high,low,close,volume,amount,adjustflag,turn,pctChg",
    start_date=start_date,
    end_date=end_date,
    frequency="d",
    adjustflag="2"  # 前复权
)

data_list = []
while (rs.error_code == '0') and rs.next():
    data_list.append(rs.get_row_data())

df = pd.DataFrame(data_list, columns=rs.fields)

bs.logout()

if df.empty:
    print("未获取到数据")
    sys.exit(1)

# Convert types
for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df['amount_wan'] = df['amount'] / 10000

print(f'\n股票: {name} ({code})')
print(f'近{days}天行情数据 (共{len(df)}个交易日)')
print(f'数据区间: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}')
print()

print(f'{"日期":>12s} {"开盘":>8s} {"收盘":>8s} {"最高":>8s} {"最低":>8s} {"涨跌幅":>8s} {"成交量(手)":>12s} {"成交额(万)":>12s} {"换手率":>8s}')
print('-' * 110)
for _, row in df.iterrows():
    vol_s = int(row['volume']) if pd.notna(row['volume']) else 0
    print(f'{row["date"]:>12s} {row["open"]:>8.2f} {row["close"]:>8.2f} {row["high"]:>8.2f} {row["low"]:>8.2f} {row["pctChg"]:>7.2f}% {vol_s:>12,d} {row["amount_wan"]:>12.1f} {row["turn"]:>7.2f}%')

# 汇总统计
print('\n' + '=' * 110)
print('汇总统计')
print('=' * 110)

closes = df['close'].tolist()
vols = df['volume'].tolist()

max_p = max(closes)
min_p = min(closes)
first_p = closes[0]
last_p = closes[-1]
total_pct = (last_p - first_p) / first_p * 100

ma5 = sum(closes[-5:]) / min(5, len(closes))
ma10 = sum(closes[-10:]) / min(10, len(closes))
ma20 = sum(closes[-20:]) / min(20, len(closes))
if len(closes) >= 60:
    ma60 = sum(closes[-60:]) / 60
else:
    ma60 = sum(closes) / len(closes)

print(f'期间最高价: {max_p:.2f}')
print(f'期间最低价: {min_p:.2f}')
print(f'期初收盘价: {first_p:.2f}')
print(f'期末收盘价: {last_p:.2f}')
print(f'期间涨跌幅: {total_pct:.2f}%')
print(f'MA5:  {ma5:.2f}')
print(f'MA10: {ma10:.2f}')
print(f'MA20: {ma20:.2f}')
print(f'MA60: {ma60:.2f}')

print(f'\n近5日收盘价:  {[f"{c:.2f}" for c in closes[-5:]]}')
print(f'近10日收盘价: {[f"{c:.2f}" for c in closes[-10:]]}')
print(f'近20日收盘价: {[f"{c:.2f}" for c in closes[-20:]]}')

avg_vol_5 = sum(vols[-5:]) / min(5, len(vols))
avg_vol_20 = sum(vols[-20:]) / min(20, len(vols))
print(f'\n近5日均量:  {avg_vol_5:,.0f}')
print(f'近20日均量: {avg_vol_20:,.0f}')
if avg_vol_20 > 0:
    print(f'量比(5/20): {avg_vol_5/avg_vol_20:.2f}')

# 近期涨跌统计
pcts = df['pctChg'].tolist()
up_days = sum(1 for p in pcts if p > 0)
down_days = sum(1 for p in pcts if p < 0)
flat_days = sum(1 for p in pcts if p == 0)
print(f'\n期间上涨天数: {up_days}')
print(f'期间下跌天数: {down_days}')
print(f'期间平盘天数: {flat_days}')

# 近期连续涨跌统计
recent_pcts = pcts[-20:]
print(f'\n近20日涨跌序列: {[f"{p:.2f}%" for p in recent_pcts]}')

# 近5日资金面（用量价关系替代）
print('\n' + '=' * 110)
print('量价关系分析 (近20日)')
print('=' * 110)
for i in range(-20, 0):
    if i + len(df) >= 0:
        row = df.iloc[i]
        avg_price = row['amount'] / row['volume'] if row['volume'] > 0 else 0
        vol_ratio = row['volume'] / avg_vol_20 if avg_vol_20 > 0 else 0
        status = ""
        if row['pctChg'] > 0 and vol_ratio > 1:
            status = "放量上涨 ↑"
        elif row['pctChg'] > 0 and vol_ratio <= 1:
            status = "缩量上涨 ↗"
        elif row['pctChg'] < 0 and vol_ratio > 1:
            status = "放量下跌 ↓"
        elif row['pctChg'] < 0 and vol_ratio <= 1:
            status = "缩量下跌 ↘"
        else:
            status = "平盘"
        print(f'{row["date"]:>12s}  收盘:{row["close"]:>7.2f}  涨跌:{row["pctChg"]:>6.2f}%  量比:{vol_ratio:>5.2f}  {status}')
