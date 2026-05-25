#!/usr/bin/env python3
"""分析上证走势，验证大盘过滤器"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd, numpy as np

init_db()
conn = get_connection()
idx = pd.read_sql("SELECT date, close, pctChg FROM kline_daily WHERE code='sh.000001' ORDER BY date", conn)
for c in ['close','pctChg']:
    idx[c] = pd.to_numeric(idx[c], errors='coerce')
idx = idx.dropna()
print(f"上证数据: {idx.shape[0]}行")
if idx.empty:
    # 没有指数数据，用所有股票的平均涨跌幅模拟大盘
    print("上证指数无数据，用全市场均值模拟")
    all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
    for c in ['open','high','low','close','volume','amount','turn','pctChg']:
        all_data[c] = pd.to_numeric(all_data[c], errors='coerce')
    # 按日计算全市场均价
    daily = all_data.groupby('date').agg({'close': 'mean', 'pctChg': 'mean'}).reset_index()
    daily = daily.sort_values('date').reset_index(drop=True)
    idx = daily
    print(f"模拟大盘: {idx.shape[0]}行, {idx['date'].iloc[0]} ~ {idx['date'].iloc[-1]}")
else:
    print(f"  范围: {idx['date'].iloc[0]} ~ {idx['date'].iloc[-1]}")
    all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
    for c in ['open','high','low','close','volume','amount','turn','pctChg']:
        all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

cls = idx['close'].values
dates = idx['date'].values

print("\n=== 2025-10 ~ 2026-04 大盘状态 ===")
for i in range(len(idx)):
    if dates[i] >= '2025-10-01' and dates[i] <= '2026-04-08':
        if i >= 20:
            ma5 = np.mean(cls[i-4:i+1])
            ma10 = np.mean(cls[i-9:i+1])
            ma20 = np.mean(cls[i-19:i+1])
            bearish = ma5 < ma10 < ma20
            bullish = ma5 > ma10 > ma20
            pct5 = (cls[i] - cls[i-5])/cls[i-5]*100
            # 只打印周一或状态切换日
            weekday = pd.Timestamp(dates[i]).weekday()
            if weekday == 0 or bearish:
                tag = "⛔空头" if bearish else ("🟢多头" if bullish else "〰️震荡")
                print(f"  {dates[i]} {cls[i]:>7.0f} MA5={ma5:.0f} MA10={ma10:.0f} MA20={ma20:.0f} 5日{pct5:+5.1f}% {tag}")

# 统计：空头排列期间做短波的胜率
print("\n=== 如果空头排列时不交易，能避免多少亏损？ ===")
all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
conn.close()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

# If we used simulated market data, all_data is already loaded

# 标记每天的大盘状态
market_state = {}
for i in range(20, len(idx)):
    ma5 = np.mean(cls[i-4:i+1])
    ma10 = np.mean(cls[i-9:i+1])
    ma20 = np.mean(cls[i-19:i+1])
    pct5 = (cls[i] - cls[i-5])/cls[i-5]*100 if cls[i-5] > 0 else 0
    bearish = ma5 < ma10 < ma20
    market_state[dates[i]] = {
        'bearish': bearish,
        'ma5_lt_ma10': ma5 < ma10,
        'pct5': pct5,
    }

# 统计10月以来的交易日中，空头排列天数
bt_dates_range = [d for d in dates if '2025-10-01' <= d <= '2026-04-08']
bear_days = sum(1 for d in bt_dates_range if d in market_state and market_state[d]['bearish'])
total_days = len(bt_dates_range)
print(f"  回测区间共 {total_days} 天，空头排列 {bear_days} 天 ({bear_days/total_days*100:.1f}%)")

# 3月份
mar_dates = [d for d in dates if '2026-03-01' <= d <= '2026-04-08']
mar_bear = sum(1 for d in mar_dates if d in market_state and market_state[d]['bearish'])
print(f"  3月-4/8共 {len(mar_dates)} 天，空头排列 {mar_bear} 天 ({mar_bear/len(mar_dates)*100:.1f}%)")

# MA5 < MA10 的天数（更敏感的指标）
ma5lt = sum(1 for d in bt_dates_range if d in market_state and market_state[d]['ma5_lt_ma10'])
print(f"  MA5<MA10 共 {ma5lt} 天 ({ma5lt/total_days*100:.1f}%)")
