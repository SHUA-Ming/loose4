#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""近两周板块数据分析"""
import sys, sqlite3, os
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '数据缓存', 'stock_cache.db')
conn = sqlite3.connect(DB_PATH)

# 查近两周的交易日
dates = pd.read_sql_query("SELECT DISTINCT date FROM sector_daily WHERE date >= '2026-03-30' ORDER BY date", conn)
print('=== 近两周交易日 ===')
print(dates['date'].tolist())

# 查所有板块近两周数据
df = pd.read_sql_query("""
    SELECT industry, date, avg_pct, up_count, down_count, stock_count, total_amount, top_gainer, top_gainer_pct
    FROM sector_daily
    WHERE date >= '2026-03-30'
    ORDER BY date, avg_pct DESC
""", conn)

n_ind = df['industry'].nunique()
print(f"\n数据量: {len(df)} 条, 板块数: {n_ind}, 日期范围: {df['date'].min()} ~ {df['date'].max()}")

# 计算每个板块的两周累计涨幅、平均涨幅、最近5日涨幅
pivot = df.pivot_table(index='industry', columns='date', values='avg_pct')
dates_list = sorted(pivot.columns.tolist())
print(f"\n交易日列表({len(dates_list)}天): {dates_list}")

# 近两周累计涨幅
pivot['cum_2w'] = pivot[dates_list].sum(axis=1)
# 近5日累计涨幅
last5 = dates_list[-5:] if len(dates_list) >= 5 else dates_list
pivot['cum_5d'] = pivot[last5].sum(axis=1)
# 近3日累计涨幅
last3 = dates_list[-3:] if len(dates_list) >= 3 else dates_list
pivot['cum_3d'] = pivot[last3].sum(axis=1)
# 最新一天涨幅
pivot['latest'] = pivot[dates_list[-1]]

# 连续上涨天数
def streak(row):
    cnt = 0
    for d in reversed(dates_list):
        if pd.notna(row[d]) and row[d] > 0:
            cnt += 1
        else:
            break
    return cnt

pivot['up_streak'] = pivot.apply(streak, axis=1)

# 按两周累计涨幅排序
result = pivot[['cum_2w', 'cum_5d', 'cum_3d', 'latest', 'up_streak']].sort_values('cum_2w', ascending=False)

print('\n' + '=' * 105)
print('                 近两周板块涨幅排行TOP30 (按两周累计涨幅)')
print('=' * 105)
header = f"{'板块':<35} {'两周累计':>8} {'近5日':>8} {'近3日':>8} {'最新日':>8} {'连涨':>4}"
print(header)
print('-' * 105)
for ind, row in result.head(30).iterrows():
    name = ind
    print(f"{name:<30} {row['cum_2w']:>+8.2f}% {row['cum_5d']:>+8.2f}% {row['cum_3d']:>+8.2f}% {row['latest']:>+8.2f}% {int(row['up_streak']):>4}天")

print('\n' + '=' * 105)
print('                 近两周板块涨幅排行BOTTOM15 (最弱板块)')
print('=' * 105)
for ind, row in result.tail(15).iterrows():
    name = ind
    print(f"{name:<30} {row['cum_2w']:>+8.2f}% {row['cum_5d']:>+8.2f}% {row['cum_3d']:>+8.2f}% {row['latest']:>+8.2f}% {int(row['up_streak']):>4}天")

# === 趋势加速分析：近3天vs此前 ===
print('\n' + '=' * 105)
print('        近3天加速走强板块 (近3日日均涨>0.5% 且 > 之前日均)')
print('=' * 105)
prev_days = dates_list[:-3] if len(dates_list) > 3 else dates_list[:1]
pivot['avg_prev'] = pivot[prev_days].mean(axis=1)
pivot['avg_3d'] = pivot[last3].mean(axis=1)
accel = pivot[(pivot['avg_3d'] > 0.5) & (pivot['avg_3d'] > pivot['avg_prev'])].sort_values('avg_3d', ascending=False)
print(f"{'板块':<35} {'近3日日均':>10} {'此前日均':>10} {'加速度':>10}")
print('-' * 105)
for ind, row in accel.head(20).iterrows():
    diff = row['avg_3d'] - row['avg_prev']
    print(f"{ind:<30} {row['avg_3d']:>+10.2f}% {row['avg_prev']:>+10.2f}% {diff:>+10.2f}%")

# === 每日板块涨幅详情 (最近5天每天TOP5) ===
print('\n' + '=' * 105)
print('        最近5个交易日 每日涨幅TOP5板块')
print('=' * 105)
for d in last5:
    day_data = df[df['date'] == d].sort_values('avg_pct', ascending=False).head(5)
    print(f"\n--- {d} ---")
    for _, r in day_data.iterrows():
        up = int(r['up_count'])
        dn = int(r['down_count'])
        print(f"  {r['industry']:<30} {r['avg_pct']:>+6.2f}%  涨:{up} 跌:{dn}  领涨:{r['top_gainer']} {r['top_gainer_pct']:>+.1f}%")

# === 板块资金流向（成交额趋势）===
print('\n' + '=' * 105)
print('        板块成交额近5日变化TOP15 (成交额持续放大=资金流入)')
print('=' * 105)
amt_pivot = df.pivot_table(index='industry', columns='date', values='total_amount')
if len(dates_list) >= 5:
    amt_pivot['amt_5d'] = amt_pivot[last5].mean(axis=1)
    prev5 = dates_list[:len(dates_list)-5] if len(dates_list) > 5 else dates_list[:2]
    if prev5:
        amt_pivot['amt_prev'] = amt_pivot[prev5].mean(axis=1)
        amt_pivot['amt_change'] = (amt_pivot['amt_5d'] / amt_pivot['amt_prev'] - 1) * 100
        amt_rank = amt_pivot.dropna(subset=['amt_change']).sort_values('amt_change', ascending=False)
        print(f"{'板块':<35} {'近5日均额':>15} {'此前均额':>15} {'增幅':>10}")
        print('-' * 105)
        for ind, row in amt_rank.head(15).iterrows():
            print(f"{ind:<30} {row['amt_5d']/1e8:>12.1f}亿 {row['amt_prev']/1e8:>12.1f}亿 {row['amt_change']:>+8.1f}%")

# === 大盘指数近两周走势 ===
print('\n' + '=' * 105)
print('        大盘指数近两周走势')
print('=' * 105)
idx_df = pd.read_sql_query("""
    SELECT code, date, close, pctChg, volume 
    FROM kline_daily 
    WHERE code IN ('sh.000001','sz.399001','sz.399006') 
      AND date >= '2026-03-30'
    ORDER BY code, date
""", conn)
for col in ['close', 'pctChg', 'volume']:
    idx_df[col] = pd.to_numeric(idx_df[col], errors='coerce')

names = {'sh.000001': '上证指数', 'sz.399001': '深证成指', 'sz.399006': '创业板指'}
for code, name in names.items():
    sub = idx_df[idx_df['code'] == code].copy()
    print(f"\n--- {name} ---")
    print(f"  {'日期':<12} {'收盘':>10} {'涨跌幅':>8} {'成交量(万手)':>12}")
    for _, r in sub.iterrows():
        vol = r['volume'] / 10000 if pd.notna(r['volume']) else 0
        print(f"  {r['date']:<12} {r['close']:>10.2f} {r['pctChg']:>+7.2f}% {vol:>12.0f}")
    if len(sub) >= 2:
        start_p = sub.iloc[0]['close']
        end_p = sub.iloc[-1]['close']
        chg = (end_p / start_p - 1) * 100
        print(f"  两周累计: {chg:>+.2f}%  ({start_p:.2f} → {end_p:.2f})")

conn.close()
print("\n=== 数据打印完毕 ===")
