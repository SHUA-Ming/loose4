#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘环境研判脚本
自上而下分析：大盘指数 → 输出当前市场状态和操作建议
每次选股前先运行此脚本，判断"今天能不能操作"

分析维度：
  1. 趋势判断（均线多空排列）
  2. 量能状态（放量/缩量）
  3. 波动/情绪（振幅、连涨/连跌天数）
  4. 支撑/阻力位
  5. 综合结论：适合操作 / 谨慎 / 不操作
"""
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from sina_kline import fetch_kline
import numpy as np
from datetime import datetime

indices = [
    ('sh.000001', '上证指数'),
    ('sz.399001', '深证成指'),
    ('sz.399006', '创业板指'),
]

for code, name in indices:
    df = fetch_kline(code, days=120)
    if df.empty:
        print(f"{name}: 无数据")
        continue

    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    vols = df['volume'].values
    pcts = df['pctChg'].values

    last = closes[-1]
    last_date = df['date'].iloc[-1]

    # ── 均线 ──
    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:])
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:]) if len(closes) >= 60 else np.mean(closes)
    ma120 = np.mean(closes) if len(closes) >= 120 else np.mean(closes)

    # ── 趋势判定 ──
    bullish_count = sum([
        ma5 > ma10,
        ma10 > ma20,
        ma20 > ma60,
        last > ma60,
        last > ma20,
    ])
    if bullish_count >= 4:
        trend = "上升趋势"
    elif bullish_count >= 2:
        trend = "震荡/盘整"
    else:
        trend = "下降趋势"

    # ── 量能 ──
    avg_vol_5 = np.mean(vols[-5:])
    avg_vol_20 = np.mean(vols[-20:])
    avg_vol_60 = np.mean(vols[-60:]) if len(vols) >= 60 else np.mean(vols)
    vol_5_20 = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 0
    vol_5_60 = avg_vol_5 / avg_vol_60 if avg_vol_60 > 0 else 0

    if vol_5_20 > 1.3:
        vol_status = "显著放量"
    elif vol_5_20 > 1.0:
        vol_status = "温和放量"
    elif vol_5_20 > 0.7:
        vol_status = "缩量"
    else:
        vol_status = "极度缩量"

    # ── 连涨/连跌 ──
    streak = 0
    for i in range(len(pcts)-1, -1, -1):
        if streak == 0:
            streak = 1 if pcts[i] > 0 else -1
        elif (streak > 0 and pcts[i] > 0):
            streak += 1
        elif (streak < 0 and pcts[i] < 0):
            streak -= 1
        else:
            break

    # ── 近N日涨跌幅 ──
    def pct_n(n):
        if len(closes) >= n:
            return (closes[-1] - closes[-n]) / closes[-n] * 100
        return 0
    pct_5d = pct_n(5)
    pct_10d = pct_n(10)
    pct_20d = pct_n(20)

    # ── 振幅 ──
    recent_amp = np.mean([(highs[i]-lows[i])/closes[i]*100 for i in range(-5, 0)])

    # ── 支撑/阻力 ──
    # 近20日最高最低
    hi_20 = np.max(highs[-20:])
    lo_20 = np.min(lows[-20:])
    # 近60日最高最低
    hi_60 = np.max(highs[-60:]) if len(highs) >= 60 else np.max(highs)
    lo_60 = np.min(lows[-60:]) if len(lows) >= 60 else np.min(lows)

    support_levels = [ma20, ma60, lo_20]
    resist_levels = [hi_20, ma120]

    # ── 市场情绪评分(0-10, 10=极度乐观) ──
    emotion = 5
    if trend == "上升趋势": emotion += 2
    elif trend == "下降趋势": emotion -= 2
    if vol_5_20 > 1.2: emotion += 1
    elif vol_5_20 < 0.7: emotion -= 1
    if streak >= 3: emotion += 1
    elif streak <= -3: emotion -= 1
    if pct_5d > 3: emotion += 1
    elif pct_5d < -3: emotion -= 1
    emotion = max(0, min(10, emotion))

    # ── 操作建议 ──
    if emotion >= 7:
        advice = "积极做多（但注意追高风险）"
    elif emotion >= 5:
        advice = "可以操作（精选个股，控制仓位）"
    elif emotion >= 3:
        advice = "谨慎操作（轻仓，快进快出）"
    else:
        advice = "不宜操作（空仓观望或只卖不买）"

    # 近10日数据
    last10 = []
    for i in range(-10, 0):
        r = df.iloc[i]
        vr = r['volume'] / avg_vol_20 if avg_vol_20 > 0 else 0
        bar = "阳" if r['close'] >= r['open'] else "阴"
        last10.append(f"  {r['date']}  收:{r['close']:>9.2f}  涨跌:{r['pctChg']:>+6.2f}%  量比:{vr:.2f}  {bar}")

    # ═══ 输出 ═══
    print(f"\n{'='*80}")
    print(f"  {name} ({code})  截至 {last_date}")
    print(f"{'='*80}")
    print(f"  收盘价: {last:.2f}")
    print(f"  ── 趋势 ──")
    print(f"    MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}  MA120={ma120:.2f}")
    ma_order = "多头排列" if ma5>ma10>ma20>ma60 else "空头排列" if ma5<ma10<ma20<ma60 else "交叉纠缠"
    print(f"    均线排列: {ma_order}")
    print(f"    趋势判定: {trend}")
    print(f"    现价 vs MA20: {(last-ma20)/ma20*100:+.2f}%  vs MA60: {(last-ma60)/ma60*100:+.2f}%")

    print(f"  ── 量能 ──")
    print(f"    5日均量: {avg_vol_5:,.0f}  20日均量: {avg_vol_20:,.0f}")
    print(f"    量比(5/20): {vol_5_20:.2f}  量比(5/60): {vol_5_60:.2f}")
    print(f"    量能状态: {vol_status}")

    print(f"  ── 涨跌 ──")
    print(f"    近5日: {pct_5d:+.2f}%  近10日: {pct_10d:+.2f}%  近20日: {pct_20d:+.2f}%")
    print(f"    连涨/跌: {'连涨' if streak>0 else '连跌'}{abs(streak)}天")
    print(f"    近5日平均振幅: {recent_amp:.2f}%")

    print(f"  ── 关键位 ──")
    print(f"    20日高/低: {hi_20:.2f} / {lo_20:.2f}")
    print(f"    60日高/低: {hi_60:.2f} / {lo_60:.2f}")
    print(f"    上方阻力: {hi_20:.2f}(20日高) → {ma120:.2f}(MA120)")
    near_support = min(support_levels, key=lambda x: abs(x - last))
    print(f"    下方支撑: MA20={ma20:.2f}  MA60={ma60:.2f}  20日低={lo_20:.2f}")

    print(f"  ── 综合评分 ──")
    bars = "█" * emotion + "░" * (10 - emotion)
    print(f"    情绪指数: [{bars}] {emotion}/10")
    print(f"    操作建议: {advice}")

    print(f"  ── 近10日走势 ──")
    for line in last10:
        print(line)

# ═══ 综合研判 ═══
print(f"\n{'='*80}")
print(f"  自上而下综合研判")
print(f"{'='*80}")
print(f"  分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"  ────────────────────────────────────")
print(f"  决策流程:")
print(f"    第1步: 大盘方向 → 决定能不能操作")
print(f"    第2步: 板块强弱 → 决定操作哪个方向")
print(f"    第3步: 个股信号 → 决定具体买哪只")
print(f"  ────────────────────────────────────")
print(f"  注: 运行后请结合 market_mode.py、em_board_rotation.py 和 offline_screener.py")

print("\n分析完成。")
