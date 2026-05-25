#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量获取5只股票120天数据"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sina_kline import fetch_kline
import pandas as pd
from datetime import datetime

stocks = [
    ('sh.603227', '雪峰科技'),
    ('sh.603585', '苏利股份'),
    ('sz.002407', '多氟多'),
    ('sh.603906', '龙蟠科技'),
    ('sz.300821', '东岳硅材'),
    ('sz.002409', '雅克科技'),
]

days = 120

for code, name in stocks:
    print(f'\n{"#"*120}')
    print(f'# {name} ({code})')
    print(f'{"#"*120}')

    df = fetch_kline(code, days=days)

    if df.empty:
        print("未获取到数据")
        continue

    for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['amount_wan'] = df['amount'] / 10000

    print(f'数据区间: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}  共{len(df)}个交易日')
    print()

    # 打印每日数据
    print(f'{"日期":>12s} {"开盘":>8s} {"收盘":>8s} {"最高":>8s} {"最低":>8s} {"涨跌幅":>8s} {"成交量(手)":>12s} {"成交额(万)":>12s} {"换手率":>8s}')
    print('-' * 110)
    for _, row in df.iterrows():
        vol_s = int(row['volume']) if pd.notna(row['volume']) else 0
        print(f'{row["date"]:>12s} {row["open"]:>8.2f} {row["close"]:>8.2f} {row["high"]:>8.2f} {row["low"]:>8.2f} {row["pctChg"]:>7.2f}% {vol_s:>12,d} {row["amount_wan"]:>12.1f} {row["turn"]:>7.2f}%')

    # 汇总统计
    closes = df['close'].tolist()
    vols = df['volume'].tolist()
    turns = df['turn'].tolist()
    pcts = df['pctChg'].tolist()

    max_p = max(closes)
    min_p = min(closes)
    first_p = closes[0]
    last_p = closes[-1]
    total_pct = (last_p - first_p) / first_p * 100

    max_p_date = df.loc[df['close'].idxmax(), 'date']
    min_p_date = df.loc[df['close'].idxmin(), 'date']
    max_p_idx = df['close'].idxmax()
    drawdown = (last_p - max_p) / max_p * 100

    ma5 = sum(closes[-5:]) / min(5, len(closes))
    ma10 = sum(closes[-10:]) / min(10, len(closes))
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else sum(closes) / len(closes)

    avg_vol_5 = sum(vols[-5:]) / min(5, len(vols))
    avg_vol_10 = sum(vols[-10:]) / min(10, len(vols))
    avg_vol_20 = sum(vols[-20:]) / min(20, len(vols))
    avg_vol_60 = sum(vols[-60:]) / 60 if len(vols) >= 60 else sum(vols) / len(vols)

    avg_turn_5 = sum(turns[-5:]) / min(5, len(turns))
    avg_turn_20 = sum(turns[-20:]) / min(20, len(turns))

    # 近5日波动幅度
    recent5_hi = max(closes[-5:])
    recent5_lo = min(closes[-5:])
    recent5_avg = sum(closes[-5:]) / 5
    recent5_range = (recent5_hi - recent5_lo) / recent5_avg * 100

    # 近10日波动
    recent10_hi = max(closes[-10:])
    recent10_lo = min(closes[-10:])
    recent10_avg = sum(closes[-10:]) / 10
    recent10_range = (recent10_hi - recent10_lo) / recent10_avg * 100

    # 均线间距
    ma_max = max(ma5, ma10, ma20)
    ma_min = min(ma5, ma10, ma20)
    ma_avg = (ma5 + ma10 + ma20) / 3
    ma_spread = (ma_max - ma_min) / ma_avg * 100

    # 涨停检测（近60日）
    limit_up_days = sum(1 for p in pcts[-60:] if p >= 9.5)

    # 近60日涨幅
    if len(closes) >= 60:
        pct_60d = (closes[-1] - closes[-60]) / closes[-60] * 100
    else:
        pct_60d = total_pct

    print(f'\n{"="*110}')
    print(f'汇总统计 - {name}')
    print(f'{"="*110}')
    print(f'期间最高: {max_p:.2f} ({max_p_date})   期间最低: {min_p:.2f} ({min_p_date})')
    print(f'期初收盘: {first_p:.2f}   期末收盘(昨收): {last_p:.2f}   期间涨跌: {total_pct:+.2f}%')
    print(f'从最高点回撤: {drawdown:+.2f}%')
    print(f'近60日涨幅: {pct_60d:+.2f}%   近60日涨停次数: {limit_up_days}')
    print(f'\n均线:')
    print(f'  MA5:  {ma5:.2f}   MA10: {ma10:.2f}   MA20: {ma20:.2f}   MA60: {ma60:.2f}')
    print(f'  现价vs MA60: {"上方" if last_p > ma60 else "下方"} ({(last_p-ma60)/ma60*100:+.2f}%)')
    print(f'  MA5/10/20间距: {ma_spread:.2f}%  {"粘合(<3%)" if ma_spread < 3 else "发散"}')
    print(f'  MA5 vs MA10: {"金叉" if ma5 > ma10 else "死叉"}   MA10 vs MA20: {"金叉" if ma10 > ma20 else "死叉"}')

    print(f'\n成交量:')
    print(f'  5日均量:  {avg_vol_5:>12,.0f}   10日均量: {avg_vol_10:>12,.0f}')
    print(f'  20日均量: {avg_vol_20:>12,.0f}   60日均量: {avg_vol_60:>12,.0f}')
    print(f'  量比(5/20): {avg_vol_5/avg_vol_20:.2f}   量比(5/60): {avg_vol_5/avg_vol_60:.2f}')
    print(f'  5日均换手: {avg_turn_5:.2f}%   20日均换手: {avg_turn_20:.2f}%')

    print(f'\n波动与横盘:')
    print(f'  近5日价格区间: {recent5_lo:.2f}~{recent5_hi:.2f}  波动幅度: {recent5_range:.2f}%  {"窄幅(<5%)" if recent5_range < 5 else "宽幅"}')
    print(f'  近10日价格区间: {recent10_lo:.2f}~{recent10_hi:.2f}  波动幅度: {recent10_range:.2f}%')

    # 重心是否下移
    avg_5 = sum(closes[-5:]) / 5
    avg_10 = sum(closes[-10:]) / 10
    center_shift = (avg_5 - avg_10) / avg_10 * 100
    print(f'  重心偏移(5日均vs10日均): {center_shift:+.2f}%  {"横盘" if abs(center_shift) <= 1 else "下移" if center_shift < -1 else "上移"}')

    # 近5日逐日量变化
    print(f'\n近5日逐日数据:')
    for i in range(-5, 0):
        row = df.iloc[i]
        vol_ratio = row['volume'] / avg_vol_20 if avg_vol_20 > 0 else 0
        print(f'  {row["date"]}  收盘:{row["close"]:>7.2f}  涨跌:{row["pctChg"]:>+6.2f}%  量:{int(row["volume"]):>10,d}  量比20日:{vol_ratio:.2f}  换手:{row["turn"]:.2f}%')

    # 量价关系 近20日
    print(f'\n量价关系 (近20日):')
    for i in range(-20, 0):
        if i + len(df) >= 0:
            row = df.iloc[i]
            vol_ratio = row['volume'] / avg_vol_20 if avg_vol_20 > 0 else 0
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
            print(f'  {row["date"]}  收盘:{row["close"]:>7.2f}  涨跌:{row["pctChg"]:>+6.2f}%  量比:{vol_ratio:>5.2f}  {status}')

    print()

bs.logout()
print("\n全部完成。")
