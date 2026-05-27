#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""贵金属板块对比分析"""
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from sina_kline import fetch_kline
import pandas as pd
import numpy as np
from datetime import datetime

stocks = [
    ('sh.600547', '山东黄金'),
    ('sh.601899', '紫金矿业'),
    ('sh.600489', '中金黄金'),
    ('sh.600988', '赤峰黄金'),
    ('sz.000975', '山金国际'),
    ('sz.002155', '湖南黄金'),
    ('sh.601069', '西部黄金'),
    ('sz.002237', '恒邦股份'),
    ('sz.000603', '盛达资源'),
    ('sz.001337', '四川黄金'),
]

days = 120

results = []

for code, name in stocks:
    df = fetch_kline(code, days=days)
    if df.empty:
        continue

    closes = df['close'].tolist()
    vols = df['volume'].tolist()
    turns = df['turn'].tolist()
    pcts = df['pctChg'].tolist()
    
    last_p = closes[-1]
    max_p = max(closes)
    max_date = df.loc[df['close'].idxmax(), 'date']
    
    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:])
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if len(closes)>=60 else np.mean(closes)
    
    drawdown = (last_p - max_p) / max_p * 100
    
    pct_60d = (closes[-1] - closes[-60]) / closes[-60] * 100 if len(closes)>=60 else 0
    
    avg_vol_5 = np.mean(vols[-5:])
    avg_vol_20 = np.mean(vols[-20:])
    avg_vol_60 = np.mean(vols[-60:]) if len(vols)>=60 else np.mean(vols)
    
    vol_ratio_5_20 = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 0
    vol_ratio_5_60 = avg_vol_5 / avg_vol_60 if avg_vol_60 > 0 else 0
    
    avg_turn_5 = np.mean(turns[-5:])
    
    # 近5日波动
    r5_hi = max(closes[-5:])
    r5_lo = min(closes[-5:])
    r5_avg = np.mean(closes[-5:])
    r5_range = (r5_hi - r5_lo) / r5_avg * 100
    
    # 重心偏移
    center_shift = (ma5 - ma10) / ma10 * 100
    
    # 涨停次数(60日)
    limit_ups = sum(1 for p in pcts[-60:] if p >= 9.5)
    
    # 近5日逐日缩量?
    recent_vols = vols[-5:]
    consecutive_shrink = all(recent_vols[i] > recent_vols[i+1] for i in range(len(recent_vols)-4, len(recent_vols)-1))
    
    # 近60日最低量
    min_vol_60 = min(vols[-60:]) if len(vols)>=60 else min(vols)
    near_min_vol = vols[-1] <= min_vol_60 * 1.2
    
    # MA5/10/20间距
    ma_max = max(ma5, ma10, ma20)
    ma_min = min(ma5, ma10, ma20)
    ma_spread = (ma_max - ma_min) / ((ma5+ma10+ma20)/3) * 100
    
    # 近5日数据明细
    last5_details = []
    for i in range(-5, 0):
        r = df.iloc[i]
        last5_details.append(f"  {r['date']} 收:{r['close']:.2f} 涨跌:{r['pctChg']:+.2f}% 量:{int(r['volume']):,} 换手:{r['turn']:.2f}%")
    
    results.append({
        'name': name, 'code': code, 'last_p': last_p,
        'max_p': max_p, 'max_date': max_date, 'drawdown': drawdown,
        'pct_60d': pct_60d,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'vs_ma60': (last_p - ma60)/ma60*100,
        'ma5_gt_ma10': ma5 > ma10, 'ma10_gt_ma20': ma10 > ma20,
        'ma_spread': ma_spread,
        'vol_ratio_5_20': vol_ratio_5_20, 'vol_ratio_5_60': vol_ratio_5_60,
        'avg_turn_5': avg_turn_5,
        'r5_range': r5_range, 'center_shift': center_shift,
        'limit_ups': limit_ups,
        'consecutive_shrink': consecutive_shrink,
        'near_min_vol': near_min_vol,
        'last5': last5_details,
    })

bs.logout()

# 输出对比表
print("="*120)
print("贵金属板块对比分析")
print("="*120)
print(f"{'股票':>8s} {'昨收':>7s} {'60日高':>7s} {'回撤':>7s} {'60日涨':>7s} {'MA5':>7s} {'MA10':>7s} {'MA20':>7s} {'MA60':>7s} {'vs60':>7s} {'量比5/20':>8s} {'量比5/60':>8s} {'5日换手':>7s} {'5日波动':>7s} {'重心':>6s}")
print("-"*120)
for r in results:
    print(f"{r['name']:>8s} {r['last_p']:>7.2f} {r['max_p']:>7.2f} {r['drawdown']:>6.1f}% {r['pct_60d']:>6.1f}% {r['ma5']:>7.2f} {r['ma10']:>7.2f} {r['ma20']:>7.2f} {r['ma60']:>7.2f} {r['vs_ma60']:>6.1f}% {r['vol_ratio_5_20']:>8.2f} {r['vol_ratio_5_60']:>8.2f} {r['avg_turn_5']:>6.2f}% {r['r5_range']:>6.2f}% {r['center_shift']:>+5.1f}%")

# 逐个详细输出
for r in results:
    print(f"\n{'='*80}")
    print(f"  {r['name']} ({r['code']})")
    print(f"{'='*80}")
    print(f"  昨收: {r['last_p']:.2f}   120日最高: {r['max_p']:.2f}({r['max_date']})   回撤: {r['drawdown']:.1f}%")
    print(f"  近60日涨幅: {r['pct_60d']:+.1f}%   近60日涨停: {r['limit_ups']}次")
    print(f"  均线: MA5={r['ma5']:.2f} MA10={r['ma10']:.2f} MA20={r['ma20']:.2f} MA60={r['ma60']:.2f}")
    print(f"  现价vs MA60: {r['vs_ma60']:+.1f}%   均线间距: {r['ma_spread']:.1f}%")
    ma_status = "金叉" if r['ma5_gt_ma10'] else "死叉"
    ma_status2 = "金叉" if r['ma10_gt_ma20'] else "死叉"
    print(f"  MA5/MA10: {ma_status}   MA10/MA20: {ma_status2}")
    print(f"  量比(5/20): {r['vol_ratio_5_20']:.2f}   量比(5/60): {r['vol_ratio_5_60']:.2f}")
    print(f"  5日均换手: {r['avg_turn_5']:.2f}%   5日波动: {r['r5_range']:.2f}%   重心偏移: {r['center_shift']:+.2f}%")
    shrink = "是" if r['consecutive_shrink'] else "否"
    minvol = "是" if r['near_min_vol'] else "否"
    print(f"  连续缩量: {shrink}   接近地量: {minvol}")
    
    # 选股手册评分
    score = 0
    notes = []
    # F1 - 不好在这里判断流通市值，跳过
    # F2 近60日涨停
    if r['limit_ups'] >= 1:
        notes.append("F2通过(有涨停)")
    else:
        notes.append("F2不通过(无涨停)")
    # F3 近60日涨幅10-60%
    if 10 <= r['pct_60d'] <= 60:
        notes.append(f"F3通过(60日涨{r['pct_60d']:.0f}%)")
    else:
        notes.append(f"F3不通过(60日涨{r['pct_60d']:.0f}%)")
    # F4 从高点回撤15-30%
    if -30 <= r['drawdown'] <= -15:
        notes.append(f"F4通过(回撤{r['drawdown']:.0f}%)")
        score += 1
    else:
        notes.append(f"F4不通过(回撤{r['drawdown']:.0f}%)")
    # F5 现价>MA60
    if r['vs_ma60'] > 0:
        notes.append("F5通过(>MA60)")
        score += 1
    else:
        notes.append(f"F5不通过(<MA60 {r['vs_ma60']:.1f}%)")
    
    # 缩量评分
    vol_score = 0
    if r['vol_ratio_5_20'] <= 0.6:
        vol_score += 1
    if r['vol_ratio_5_60'] <= 0.5:
        vol_score += 1
    if r['avg_turn_5'] <= 3:
        vol_score += 1
    if r['consecutive_shrink']:
        vol_score += 1
    if r['near_min_vol']:
        vol_score += 1
    
    # 横盘评分
    side_score = 0
    if r['r5_range'] <= 5:
        side_score += 1
    if abs(r['center_shift']) <= 1:
        side_score += 1
    
    print(f"  ── 手册评分 ──")
    for n in notes:
        print(f"    {n}")
    print(f"    缩量得分: {vol_score}/5   横盘得分: {side_score}/2")
    
    print(f"  ── 近5日明细 ──")
    for d in r['last5']:
        print(d)

print("\n分析完成。")
