#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
筹码成本分析模块 (Chip Cost Distribution Analysis)
═══════════════════════════════════════════════════
基于历史K线的成交量-价格分布，估算当前筹码结构。

核心原理：
  每日成交量按当日价格区间（开高低收）分布，采用三角分布以VWAP为中心。
  越近期的成交量权重越大（旧筹码随时间被换手消化）。

提供的分析维度：
  1. 筹码密集区（套牢盘/获利盘集中区间）
  2. 获利盘比例（当前价以下的筹码占比）
  3. 成本重心（加权平均成本价）
  4. 上方套牢压力区 & 下方支撑区
  5. 筹码集中度（90%筹码所在价格区间的宽度/均价）

用法：
  from chip_cost import analyze_chip_cost
  result = analyze_chip_cost(df, current_price=25.0)
  print_chip_report(result)

  # 或者从数据库直接分析
  result = analyze_chip_from_db('sh.600251')
"""

import numpy as np
import pandas as pd


def _build_chip_distribution(closes, opens, highs, lows, volumes, turnover_rates=None,
                              bins=100, decay_half_life=30):
    """
    构建筹码分布

    参数:
      closes, opens, highs, lows, volumes: 历史K线数据（numpy array）
      turnover_rates: 换手率数组（可选，用于更精准的衰减）
      bins: 价格区间划分数量
      decay_half_life: 半衰期（交易日数），越近期的筹码权重越大

    返回:
      price_axis: 价格轴 (array)
      chip_dist: 各价格区间的筹码量 (array, 已归一化为比例)
    """
    n = len(closes)
    if n < 10:
        return np.array([]), np.array([])

    # 确定价格范围（取历史最低到最高，留点余量）
    price_min = max(np.min(lows) * 0.95, 0.01)
    price_max = np.max(highs) * 1.05
    price_axis = np.linspace(price_min, price_max, bins)
    bin_width = price_axis[1] - price_axis[0]

    chip_dist = np.zeros(bins)

    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        vol = volumes[i]
        if vol <= 0 or h <= 0:
            continue

        # 当日VWAP近似（用 (高+低+收)/3 近似，因为没有分钟数据）
        vwap = (h + l + c) / 3.0

        # 时间衰减权重：距今越远的成交量，被换手消化的比例越高
        days_ago = n - 1 - i
        if turnover_rates is not None and turnover_rates[i] > 0:
            # 用实际换手率估算衰减：后续每天有 turn% 的筹码被换手
            # 留存率 = 连乘(1 - turn_j) for j = i+1 to n-1
            remain = 1.0
            for j in range(i + 1, n):
                tr = turnover_rates[j] / 100.0 if turnover_rates[j] > 0 else 0.01
                remain *= (1.0 - min(tr, 0.5))  # 单日最多衰减50%
                if remain < 0.01:
                    break
            weight = remain
        else:
            # 简单半衰期指数衰减
            weight = 0.5 ** (days_ago / decay_half_life)

        weighted_vol = vol * weight

        # 将成交量按三角分布分配到 [low, high] 区间，以vwap为峰值
        # 找到 low, vwap, high 对应的 bin 索引
        idx_lo = np.searchsorted(price_axis, l)
        idx_hi = np.searchsorted(price_axis, h)
        idx_vwap = np.searchsorted(price_axis, vwap)

        idx_lo = max(0, min(idx_lo, bins - 1))
        idx_hi = max(0, min(idx_hi, bins - 1))
        idx_vwap = max(idx_lo, min(idx_vwap, idx_hi))

        if idx_hi <= idx_lo:
            # 当日几乎没有波动，全部集中在一个price bin
            chip_dist[idx_lo] += weighted_vol
        else:
            # 三角分布：从 idx_lo 到 idx_vwap 线性上升，idx_vwap 到 idx_hi 线性下降
            for bi in range(idx_lo, idx_hi + 1):
                if bi <= idx_vwap:
                    # 上升段
                    if idx_vwap > idx_lo:
                        tri_weight = (bi - idx_lo) / (idx_vwap - idx_lo)
                    else:
                        tri_weight = 1.0
                else:
                    # 下降段
                    if idx_hi > idx_vwap:
                        tri_weight = (idx_hi - bi) / (idx_hi - idx_vwap)
                    else:
                        tri_weight = 1.0
                # 确保至少有0.1的底部权重（成交量不会完全为0）
                tri_weight = max(tri_weight, 0.1)
                chip_dist[bi] += weighted_vol * tri_weight

    # 归一化为比例
    total = np.sum(chip_dist)
    if total > 0:
        chip_dist = chip_dist / total

    return price_axis, chip_dist


def analyze_chip_cost(df, current_price=None):
    """
    对一只股票进行筹码成本分析

    参数:
      df: DataFrame，必须含 open, high, low, close, volume 列（数值类型）
          可选列: turn (换手率)
      current_price: 当前价，默认取 df 最后一天收盘价

    返回: dict
      {
        'current_price': float,
        'cost_center': float,          # 成本重心（加权平均成本）
        'profit_ratio': float,         # 获利盘比例 (0~1)
        'trapped_ratio': float,        # 套牢盘比例 (0~1)
        'chip_concentration': float,   # 筹码集中度 (90%筹码价幅/均价, 越小越集中)
        'support_zone': (lo, hi),      # 下方最大支撑筹码密集区
        'resistance_zone': (lo, hi),   # 上方最大套牢筹码密集区
        'dense_zones': [(lo, hi, pct), ...],  # 所有密集区（从大到小）
        'price_axis': array,
        'chip_dist': array,
        'short_term_cost': float,      # 近20日成本重心
        'advice': str,                 # 简短建议
      }
    """
    if df is None or len(df) < 20:
        return None

    closes = df['close'].values.astype(float)
    opens = df['open'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    volumes = df['volume'].values.astype(float)
    turns = df['turn'].values.astype(float) if 'turn' in df.columns else None

    if current_price is None:
        current_price = float(closes[-1])

    # 构建筹码分布
    price_axis, chip_dist = _build_chip_distribution(
        closes, opens, highs, lows, volumes,
        turnover_rates=turns, bins=120, decay_half_life=25
    )

    if len(price_axis) == 0:
        return None

    bin_width = price_axis[1] - price_axis[0]

    # 1. 成本重心
    cost_center = np.sum(price_axis * chip_dist) / max(np.sum(chip_dist), 1e-10)

    # 2. 获利盘/套牢盘比例
    cur_idx = np.searchsorted(price_axis, current_price)
    cur_idx = min(cur_idx, len(chip_dist) - 1)
    profit_ratio = float(np.sum(chip_dist[:cur_idx + 1]))  # 当前价以下 = 获利盘
    trapped_ratio = 1.0 - profit_ratio

    # 3. 筹码集中度（90%筹码的价格范围）
    cumsum = np.cumsum(chip_dist)
    p5_idx = np.searchsorted(cumsum, 0.05)
    p95_idx = np.searchsorted(cumsum, 0.95)
    if p95_idx < len(price_axis) and p5_idx < len(price_axis):
        chip_range_90 = price_axis[min(p95_idx, len(price_axis) - 1)] - price_axis[p5_idx]
        chip_concentration = chip_range_90 / max(cost_center, 0.01)
    else:
        chip_concentration = 0

    # 4. 找密集区（峰值检测）
    # 用滑动窗口平滑后找局部峰值
    kernel_size = 7
    if len(chip_dist) > kernel_size:
        smoothed = np.convolve(chip_dist, np.ones(kernel_size) / kernel_size, mode='same')
    else:
        smoothed = chip_dist.copy()

    dense_zones = []
    # 找所有局部峰值
    for i in range(2, len(smoothed) - 2):
        if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1]:
            if smoothed[i] > np.mean(smoothed) * 1.5:  # 峰值要超过平均1.5倍
                # 找峰值的宽度（连续高于平均的区间）
                left = i
                while left > 0 and smoothed[left] > np.mean(smoothed) * 0.8:
                    left -= 1
                right = i
                while right < len(smoothed) - 1 and smoothed[right] > np.mean(smoothed) * 0.8:
                    right += 1
                zone_lo = price_axis[left]
                zone_hi = price_axis[min(right, len(price_axis) - 1)]
                zone_pct = float(np.sum(chip_dist[left:right + 1]))
                dense_zones.append((zone_lo, zone_hi, zone_pct))

    # 去重合并重叠区间
    dense_zones = _merge_zones(dense_zones)
    dense_zones.sort(key=lambda x: x[2], reverse=True)

    # 5. 上方阻力/下方支撑
    support_zone = None
    resistance_zone = None
    for z in dense_zones:
        if z[1] < current_price and support_zone is None:
            support_zone = (z[0], z[1])
        elif z[0] > current_price and resistance_zone is None:
            resistance_zone = (z[0], z[1])

    # 如果没有明确密集区，用简单百分位
    if support_zone is None:
        # 取当前价下方5-15%区间作为观察
        support_zone = (current_price * 0.92, current_price * 0.97)
    if resistance_zone is None:
        resistance_zone = (current_price * 1.03, current_price * 1.08)

    # 6. 近20日短期成本
    if len(df) >= 20:
        recent = df.tail(20)
        rc = recent['close'].values.astype(float)
        rv = recent['volume'].values.astype(float)
        total_rv = np.sum(rv)
        short_term_cost = np.sum(rc * rv) / total_rv if total_rv > 0 else float(closes[-1])
    else:
        short_term_cost = cost_center

    # 7. 生成建议
    advice = _generate_advice(current_price, cost_center, profit_ratio,
                               trapped_ratio, resistance_zone, support_zone,
                               chip_concentration, short_term_cost)

    return {
        'current_price': current_price,
        'cost_center': cost_center,
        'profit_ratio': profit_ratio,
        'trapped_ratio': trapped_ratio,
        'chip_concentration': chip_concentration,
        'support_zone': support_zone,
        'resistance_zone': resistance_zone,
        'dense_zones': dense_zones[:5],
        'price_axis': price_axis,
        'chip_dist': chip_dist,
        'short_term_cost': short_term_cost,
        'advice': advice,
    }


def _merge_zones(zones):
    """合并重叠的密集区"""
    if not zones:
        return []
    zones.sort(key=lambda x: x[0])
    merged = [zones[0]]
    for z in zones[1:]:
        if z[0] <= merged[-1][1]:
            # 重叠，合并
            old = merged[-1]
            merged[-1] = (old[0], max(old[1], z[1]), old[2] + z[2])
        else:
            merged.append(z)
    return merged


def _generate_advice(cur, cost_center, profit_ratio, trapped_ratio,
                     resistance_zone, support_zone, concentration, short_cost):
    """根据筹码结构生成简短建议"""
    parts = []

    # 获利盘过高 → 有抛压
    if profit_ratio > 0.90:
        parts.append("⚠️获利盘>90%，短线抛压大，不宜追高")
    elif profit_ratio > 0.80:
        parts.append("⚠️获利盘>80%，注意高位套现压力")
    elif profit_ratio < 0.30:
        parts.append("🟢获利盘<30%，大部分筹码套牢，抛压小（如有资金进场易拉升）")
    elif profit_ratio < 0.50:
        parts.append("🟡获利盘<50%，仍有套牢盘压制")

    # 当前价在密集区内
    if resistance_zone and resistance_zone[0] <= cur * 1.03:
        parts.append(f"⚠️上方{resistance_zone[0]:.2f}~{resistance_zone[1]:.2f}有套牢密集区，短波进场需谨慎")
    elif resistance_zone and resistance_zone[0] <= cur * 1.06:
        parts.append(f"🟡上方{resistance_zone[0]:.2f}~{resistance_zone[1]:.2f}附近有压力")

    # 成本重心与现价的关系
    cost_diff_pct = (cur - cost_center) / cost_center * 100
    if cost_diff_pct > 10:
        parts.append(f"现价高于成本重心{cost_diff_pct:.1f}%，获利了结动力强")
    elif cost_diff_pct < -5:
        parts.append(f"现价低于成本重心{abs(cost_diff_pct):.1f}%，可能处于价值区间")

    # 筹码集中度
    if concentration < 0.15:
        parts.append("🟢筹码高度集中，控盘良好")
    elif concentration > 0.35:
        parts.append("⚠️筹码分散，多空分歧大")

    if not parts:
        parts.append("筹码结构中性")

    return "；".join(parts)


def print_chip_report(result, name=""):
    """打印筹码分析报告"""
    if result is None:
        print("  筹码分析：数据不足，无法计算")
        return

    r = result
    prefix = f"  {name} " if name else "  "

    print()
    print("=" * 60)
    print(f"{prefix}筹码成本分析")
    print("=" * 60)
    print(f"  当前价: {r['current_price']:.2f}")
    print(f"  成本重心: {r['cost_center']:.2f}  (偏移: {(r['current_price']/r['cost_center']-1)*100:+.1f}%)")
    print(f"  近20日成本: {r['short_term_cost']:.2f}")
    print(f"  获利盘: {r['profit_ratio']:.1%}  套牢盘: {r['trapped_ratio']:.1%}")
    print(f"  筹码集中度: {r['chip_concentration']:.2%} {'(高度集中)' if r['chip_concentration']<0.15 else '(较集中)' if r['chip_concentration']<0.25 else '(分散)'}")

    # 密集区
    if r['dense_zones']:
        print(f"  ── 筹码密集区（套牢/支撑区间）──")
        for i, (lo, hi, pct) in enumerate(r['dense_zones'][:4]):
            pos = "📍当前价附近" if lo <= r['current_price'] <= hi else \
                  "🔴上方套牢" if lo > r['current_price'] else \
                  "🟢下方支撑"
                  
            print(f"    {i+1}. {lo:.2f} ~ {hi:.2f}  筹码占比{pct:.1%}  {pos}")

    print(f"  ── 关键区间 ──")
    if r['support_zone']:
        print(f"    下方支撑: {r['support_zone'][0]:.2f} ~ {r['support_zone'][1]:.2f}")
    if r['resistance_zone']:
        print(f"    上方压力: {r['resistance_zone'][0]:.2f} ~ {r['resistance_zone'][1]:.2f}")

    # 简易筹码分布图（文字版）
    _print_chip_ascii(r['price_axis'], r['chip_dist'], r['current_price'])

    print(f"\n  💡 {r['advice']}")
    print()


def _print_chip_ascii(price_axis, chip_dist, current_price, rows=20):
    """打印简易文字版筹码分布图"""
    if len(price_axis) == 0:
        return

    # 缩减到 rows 行
    n = len(price_axis)
    step = max(1, n // rows)
    display_prices = []
    display_chips = []

    for i in range(0, n, step):
        end = min(i + step, n)
        display_prices.append(np.mean(price_axis[i:end]))
        display_chips.append(np.sum(chip_dist[i:end]))

    max_chip = max(display_chips) if display_chips else 1
    bar_width = 30

    print(f"  ── 筹码分布图 ──")
    for i in range(len(display_prices) - 1, -1, -1):
        p = display_prices[i]
        c = display_chips[i]
        bar_len = int(c / max_chip * bar_width) if max_chip > 0 else 0

        # 标记当前价
        marker = " ◄当前" if abs(p - current_price) / current_price < 0.02 else ""
        # 颜色标记：当前价以上=红(套牢)，以下=绿(获利)
        bar_char = "▓" if p > current_price else "█"

        print(f"    {p:>7.2f} |{bar_char * bar_len}{'░' * (bar_width - bar_len)}| {c:.2%}{marker}")


def analyze_chip_from_db(code, current_price=None):
    """
    从数据库缓存读取K线数据进行筹码分析

    参数:
      code: 股票代码，如 'sh.600251'
      current_price: 当前价（可选，不传则用最后一天收盘价）

    返回: analyze_chip_cost() 的返回值
    """
    from db_cache import get_connection, init_db
    init_db()
    conn = get_connection()
    df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', conn, params=[code])
    conn.close()

    if df.empty or len(df) < 20:
        print(f"  {code} 数据不足({len(df)}天)，需要至少20天数据")
        return None

    for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close', 'volume'])

    return analyze_chip_cost(df, current_price=current_price)


def chip_entry_check(result, planned_entry_lo, planned_entry_hi):
    """
    检查计划入场区间是否与套牢密集区重叠

    参数:
      result: analyze_chip_cost() 的返回值
      planned_entry_lo, planned_entry_hi: 计划入场价格区间

    返回: (safe: bool, warning: str)
    """
    if result is None:
        return True, ""

    warnings = []
    for lo, hi, pct in result.get('dense_zones', []):
        if pct < 0.05:
            continue  # 忽略小筹码区

        # 检查是否有重叠
        overlap = max(0, min(hi, planned_entry_hi) - max(lo, planned_entry_lo))
        if overlap > 0 and lo > result['current_price'] * 0.99:
            # 入场区间与上方套牢密集区重叠
            warnings.append(
                f"⚠️入场区间{planned_entry_lo:.2f}~{planned_entry_hi:.2f}"
                f"与套牢密集区{lo:.2f}~{hi:.2f}(占比{pct:.1%})重叠，"
                f"短波可能被套牢盘抛压压住"
            )

    if result.get('profit_ratio', 0) > 0.88:
        warnings.append(
            f"⚠️获利盘{result['profit_ratio']:.0%}过高，存在集中止盈抛压风险"
        )

    if warnings:
        return False, "；".join(warnings)
    return True, "✅入场区间筹码结构安全"


# ═══ 命令行直接运行 ═══
if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python chip_cost.py <代码> [当前价]")
        print("例如: python chip_cost.py sh.600251")
        print("例如: python chip_cost.py sh.600251 11.05")
        sys.exit(1)

    code = sys.argv[1]
    cur = float(sys.argv[2]) if len(sys.argv) > 2 else None
    result = analyze_chip_from_db(code, current_price=cur)
    if result:
        print_chip_report(result, name=code)

        # 如果有入场价，做安全检查
        if cur:
            safe, msg = chip_entry_check(result, cur * 0.98, cur * 1.02)
            print(f"  入场安全检查: {msg}")
