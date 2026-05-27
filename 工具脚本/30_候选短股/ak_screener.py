#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 akshare 的实时选股器
使用东财实时行情+历史K线，比baostock快10倍
筛选逻辑参照《选股参数手册》
"""
import sys, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

print("=" * 80)
print(f"  akshare 实时选股器  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 80)

# ═══ Step 1: 拉取全市场实时行情 ═══
print("\n[1/4] 拉取全市场实时行情...")
df_all = ak.stock_zh_a_spot_em()  # 东财全A实时行情
print(f"  获取到 {len(df_all)} 只股票")

# ═══ Step 2: 前置过滤 ═══
print("[2/4] 前置过滤...")
df = df_all.copy()

# 只要沪深主板 (60xxxx, 00xxxx)
df = df[df['代码'].str.match(r'^(60|00)\d{4}$')]

# 排除ST
df = df[~df['名称'].str.contains('ST|st|退', na=False)]

# 价格范围
df = df[(df['最新价'] >= 3) & (df['最新价'] <= 200)]

# 流通市值 30亿~300亿
if '流通市值' in df.columns:
    df = df[(df['流通市值'] >= 30e8) & (df['流通市值'] <= 300e8)]

# 成交额 > 1000万（排除僵尸股）
if '成交额' in df.columns:
    df = df[df['成交额'] >= 1000e4]

# 换手率合理 (排除异常高换手)
if '换手率' in df.columns:
    df = df[(df['换手率'] >= 0.3) & (df['换手率'] <= 8)]

print(f"  前置过滤后: {len(df)} 只")

# ═══ Step 3: 逐股拉取历史K线并评分 ═══
print("[3/4] 逐股分析评分（取前100只候选）...")

# 先按今日涨幅排序取合理范围（不追涨停，也不要下跌的）
df = df.sort_values('涨跌幅', ascending=False)
# 选取今日涨幅在 -1% ~ +7% 的（尾盘买入不追涨停，避免高位接盘）
df = df[(df['涨跌幅'] >= -1) & (df['涨跌幅'] <= 7)]
# 取前200只逐一分析（按涨幅中等优先——3%附近趋势确认但不追高）
df['rank_score'] = -abs(df['涨跌幅'] - 3)  # 越接近3%得分越高
df = df.sort_values('rank_score', ascending=False).head(200)

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')

results = []
errors = 0
for i, (_, row) in enumerate(df.iterrows()):
    code = row['代码']
    name = row['名称']
    cur_price = row['最新价']
    today_pct = row['涨跌幅']
    today_vol = row.get('成交量', 0)
    today_amt = row.get('成交额', 0)
    today_turn = row.get('换手率', 0)

    if i % 20 == 0 and i > 0:
        print(f"  已分析 {i}/{len(df)} 只... 候选 {len(results)} 只")

    try:
        # 拉取120天日K
        hist = ak.stock_zh_a_hist(symbol=code, period="daily",
                                   start_date=start_date, end_date=end_date, adjust="qfq")
        if hist is None or len(hist) < 60:
            continue

        cls = hist['收盘'].values.astype(float)
        ops = hist['开盘'].values.astype(float)
        his = hist['最高'].values.astype(float)
        los = hist['最低'].values.astype(float)
        vols = hist['成交量'].values.astype(float)
        amts = hist['成交额'].values.astype(float)
        pcts = hist['涨跌幅'].values.astype(float)
        turns = hist['换手率'].values.astype(float) if '换手率' in hist.columns else np.zeros(len(hist))
        n = len(cls)

        # ── 前置条件 F1-F5 ──
        ma60 = np.mean(cls[-60:])
        if cur_price <= ma60:
            continue  # F5: 现价须 > MA60

        c60 = cls[-60:]
        pct60 = (cur_price - c60[0]) / c60[0] * 100
        if not (10 <= pct60 <= 60):
            continue  # F3: 60日涨幅10~60%

        max60 = np.max(c60)
        dd60 = (cur_price - max60) / max60 * 100
        if not (-20 <= dd60 <= -5):
            continue  # F4: 从高点回撤5~20%

        # 检查60日内是否有涨停
        limit_ups = np.sum(pcts[-60:] >= 9.5)
        if limit_ups < 1:
            continue  # F2

        # 安全检查：排除近5日暴跌或换手异常
        if n >= 5 and np.any(pcts[-5:] < -5):
            continue
        if n >= 5 and np.any(turns[-5:] > 8):
            continue

        # ── 核心指标评分 ──
        score = 0
        details = []

        # ① 缩量程度
        vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
        vr520 = vol5 / vol20 if vol20 > 0 else 999
        vr560 = vol5 / vol60 if vol60 > 0 else 999
        turn5 = np.mean(turns[-5:])
        vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
        vol_min60 = np.min(vols[-60:])
        floor_vol = vols[-1] <= vol_min60 * 1.2 if vol_min60 > 0 else False

        sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, turn5 <= 2, vol_dec, floor_vol])
        if sc1 >= 3:
            score += 5; details.append("①缩量✅")
        elif sc1 >= 1:
            score += 1; details.append("①缩量⚠️")
        else:
            details.append("①缩量❌")

        # ② 横盘整理
        c5 = cls[-5:]
        rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
        cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
        ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])

        sc2 = sum([rng5 <= 5, abs(cs) <= 1, cur_price > ma60])
        if sc2 >= 3:
            score += 4; details.append("②横盘✅")
        elif sc2 >= 2:
            score += 2; details.append("②横盘⚠️")
        else:
            details.append("②横盘❌")

        # ③ 均线收敛
        ma_sp = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ((ma5+ma10+ma20)/3) * 100
        sc3 = sum([ma_sp <= 3, cur_price > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
        if sc3 >= 3:
            score += 4; details.append("③均线✅")
        elif sc3 >= 2:
            score += 2; details.append("③均线⚠️")
        else:
            details.append("③均线❌")

        # ④ 实体缩小
        bodies = np.abs(cls - ops)
        br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
        amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
        pct_abs5 = np.mean(np.abs(pcts[-5:]))
        sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
        if sc4 >= 2:
            score += 3; details.append("④实体✅")
        elif sc4 >= 1:
            score += 1; details.append("④实体⚠️")
        else:
            details.append("④实体❌")

        # ⑤ 下影线试探
        lsb = 0
        for j in range(-5, 0):
            o, c, h, l = ops[j], cls[j], his[j], los[j]
            body = abs(c - o)
            ls_len = min(o, c) - l
            if c > o and body > 0 and ls_len >= 2 * body and pcts[j] <= 2:
                lsb += 1
        if lsb >= 1:
            score += 3; details.append("⑤下影✅")
        else:
            details.append("⑤下影—")

        # ⑥ 十字星
        doji = 0
        for j in range(-5, 0):
            o, c, h, l = ops[j], cls[j], his[j], los[j]
            body = abs(c - o)
            bp = body / o * 100 if o > 0 else 999
            shadow = max(h - max(o, c), min(o, c) - l)
            if bp <= 0.5 and body > 0 and shadow >= 2 * body:
                doji += 1
        if doji >= 2:
            score += 2; details.append("⑥十字✅")
        elif doji >= 1:
            score += 1; details.append("⑥十字⚠️")
        else:
            details.append("⑥十字—")

        # ⑦ 红绿交替
        colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
        no3 = all(not (colors[j] == colors[j+1] == colors[j+2]) for j in range(3))
        pct5r = all(-2 <= pcts[j] <= 2 for j in range(-5, 0))
        pct5s = np.sum(pcts[-5:])
        if no3 and pct5r and -2 <= pct5s <= 3:
            score += 2; details.append("⑦交替✅")
        else:
            details.append("⑦交替❌")

        # ── 信号检测 ──
        signals = []
        if n >= 12:
            for j in [-1, -2, -3]:
                pm5 = np.mean(cls[j-5:j])
                pm10 = np.mean(cls[j-10:j])
                cm5 = np.mean(cls[j-4:j+1])
                cm10 = np.mean(cls[j-9:j+1])
                if pm5 <= pm10 and cm5 > cm10:
                    signals.append("MA5金叉MA10")
                    break
        if vols[-1] >= vol5 * 2 and pcts[-1] > 3:
            signals.append("放量突破")
        if n >= 15 and cls[-1] > np.max(cls[-15:-1]) and pcts[-1] > 0:
            signals.append("突破横盘上沿")

        grade = 'A' if score >= 18 else 'B' if score >= 12 else 'C'

        if score >= 12:  # 放宽到12分也显示
            results.append({
                'code': code, 'name': name, 'price': cur_price,
                'score': score, 'grade': grade,
                'details': ' '.join(details),
                'signals': '|'.join(signals) if signals else '',
                'vr520': vr520, 'turn5': turn5, 'ma_sp': ma_sp,
                'rng5': rng5, 'pct60': pct60, 'dd60': dd60, 'cs': cs,
                'pct_today': today_pct,
                'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            })
    except Exception as e:
        errors += 1
        continue

# ═══ Step 4: 输出结果 ═══
print(f"\n[4/4] 出结果！")
results.sort(key=lambda x: x['score'], reverse=True)

print(f"\n  通过筛选: {len(results)} 只  (分析错误跳过: {errors})")
print()
if results:
    header = f"{'排名':>4s} {'代码':<8s} {'名称':<8s} {'价格':>7s} {'今涨':>7s} {'评分':>4s} {'级':>2s} {'量比5/20':>8s} {'5日换手':>7s} {'均线距':>6s} {'5日幅':>6s} {'60日涨':>7s} {'回撤':>7s}  指标明细"
    print(header)
    print("-" * 160)
    for rank, c in enumerate(results[:20], 1):
        sig = f" >> {c['signals']}" if c['signals'] else ""
        print(f"{rank:>4d} {c['code']:<8s} {c['name']:<8s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['score']:>4d} {c['grade']:>2s} {c['vr520']:>8.2f} {c['turn5']:>6.2f}% {c['ma_sp']:>5.2f}% {c['rng5']:>5.2f}% {c['pct60']:>+6.1f}% {c['dd60']:>+6.1f}%  {c['details']}{sig}")

    print()
    print("=" * 80)
    print("  TOP 5 蓄力候选 详细分析")
    print("=" * 80)
    for rank, c in enumerate(results[:5], 1):
        print(f"\n  ── #{rank} {c['name']}（{c['code']}）──")
        print(f"  价格: {c['price']:.2f}  今日涨跌: {c['pct_today']:+.2f}%")
        print(f"  MA5={c['ma5']:.2f}  MA10={c['ma10']:.2f}  MA20={c['ma20']:.2f}  MA60={c['ma60']:.2f}")
        print(f"  量比(5/20): {c['vr520']:.2f}  5日换手: {c['turn5']:.2f}%  均线间距: {c['ma_sp']:.2f}%")
        print(f"  5日波幅: {c['rng5']:.2f}%  重心偏移: {c['cs']:+.2f}%")
        print(f"  60日涨幅: {c['pct60']:+.1f}%  高点回撤: {c['dd60']:+.1f}%")
        print(f"  评分: {c['score']}/23 ({c['grade']}级)")
        print(f"  指标: {c['details']}")
        if c['signals']:
            print(f"  信号: {c['signals']}")
        entry = max(c['ma5'], c['ma10'])
        tp1 = c['price'] * 1.03
        tp2 = c['price'] * 1.05
        stop = c['price'] * 0.98
        hard_stop = c['ma60'] * 0.98
        print(f"  ── 操作计划 ──")
        print(f"  入场区间: {entry:.2f} 附近 (均线支撑处)")
        print(f"  止盈1(+3%): {tp1:.2f}  止盈2(+5%): {tp2:.2f}")
        print(f"  止损(-2%): {stop:.2f}  硬止损(破MA60): {hard_stop:.2f}")
else:
    print("  （无符合条件的蓄力候选）")

print(f"\n分析完成。 {datetime.now().strftime('%H:%M:%S')}")
