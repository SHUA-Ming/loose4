#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

code = 'sh.603227'
days = 120
df = fetch_kline(code, days=days)
for col in ['open','high','low','close','volume','amount','turn','pctChg']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

closes = df['close'].values
vols = df['volume'].values
turns = df['turn'].values
pcts = df['pctChg'].values
highs = df['high'].values
lows = df['low'].values

last_p = closes[-1]; max_p = max(closes); min_p = min(closes)
max_date = df.loc[df['close'].idxmax(),'date']
min_date = df.loc[df['close'].idxmin(),'date']
drawdown = (last_p - max_p)/max_p*100

ma5=np.mean(closes[-5:]); ma10=np.mean(closes[-10:]); ma20=np.mean(closes[-20:]); ma60=np.mean(closes[-60:])
avg_vol_5=np.mean(vols[-5:]); avg_vol_10=np.mean(vols[-10:]); avg_vol_20=np.mean(vols[-20:]); avg_vol_60=np.mean(vols[-60:])
avg_turn_5=np.mean(turns[-5:]); avg_turn_20=np.mean(turns[-20:])

pct_60d = (closes[-1]-closes[-60])/closes[-60]*100
limit_ups = sum(1 for p in pcts[-60:] if p>=9.5)

r5_hi=max(closes[-5:]); r5_lo=min(closes[-5:])
r5_avg=np.mean(closes[-5:]); r5_range=(r5_hi-r5_lo)/r5_avg*100
center_shift = (ma5-ma10)/ma10*100
ma_spread = (max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
min_vol_60 = min(vols[-60:])

# 支撑阻力
hi_20 = np.max(highs[-20:]); lo_20 = np.min(lows[-20:])
hi_60 = np.max(highs[-60:]); lo_60 = np.min(lows[-60:])

print("="*80)
print("  雪峰科技 (sh.603227) 120天深度分析")
print("="*80)
print(f"  昨收: {last_p:.2f}   120日最高: {max_p:.2f}({max_date})   最低: {min_p:.2f}({min_date})")
print(f"  从最高点回撤: {drawdown:.1f}%")
print(f"  近60日涨幅: {pct_60d:+.1f}%   近60日涨停: {limit_ups}次")
print()
print(f"  ── 均线 ──")
print(f"    MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}")
print(f"    现价vs MA60: {(last_p-ma60)/ma60*100:+.1f}%")
print(f"    均线间距: {ma_spread:.2f}%  {'粘合(<3%)' if ma_spread<3 else '发散'}")
ma_str1 = '金叉' if ma5>ma10 else '死叉'
ma_str2 = '金叉' if ma10>ma20 else '死叉'
print(f"    MA5/MA10: {ma_str1}   MA10/MA20: {ma_str2}")
print()
print(f"  ── 量能 ──")
print(f"    5日均量: {avg_vol_5:>12,.0f}   10日均量: {avg_vol_10:>12,.0f}")
print(f"    20日均量: {avg_vol_20:>12,.0f}   60日均量: {avg_vol_60:>12,.0f}")
print(f"    量比(5/20): {avg_vol_5/avg_vol_20:.2f}   量比(5/60): {avg_vol_5/avg_vol_60:.2f}")
print(f"    5日均换手: {avg_turn_5:.2f}%   20日均换手: {avg_turn_20:.2f}%")
print(f"    60日最低量: {min_vol_60:,.0f}   昨日量: {vols[-1]:,.0f}")
print()
print(f"  ── 波动 ──")
print(f"    近5日区间: {r5_lo:.2f}~{r5_hi:.2f}  波幅: {r5_range:.2f}%  {'窄幅' if r5_range<5 else '宽幅'}")
print(f"    重心偏移: {center_shift:+.2f}%  {'横盘' if abs(center_shift)<=1 else '下移' if center_shift<-1 else '上移'}")
print()
print(f"  ── 关键位 ──")
print(f"    20日高/低: {hi_20:.2f} / {lo_20:.2f}")
print(f"    60日高/低: {hi_60:.2f} / {lo_60:.2f}")
print()

# 连续缩量?
recent3 = vols[-3:]
consec_shrink = recent3[0] > recent3[1] > recent3[2] if len(recent3)==3 else False
near_min = vols[-1] <= min_vol_60 * 1.2
print(f"  ── 手册评分 ──")
# F2
if limit_ups >= 1:
    print(f"    F2 通过 (60日内{limit_ups}次涨停)")
else:
    print(f"    F2 不通过 (无涨停)")
# F3
if 10 <= pct_60d <= 60:
    print(f"    F3 通过 (60日涨{pct_60d:.0f}%)")
else:
    print(f"    F3 不通过 (60日涨{pct_60d:.0f}%)")
# F4
if -30 <= drawdown <= -15:
    print(f"    F4 通过 (回撤{drawdown:.0f}%)")
else:
    print(f"    F4 不通过 (回撤{drawdown:.0f}%)")
# F5
if last_p > ma60:
    print(f"    F5 通过 (现价>MA60)")
else:
    print(f"    F5 不通过 (现价<MA60 {(last_p-ma60)/ma60*100:.1f}%)")

# 缩量评分
vs = 0
if avg_vol_5/avg_vol_20 <= 0.6: vs += 1
if avg_vol_5/avg_vol_60 <= 0.5: vs += 1
if avg_turn_5 <= 3: vs += 1
if consec_shrink: vs += 1
if near_min: vs += 1
# 横盘评分
ss = 0
if r5_range <= 5: ss += 1
if abs(center_shift) <= 1: ss += 1
print(f"    缩量得分: {vs}/5  横盘得分: {ss}/2")
print(f"    连续缩量: {'是' if consec_shrink else '否'}  接近地量: {'是' if near_min else '否'}")
print()

print(f"  ── 近10日明细 ──")
for i in range(-10, 0):
    r = df.iloc[i]
    vr = r['volume']/avg_vol_20 if avg_vol_20>0 else 0
    bar = "阳" if r['close']>=r['open'] else "阴"
    print(f"    {r['date']}  收:{r['close']:>7.2f}  涨跌:{r['pctChg']:>+6.2f}%  量:{int(r['volume']):>10,}  量比:{vr:.2f}  换手:{r['turn']:.2f}%  {bar}")

print()
print(f"  ── 近20日量价关系 ──")
for i in range(-20, 0):
    r = df.iloc[i]
    vr = r['volume']/avg_vol_20 if avg_vol_20>0 else 0
    if r['pctChg']>0 and vr>1: s='放量上涨 ↑'
    elif r['pctChg']>0: s='缩量上涨 ↗'
    elif r['pctChg']<0 and vr>1: s='放量下跌 ↓'
    elif r['pctChg']<0: s='缩量下跌 ↘'
    else: s='平盘'
    print(f"    {r['date']}  收:{r['close']:>7.2f}  涨跌:{r['pctChg']:>+6.2f}%  量比:{vr:.2f}  {s}")

bs.logout()
