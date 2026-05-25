#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
尾盘选股专用 - 强势板块内找回踩买点
当前市场刚经历大反弹(4/8)，标准蓄力模型很难找到，
改为：强势板块内找"反弹后缩量回踩、未追高"的短波机会
"""
import sys, warnings
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
warnings.filterwarnings('ignore')

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd
import numpy as np

init_db()
conn = get_connection()

# === 强势板块TOP（近5日动量排名前30%）===
sec_df = pd.read_sql('SELECT * FROM sector_daily ORDER BY industry, date', conn)
sec_df['avg_pct'] = pd.to_numeric(sec_df['avg_pct'], errors='coerce')
sector_momentum = {}
for ind, grp in sec_df.groupby('industry'):
    grp = grp.sort_values('date')
    if len(grp) >= 5:
        sector_momentum[ind] = float(np.mean(grp['avg_pct'].values[-5:]))

sorted_secs = sorted(sector_momentum.items(), key=lambda x: x[1], reverse=True)
top30_pct = int(len(sorted_secs) * 0.35)
strong_sectors = set(ind for ind, _ in sorted_secs[:top30_pct])
print("=" * 100)
print("  尾盘选股 — 强势板块回踩买入扫描")
print("=" * 100)
print(f"\n强势板块(TOP35%，共{len(strong_sectors)}个):")
for ind, m in sorted_secs[:top30_pct]:
    print(f"  {ind}: 近5日日均{m:+.2f}%")

# === 行业映射 ===
ind_df = pd.read_sql('SELECT code, industry FROM stock_industry', conn)
industry_map = dict(zip(ind_df['code'], ind_df['industry']))

# === 扫描 ===
codes = [r[0] for r in conn.execute('SELECT DISTINCT code FROM kline_daily').fetchall()]
results = []

for code in codes:
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    ind = industry_map.get(code, '')
    if ind not in strong_sectors:
        continue  # 只看强势板块

    df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', conn, params=[code])
    if len(df) < 60:
        continue
    for c in ['open','high','low','close','volume','amount','turn','pctChg']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['close','volume'])
    if len(df) < 60:
        continue

    cls = df['close'].values; ops = df['open'].values
    his = df['high'].values; los = df['low'].values
    vols = df['volume'].values; turns = df['turn'].values
    pcts = df['pctChg'].values; amts = df['amount'].values
    n = len(df); last = cls[-1]

    if last < 3 or last > 200: continue
    avg_amt_20 = np.mean(amts[-20:]) / 10000
    if avg_amt_20 < 2000: continue  # 要求成交额更好

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])

    # 流通市值粗估（日均成交额*换手率反推，不必精确）
    # 基本趋势要求：现价>MA20 (中短期向上)
    if last <= ma20:
        continue

    # 近60日有过涨停（有资金关注）
    p60 = pcts[-60:]
    if np.sum(p60 >= 9.5) < 1:
        continue

    # 排除项
    if np.any(pcts[-5:] < -5): continue  # 近5日无大跌
    if np.any(turns[-5:] > 8): continue  # 无异常换手

    # === 核心选股逻辑：反弹后回踩 ===
    # 近5日有过大涨(>=3%)，但最新1-2天出现缩量小幅回调或横盘
    # 这是"涨完歇一天"的回踩买点

    # 条件1: 近5日累计涨幅 > 3%（参与了本轮反弹）
    pct5_sum = np.sum(pcts[-5:])
    if pct5_sum < 3:
        continue

    # 条件2: 最新1天涨幅在 -2% ~ +1.5%（不是在追高, 也没崩）
    pct_today = pcts[-1]
    if pct_today > 1.5 or pct_today < -2:
        continue

    # 条件3: 最新日成交量缩量（量比 < 前日）
    vol_ratio_today = vols[-1] / vols[-2] if vols[-2] > 0 else 999
    # 允许小幅放量但不能太猛
    if vol_ratio_today > 1.3:
        continue

    # 条件4: 收盘价在MA5附近（回踩到5日均线支撑）
    dist_ma5 = (last - ma5) / ma5 * 100
    # 允许在MA5上方2%或下方1%以内
    if dist_ma5 > 3 or dist_ma5 < -2:
        continue

    # === 评分（原版手册7指标简化版）===
    score = 0
    details = []

    # 量能健康度
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    turn5 = np.mean(turns[-5:])

    # 缩量回调（最新1-2天缩量=好事）
    if vol_ratio_today < 0.8:
        score += 3; details.append("缩量回踩✅")
    elif vol_ratio_today < 1.0:
        score += 2; details.append("温和缩量⚠️")
    else:
        score += 1; details.append("量能平稳")

    # 均线多头排列
    if ma5 > ma10 > ma20:
        score += 4; details.append("均线多头✅")
    elif ma5 > ma10:
        score += 2; details.append("短线向上⚠️")
    else:
        details.append("均线纠缠")

    # 回踩幅度适中
    if -1 <= pct_today <= 0:
        score += 3; details.append("回踩到位✅")
    elif 0 < pct_today <= 0.5:
        score += 2; details.append("微涨企稳⚠️")
    elif -2 <= pct_today < -1:
        score += 1; details.append("回调偏深")
    else:
        details.append("未回踩")

    # 5日涨幅不过分（吃第一口肉空间还有）
    if 3 <= pct5_sum <= 8:
        score += 3; details.append("空间充裕✅")
    elif 8 < pct5_sum <= 12:
        score += 1; details.append("涨幅偏大⚠️")
    else:
        details.append("涨幅过大")

    # 距MA60有安全垫
    above_ma60 = (last - ma60) / ma60 * 100
    if 2 <= above_ma60 <= 15:
        score += 2; details.append(f"安全垫{above_ma60:.1f}%✅")
    elif above_ma60 > 15:
        score += 1; details.append(f"偏高{above_ma60:.1f}%⚠️")

    # K线形态：收红或十字星加分
    if cls[-1] >= ops[-1]:
        score += 1; details.append("收红")
    # 下影线
    body_last = abs(cls[-1] - ops[-1])
    lower_shadow = min(ops[-1], cls[-1]) - los[-1]
    if body_last > 0 and lower_shadow >= 2 * body_last:
        score += 1; details.append("下影线")

    # 板块动量加分
    sec_m = sector_momentum.get(ind, 0)
    if sec_m > 1.5:
        score += 2; details.append(f"板块强势{sec_m:+.1f}%")
    elif sec_m > 0.5:
        score += 1; details.append(f"板块偏强{sec_m:+.1f}%")

    # 近60日最高点回撤（浅回撤更优）
    max60 = np.max(cls[-60:])
    dd60 = (last - max60) / max60 * 100

    # MA5金叉MA10
    has_cross = False
    if n >= 12:
        for i in [-1, -2, -3]:
            pm5 = np.mean(cls[i-5:i]); pm10 = np.mean(cls[i-10:i])
            cm5 = np.mean(cls[i-4:i+1]); cm10 = np.mean(cls[i-9:i+1])
            if pm5 <= pm10 and cm5 > cm10:
                has_cross = True; break
    signals = []
    if has_cross:
        signals.append("MA5金叉")
    if cls[-1] > np.max(cls[-15:-1]) if n >= 15 else False:
        signals.append("突破前高")
    if vols[-1] >= vol5 * 1.5 and pcts[-1] > 2:
        signals.append("放量突破")

    results.append({
        'code': code, 'price': last, 'score': score,
        'details': ' '.join(details),
        'signals': '|'.join(signals) if signals else '',
        'pct_today': pct_today, 'pct5': pct5_sum,
        'vol_ratio': vol_ratio_today, 'vr520': vr520, 'turn5': turn5,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'dd60': dd60, 'above_ma60': above_ma60,
        'industry': ind, 'dist_ma5': dist_ma5,
    })

# 排序：评分 > 板块动量 > 回踩幅度
results.sort(key=lambda x: (-x['score'], -sector_momentum.get(x['industry'], 0), abs(x['pct_today'])))

print(f"\n通过筛选: {len(results)} 只\n")

if results:
    print(f"{'排名':>4s} {'代码':<12s} {'价格':>7s} {'今涨':>7s} {'5日涨':>7s} {'评分':>4s} {'板块':<20s} {'量比':>6s} {'距MA5':>6s} {'回撤60':>7s}  指标明细")
    print("-" * 150)
    for rank, c in enumerate(results[:25], 1):
        sig = f" 💥{c['signals']}" if c['signals'] else ""
        print(f"{rank:>4d} {c['code']:<12s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['pct5']:>+6.2f}% {c['score']:>4d} {c['industry']:<18s} {c['vol_ratio']:>6.2f} {c['dist_ma5']:>+5.2f}% {c['dd60']:>+6.1f}%  {c['details']}{sig}")

    print()
    print("=" * 100)
    print("  TOP5 详细分析 & 操作计划")
    print("=" * 100)
    for rank, c in enumerate(results[:5], 1):
        print(f"\n  ── #{rank} {c['code']} [{c['industry']}] ──")
        print(f"  价格: {c['price']:.2f}  今日: {c['pct_today']:+.2f}%  近5日: {c['pct5']:+.2f}%")
        print(f"  MA5={c['ma5']:.2f} MA10={c['ma10']:.2f} MA20={c['ma20']:.2f} MA60={c['ma60']:.2f}")
        print(f"  量比(今/昨): {c['vol_ratio']:.2f}  量比(5/20): {c['vr520']:.2f}  5日换手: {c['turn5']:.2f}%")
        print(f"  距MA5: {c['dist_ma5']:+.2f}%  距MA60: {c['above_ma60']:+.1f}%  60日高点回撤: {c['dd60']:+.1f}%")
        print(f"  评分: {c['score']}/20  指标: {c['details']}")
        if c['signals']:
            print(f"  信号: {c['signals']}")
        # 操作计划
        entry = c['price']
        tp1 = entry * 1.03
        tp2 = entry * 1.05
        stop_soft = entry * 0.985  # D1收盘止损-1.5%
        stop_hard = entry * 0.97   # D1盘中硬止损-3%
        print(f"  ── 短波操作计划（持有1-2天）──")
        print(f"  尾盘买入价: ≤{entry:.2f}")
        print(f"  止盈1(+3%): {tp1:.2f} → 卖1/2")
        print(f"  止盈2(+5%): {tp2:.2f} → 全清")
        print(f"  收盘止损(D1 -1.5%): {stop_soft:.2f}")
        print(f"  盘中硬止损(D1 -3%): {stop_hard:.2f}")
        print(f"  最长持有: 2天，D3收盘前必须清仓")
else:
    print("  （无符合条件的候选）")

conn.close()
print("\n扫描完成。")
