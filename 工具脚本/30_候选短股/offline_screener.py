#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线选股器 V6.1 - 市场模式+情绪周期+梯队定位

根据 market_mode.py 判定的市场模式(M1-M5)：
  - 自动启用/禁用/降级策略
  - 动态调整板块淘汰线
  - 动态调整S3 X2放宽阈值
V6.1新增：
  - 情绪周期仓位修正(position_modifier)
  - 板块轮动加成/惩罚
  - 梯队定位(龙头/跟风/补涨)
  - 跨策略同行业去重
"""
import sys, warnings
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_connection, init_db
from market_mode import get_mode_params, detect_market_mode
from chip_cost import analyze_chip_cost, chip_entry_check
import pandas as pd
import numpy as np

# ═══ V6: 先判定市场模式 ═══
mode_key, mode_cfg, mode_details = detect_market_mode(verbose=True)
MODE = get_mode_params()
print(f"\n  >>> offline_screener V6.1 已加载模式: {MODE['mode']} | 综合情绪: {MODE['composite_emotion']}/10")
print(f"  >>> 策略: S1={MODE['strategies']['S1']} S2={MODE['strategies']['S2']} S3={MODE['strategies']['S3']}")
print(f"  >>> 情绪周期: {MODE['cycle_phase']}(得分{MODE['cycle_score']}/12)  仓位修正: {MODE['position_modifier']}")
if MODE['cycle_warning']:
    print(f"  >>> ⚠️ {MODE['cycle_warning']}")
# 板块轮动提示
rot = MODE.get('sector_rotation', {})
if rot.get('rising'):
    print(f"  >>> 🔺 新晋强势: {', '.join(s['industry'][:8] for s in rot['rising'][:3])}")
if rot.get('falling'):
    print(f"  >>> 🔻 退潮板块: {', '.join(s['industry'][:8] for s in rot['falling'][:3])}")
print()

init_db()
conn = get_connection()

# ═══ 历史数据驱动的入场/止盈/止损计算 ═══

def _load_chip(code, price):
    """加载筹码数据，返回 chip_result 或 None"""
    try:
        chip_df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', conn, params=[code])
        for col in ['open','high','low','close','volume','amount','turn','pctChg']:
            chip_df[col] = pd.to_numeric(chip_df[col], errors='coerce')
        chip_df = chip_df.dropna(subset=['close','volume'])
        return analyze_chip_cost(chip_df, current_price=price)
    except Exception:
        return None


def _mode_tp_tiers(mode, strategy):
    """根据市场模式和策略返回止盈档位百分比 (tier1_pct, tier2_pct)"""
    if strategy == 'S3':
        # S3手册: 止盈1=+5%, 止盈2=+8%
        if mode in ('M3', 'M4'):
            return 0.05, 0.08
        return 0.05, 0.08
    # S1/S2 通用
    if mode in ('M1', 'M2'):
        return 0.03, 0.05
    elif mode == 'M3':
        return 0.03, 0.05  # +3%卖1/3, +5%卖1/3, 剩1/3移动止盈
    elif mode == 'M4':
        return 0.05, 0.08  # +5%卖1/3, +8%卖1/3
    return 0.03, 0.05


def _compute_plan(strategy, c, chip, mode):
    """
    基于历史数据计算入场/止盈/止损，chip=None 时回退到百分比。
    返回 dict: entry_lo, entry_hi, entry_basis,
               tp1, tp1_basis, tp2, tp2_basis,
               soft_stop, hard_stop, stop_basis
    """
    price = c['price']
    rz = chip['resistance_zone'] if chip else None  # (lo, hi) 上方压力区
    sz = chip['support_zone'] if chip else None       # (lo, hi) 下方支撑区

    # ═══ 1. 入场区间 ═══
    if strategy == 'S1':
        entry_lo = min(c['ma5'], c['ma10'], c['ma20'])
        entry_hi = max(c['ma5'], c['ma10'], c['ma20'])
        entry_basis = "三线粘合区间(MA5/10/20)"
    elif strategy == 'S2':
        # 大阳线收盘±2%为基础，用筹码支撑收窄下沿
        bc_close = c['bc_close']
        base_lo = bc_close * 0.98
        base_hi = bc_close * 1.02
        if sz and sz[1] >= base_lo:
            # 筹码支撑上沿在入场区内 → 用支撑上沿作为更精准的下沿
            entry_lo = max(base_lo, sz[1] * 0.99)
        else:
            entry_lo = base_lo
        entry_hi = base_hi
        entry_basis = f"大阳收盘{bc_close:.2f}±2%"
        if sz and sz[1] >= base_lo:
            entry_basis += f" 筹码支撑{sz[0]:.2f}~{sz[1]:.2f}托底"
    else:  # S3
        entry_lo = price * 0.985
        entry_hi = price * 1.015
        entry_basis = "突破日收盘±1.5%"
        # 如果有筹码支撑且紧贴现价，用它作为下沿
        if sz and sz[1] >= price * 0.97:
            entry_lo = max(entry_lo, sz[1] * 0.99)
            entry_basis += f" 筹码{sz[1]:.2f}托底"

    # ═══ 2. 止盈目标 → 优先用筹码压力区 ═══
    tp1_pct, tp2_pct = _mode_tp_tiers(mode, strategy)
    fallback_tp1 = price * (1 + tp1_pct)
    fallback_tp2 = price * (1 + tp2_pct)

    if rz:
        # 压力区下沿=第一止盈目标（到了就有抛压，先落袋）
        # 压力区上沿=第二止盈目标（突破才能到）
        tp1 = rz[0]
        tp2 = rz[1]
        # 安全检查：如果压力区离现价太近(<1.5%)或太远(>15%)，回退百分比
        dist1 = (tp1 - price) / price
        dist2 = (tp2 - price) / price
        if dist1 < 0.015:
            # 压力区就在头顶——用压力区上沿作tp1，百分比作tp2
            tp1 = rz[1]
            tp2 = fallback_tp2
            tp1_basis = f"压力区上沿{rz[1]:.2f}"
            tp2_basis = f"百分比+{tp2_pct:.0%}"
        elif dist1 > 0.15:
            # 压力区太远，回退百分比
            tp1 = fallback_tp1
            tp2 = fallback_tp2
            tp1_basis = f"+{tp1_pct:.0%}(压力区{rz[0]:.2f}过远)"
            tp2_basis = f"+{tp2_pct:.0%}"
        else:
            tp1_basis = f"筹码压力区下沿{rz[0]:.2f}"
            tp2_basis = f"筹码压力区上沿{rz[1]:.2f}"
    else:
        tp1 = fallback_tp1
        tp2 = fallback_tp2
        tp1_basis = f"+{tp1_pct:.0%}"
        tp2_basis = f"+{tp2_pct:.0%}"

    # ═══ 3. 止损 → 优先用历史支撑 ═══
    soft_stop = price * 0.985  # 通用软止损: 收盘-1.5%

    if strategy == 'S2':
        # S2硬止损=大阳线开盘价（逻辑失效线）
        hard_stop = c['bc_open']
        stop_basis = f"大阳线开盘{c['bc_open']:.2f}(跌破=阳线作废)"
        # 如果筹码支撑比大阳开盘更高，取更保守的
        if sz and sz[0] > hard_stop:
            hard_stop = sz[0]
            stop_basis = f"筹码支撑{sz[0]:.2f}(高于大阳开盘{c['bc_open']:.2f})"
    elif strategy == 'S3':
        # S3硬止损: 手册=-3%, 但如果有筹码支撑且更近则用支撑
        pct_stop = price * 0.97
        if sz and sz[0] > pct_stop and sz[0] < price:
            hard_stop = sz[0]
            stop_basis = f"筹码支撑下沿{sz[0]:.2f}"
        else:
            hard_stop = pct_stop
            stop_basis = f"盘中-3%"
    else:  # S1
        # S1: ①筹码支撑 ②MA60×0.98 取较近的
        ma60_stop = c['ma60'] * 0.98
        if sz and sz[0] < price:
            hard_stop = max(sz[0], ma60_stop)
            if sz[0] >= ma60_stop:
                stop_basis = f"筹码支撑{sz[0]:.2f}"
            else:
                stop_basis = f"MA60×0.98={ma60_stop:.2f}(高于筹码支撑{sz[0]:.2f})"
        else:
            hard_stop = ma60_stop
            stop_basis = f"MA60×0.98"

    # 确保止盈>入场，止损<入场
    tp1 = max(tp1, entry_hi * 1.005)
    tp2 = max(tp2, tp1 * 1.005)

    return {
        'entry_lo': entry_lo, 'entry_hi': entry_hi, 'entry_basis': entry_basis,
        'tp1': tp1, 'tp1_basis': tp1_basis, 'tp2': tp2, 'tp2_basis': tp2_basis,
        'soft_stop': soft_stop, 'hard_stop': hard_stop, 'stop_basis': stop_basis,
    }


def _print_plan(plan, mode, strategy):
    """打印操作计划"""
    p = plan
    # 止盈批次说明
    if mode in ('M1', 'M2'):
        tp_note = "卖1/2 → 全清"
    elif mode == 'M3':
        tp_note = "卖1/3 → 卖1/3 → 剩1/3移动止盈(高点回落2%)"
    elif mode == 'M4':
        tp_note = "卖1/3 → 卖1/3 → 剩1/3移动止盈(高点回落3%)"
    else:
        tp_note = "卖1/2 → 全清"

    print(f"  ── 操作计划 ({mode}止盈) ──")
    print(f"  入场区间: {p['entry_lo']:.2f} ~ {p['entry_hi']:.2f}  ← {p['entry_basis']}")
    print(f"  止盈1: {p['tp1']:.2f}  ← {p['tp1_basis']}  │ {tp_note}")
    print(f"  止盈2: {p['tp2']:.2f}  ← {p['tp2_basis']}")
    print(f"  软止损(收盘-1.5%): {p['soft_stop']:.2f}  硬止损: {p['hard_stop']:.2f}  ← {p['stop_basis']}")
    # 盈亏比
    mid_entry = (p['entry_lo'] + p['entry_hi']) / 2
    reward = p['tp1'] - mid_entry
    risk = mid_entry - p['hard_stop']
    if risk > 0:
        rr = reward / risk
        rr_tag = "✅" if rr >= 2 else "⚠️" if rr >= 1.5 else "❌"
        print(f"  盈亏比: {rr:.1f}:1 {rr_tag}  (止盈1距离{reward:.2f} / 硬止损距离{risk:.2f})")


def _print_chip(chip):
    """打印筹码分析摘要"""
    if not chip:
        return
    print(f"  ── 筹码分析 ──")
    print(f"  成本重心: {chip['cost_center']:.2f}  获利盘: {chip['profit_ratio']:.0%}  套牢盘: {chip['trapped_ratio']:.0%}  集中度: {chip['chip_concentration']:.1%}")
    if chip['resistance_zone']:
        rz = chip['resistance_zone']
        print(f"  上方压力: {rz[0]:.2f}~{rz[1]:.2f}")
    if chip['support_zone']:
        sz = chip['support_zone']
        print(f"  下方支撑: {sz[0]:.2f}~{sz[1]:.2f}")
    print(f"  💡 {chip['advice']}")


codes = [r[0] for r in conn.execute('SELECT DISTINCT code FROM kline_daily').fetchall()]

# === 加载行业映射和板块数据 ===
industry_map = {}
sector_momentum = {}  # industry → 5日动量
try:
    ind_df = pd.read_sql('SELECT code, industry FROM stock_industry', conn)
    industry_map = dict(zip(ind_df['code'], ind_df['industry']))
    # 计算最新日板块5日动量排名
    sec_df = pd.read_sql('SELECT * FROM sector_daily ORDER BY industry, date', conn)
    for col in ['avg_pct']:
        sec_df[col] = pd.to_numeric(sec_df[col], errors='coerce')
    for ind, grp in sec_df.groupby('industry'):
        grp = grp.sort_values('date').reset_index(drop=True)
        if len(grp) >= 5:
            m5 = float(np.mean(grp['avg_pct'].values[-5:]))
            sector_momentum[ind] = m5
    # 计算排名百分位
    if sector_momentum:
        sorted_secs = sorted(sector_momentum.items(), key=lambda x: x[1])
        n_sec = len(sorted_secs)
        sector_rank = {}
        for i, (ind, _) in enumerate(sorted_secs):
            sector_rank[ind] = i / max(n_sec - 1, 1)
        print(f"  板块数据: {len(industry_map)} 只股票映射, {len(sector_momentum)} 个板块有动量数据")
        # 显示前5/后5板块
        print(f"  强势板块TOP5: {', '.join(ind for ind, _ in sorted_secs[-5:])}")
        print(f"  弱势板块TOP5: {', '.join(ind for ind, _ in sorted_secs[:5])}")
    else:
        sector_rank = {}
except Exception as e:
    print(f"  板块数据加载失败: {e}")
    sector_rank = {}

# === V6.1: 梯队定位 - 计算每只股票在板块内的5日涨幅排名 ===
stock_tier = {}   # code → {'rank_pct': 0~1, 'tier': '龙头'/'跟风'/'补涨', 'sector_chg5': float}
try:
    # 批量取所有股票最近6天收盘价
    latest_date = conn.execute("SELECT MAX(date) FROM kline_daily WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'").fetchone()[0]
    tier_sql = """
        SELECT k.code, k.date, k.close, si.industry
        FROM kline_daily k
        JOIN stock_industry si ON k.code = si.code
        WHERE k.date >= (SELECT DISTINCT date FROM kline_daily WHERE code='sh.000001' ORDER BY date DESC LIMIT 1 OFFSET 5)
        AND k.code NOT LIKE 'sh.000%' AND k.code NOT LIKE 'sz.399%'
        ORDER BY k.code, k.date
    """
    tier_df = pd.read_sql(tier_sql, conn)
    tier_df['close'] = pd.to_numeric(tier_df['close'], errors='coerce')
    # 计算每只个股的5日涨幅
    stock_chg5 = {}
    for code_g, grp in tier_df.groupby('code'):
        grp = grp.sort_values('date')
        if len(grp) >= 2:
            stock_chg5[code_g] = (grp['close'].iloc[-1] / grp['close'].iloc[0] - 1) * 100
    # 在板块内排名
    from collections import defaultdict
    sector_stocks = defaultdict(list)
    for code_g, chg in stock_chg5.items():
        ind = industry_map.get(code_g, '')
        if ind:
            sector_stocks[ind].append((code_g, chg))
    for ind, stocks in sector_stocks.items():
        stocks_sorted = sorted(stocks, key=lambda x: x[1], reverse=True)
        n_s = len(stocks_sorted)
        for rank_i, (code_g, chg) in enumerate(stocks_sorted):
            rank_pct = rank_i / max(n_s - 1, 1)  # 0=最强，1=最弱
            if rank_pct <= 0.15:
                tier = '龙头'
            elif rank_pct <= 0.50:
                tier = '跟风'
            else:
                tier = '补涨'
            stock_tier[code_g] = {'rank_pct': rank_pct, 'tier': tier, 'sector_chg5': chg}
    print(f"  梯队定位: {len(stock_tier)} 只股票已计算板块内排名")
except Exception as e:
    print(f"  梯队定位计算失败: {e}")

# === V6.1: 板块轮动加分表 ===
rotation_rising = set()
rotation_falling = set()
rot = MODE.get('sector_rotation', {})
for s in rot.get('rising', []):
    rotation_rising.add(s.get('industry', ''))
for s in rot.get('falling', []):
    rotation_falling.add(s.get('industry', ''))

# === 大盘环境 ===
print("=" * 80)
print("  大盘环境速览 (缓存数据)")
print("=" * 80)
for idx_code, idx_name in [('sh.000001','上证指数'),('sz.399001','深证成指'),('sz.399006','创业板指')]:
    df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', conn, params=[idx_code])
    if df.empty:
        print(f"  {idx_name}: 无缓存数据")
        continue
    for c in ['open','high','low','close','volume','pctChg']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    cls = df['close'].values
    pcts = df['pctChg'].values
    last_date = df['date'].iloc[-1]
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    ma60 = np.mean(cls[-60:]) if len(cls) >= 60 else np.mean(cls)
    pct5 = (cls[-1] - cls[-5]) / cls[-5] * 100 if len(cls) >= 5 else 0
    pct1 = pcts[-1]
    bullish = sum([ma5 > ma10, ma10 > ma20, ma20 > ma60, cls[-1] > ma60, cls[-1] > ma20])
    trend = "上升" if bullish >= 4 else "震荡" if bullish >= 2 else "下降"
    vol520 = np.mean(df['volume'].values[-5:]) / np.mean(df['volume'].values[-20:])
    emotion = 5
    if bullish >= 4: emotion += 2
    elif bullish <= 1: emotion -= 2
    if vol520 > 1.2: emotion += 1
    elif vol520 < 0.7: emotion -= 1
    if pct5 > 3: emotion += 1
    elif pct5 < -3: emotion -= 1
    emotion = max(0, min(10, emotion))
    bars = "█" * emotion + "░" * (10 - emotion)
    print(f"  {idx_name}: {cls[-1]:.2f}  今日{pct1:+.2f}%  近5日{pct5:+.2f}%  趋势:{trend}  情绪:[{bars}] {emotion}/10")

# ═══ V6: S1策略状态检查 ═══
s1_role = MODE['strategies'].get('S1', 'disabled')
s1_cutoff = MODE['sector_cutoff'].get('S1', 0.30) or 0.30

print()
print("=" * 80)
if s1_role == 'disabled':
    print(f"  S1 蓄力候选 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
elif s1_role == 'watchonly':
    print(f"  S1 蓄力候选 👀 当前{MODE['mode']}模式降为观察池 ({len(codes)}只缓存股票)")
    print(f"  板块淘汰线: 后{int(s1_cutoff*100)}%")
    print("=" * 80)
else:
    print(f"  蓄力候选扫描 ({len(codes)}只缓存股票)")
    print(f"  板块淘汰线: 后{int(s1_cutoff*100)}%")
    print("=" * 80)
print()

results = []
for code in (codes if s1_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
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
    if avg_amt_20 < 1000: continue

    # F1: 流通市值过滤 30~300亿
    t_last_s1 = turns[-1] if len(turns) > 0 else 0
    if t_last_s1 > 0:
        float_mcap_s1 = (amts[-1] / (last * 100)) / (t_last_s1 / 100) * 100 * last / 1e8
        if float_mcap_s1 < 30 or float_mcap_s1 > 300: continue
    else:
        continue

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
    if last <= ma60: continue

    c60 = cls[-60:]; p60 = pcts[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): continue
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): continue
    limit_ups = np.sum(p60 >= 9.5)
    if limit_ups < 1: continue

    # X1: 近5日无单日跌>5%
    if np.any(pcts[-5:] < -5): continue
    # X2: 近20日未连续下跌超10天
    if n >= 20:
        consec_down = 0
        max_consec_down = 0
        for _p in pcts[-20:]:
            if _p < 0:
                consec_down += 1
                max_consec_down = max(max_consec_down, consec_down)
            else:
                consec_down = 0
        if max_consec_down > 10: continue
    # X3: 无异常高换手>8%
    if np.any(turns[-5:] > 8): continue

    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    vol_min60 = np.min(vols[-60:])
    floor_vol = vols[-1] <= vol_min60 * 1.2

    score = 0; details = []
    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 4: score += 3; details.append("①缩量3")
    elif sc1 >= 3: score += 2; details.append("①缩量2")
    elif sc1 >= 1: score += 1; details.append("①缩量1")
    else: details.append("①缩量0")

    c5 = cls[-5:]; rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    # 横盘天数：连续收盘价在±2.5%区间内的天数
    hp_days = 0
    if n >= 15:
        mid = np.mean(cls[-5:])
        for di in range(min(20, n)):
            if abs(cls[-(di+1)] - mid) / mid * 100 <= 2.5:
                hp_days += 1
            else:
                break
    sc2 = sum([rng5 <= 5, abs(cs) <= 1, last > ma60, hp_days >= 5])
    if sc2 >= 4: score += 4; details.append(f"②横盘4({hp_days}d)")
    elif sc2 >= 3: score += 3; details.append(f"②横盘3({hp_days}d)")
    elif sc2 >= 2: score += 2; details.append(f"②横盘2({hp_days}d)")
    elif sc2 >= 1: score += 1; details.append(f"②横盘1({hp_days}d)")
    else: details.append("②横盘0")

    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, last > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3: score += 4; details.append("③均线4")
    elif sc3 >= 2: score += 3; details.append("③均线3")
    elif sc3 >= 1: score += 2; details.append("③均线2")
    else: details.append("③均线0")

    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 3: score += 3; details.append("④实体3")
    elif sc4 >= 2: score += 2; details.append("④实体2")
    elif sc4 >= 1: score += 1; details.append("④实体1")
    else: details.append("④实体0")

    lsb = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); ls_len = min(o, c) - l
        if c > o and body > 0 and ls_len >= 2 * body and pcts[i] <= 2:
            lsb += 1
    if lsb >= 2: score += 2; details.append("⑤下影2")
    elif lsb >= 1: score += 1; details.append("⑤下影1")
    else: details.append("⑤下影0")

    doji = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); bp = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if bp <= 0.5 and body > 0 and shadow >= 2 * body:
            doji += 1
    if doji >= 2: score += 2; details.append("⑥十字2")
    elif doji >= 1: score += 1; details.append("⑥十字1")
    else: details.append("⑥十字0")

    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    sc7 = sum([no3, pct5r, -2 <= pct5s <= 3])
    if sc7 >= 3: score += 2; details.append("⑦交替2")
    elif sc7 >= 2: score += 1; details.append("⑦交替1")
    else: details.append("⑦交替0")

    signals = []
    if n >= 12:
        for i in [-1, -2, -3]:
            pm5 = np.mean(cls[i-5:i]); pm10 = np.mean(cls[i-10:i])
            cm5 = np.mean(cls[i-4:i+1]); cm10 = np.mean(cls[i-9:i+1])
            if pm5 <= pm10 and cm5 > cm10:
                signals.append("MA5金叉MA10"); break
    if vols[-1] >= vol5 * 2 and pcts[-1] > 3:
        signals.append("放量突破")
    if n >= 15 and cls[-1] > np.max(cls[-15:-1]) and pcts[-1] > 0:
        signals.append("突破横盘上沿")

    grade = 'A' if score >= 16 else 'B' if score >= 10 else 'C'

    # V3: B级只保留15分，低分B淘汰
    if grade == 'A' or (grade == 'B' and score >= 15):
        # V6: 按模式调整板块淘汰线
        ind = industry_map.get(code, '')
        sec_pct = sector_rank.get(ind, 0.5)
        if sec_pct < s1_cutoff and ind:
            continue  # 板块弱于该模式淘汰线，跳过
        sec_bonus = 1 if sec_pct >= 0.7 else 0
        recent_low5 = float(np.min(los[-5:]))
        # V6.1: 梯队定位 + 轮动加成
        t_info = stock_tier.get(code, {})
        tier_label = t_info.get('tier', '—')
        tier_rank = t_info.get('rank_pct', 0.5)
        rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
        results.append({
            'code': code, 'price': last, 'score': score + rot_bonus, 'grade': grade,
            'details': ' '.join(details),
            'signals': '|'.join(signals) if signals else '',
            'vr520': vr520, 'turn5': turn5, 'ma_sp': ma_sp, 'rng5': rng5,
            'pct60': pct60, 'dd60': dd60, 'cs': cs, 'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'industry': ind, 'sector_bonus': sec_bonus,
            'recent_low5': recent_low5,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            'strategy': 'S1',
        })

# V4: 有启动信号优先，强势板块优先，同级按分数降序
results.sort(key=lambda x: (0 if x['signals'] else 1, 0 if x['grade']=='A' else 1, -x.get('sector_bonus',0), 0 if x.get('tier')=='龙头' else 1, -x['score']))

# V6: S1禁用或观察池模式标注
if s1_role == 'disabled':
    results = []  # 清空S1结果
    print(f"  S1 在{MODE['mode']}模式下禁用，跳过")
elif s1_role == 'watchonly':
    print(f"  通过筛选: {len(results)} 只 (👀观察池，不下单)")
    for r in results:
        r['watchonly'] = True
else:
    print(f"  通过筛选: {len(results)} 只")
print()
if results:
    header = f"{'排名':>4s} {'代码':<12s} {'价格':>7s} {'今涨':>7s} {'评分':>4s} {'级':>2s} {'梯队':>4s} {'板块':>10s} {'量比5/20':>8s} {'5日换手':>7s} {'均线距':>6s} {'5日幅':>6s} {'60日涨':>7s} {'回撤':>7s}  指标明细"
    print(header)
    print("-" * 155)
    for rank, c in enumerate(results[:20], 1):
        sig = f" 💥{c['signals']}" if c['signals'] else ""
        sec_tag = f"🔥{c['industry'][:6]}" if c.get('sector_bonus') else c.get('industry','')[:8]
        tier_tag = c.get('tier', '—')
        rot_tag = '🔺' if c.get('rot_bonus', 0) > 0 else ('🔻' if c.get('rot_bonus', 0) < 0 else '')
        print(f"{rank:>4d} {c['code']:<12s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['score']:>4d} {c['grade']:>2s} {tier_tag:>4s} {sec_tag:>10s}{rot_tag} {c['vr520']:>8.2f} {c['turn5']:>6.2f}% {c['ma_sp']:>5.2f}% {c['rng5']:>5.2f}% {c['pct60']:>+6.1f}% {c['dd60']:>+6.1f}%  {c['details']}{sig}")

    print()
    print("=" * 80)
    print("  TOP 3 蓄力即将突破 详细分析")
    print("=" * 80)
    for rank, c in enumerate(results[:3], 1):
        ind_info = f" [{c.get('industry','')}]" if c.get('industry') else ""
        tier_info = f" 梯队:{c.get('tier','—')}" if c.get('tier') else ""
        print(f"\n  ── #{rank} {c['code']}{ind_info}{tier_info} ──")
        print(f"  价格: {c['price']:.2f}  今日涨跌: {c['pct_today']:+.2f}%")
        print(f"  MA5={c['ma5']:.2f}  MA10={c['ma10']:.2f}  MA20={c['ma20']:.2f}  MA60={c['ma60']:.2f}")
        print(f"  量比(5/20): {c['vr520']:.2f}  5日换手: {c['turn5']:.2f}%  均线间距: {c['ma_sp']:.2f}%")
        print(f"  5日波幅: {c['rng5']:.2f}%  重心偏移: {c['cs']:+.2f}%")
        print(f"  60日涨幅: {c['pct60']:+.1f}%  高点回撤: {c['dd60']:+.1f}%")
        print(f"  评分: {c['score']}/20 ({c['grade']}级)")
        print(f"  指标: {c['details']}")
        if c['signals']:
            print(f"  信号: {c['signals']}")

        # ═══ V7: 基于历史数据的操作计划 ═══
        chip = _load_chip(c['code'], c['price'])
        plan = _compute_plan('S1', c, chip, MODE['mode'])
        sig_tag = " 💥有启动信号" if c['signals'] else " ⏳等待信号"
        print(f"  {sig_tag}")
        _print_plan(plan, MODE['mode'], 'S1')
        _print_chip(chip)
        if chip:
            safe, msg = chip_entry_check(chip, plan['entry_lo'], plan['entry_hi'])
            print(f"  {msg}")
else:
    print("  （无符合条件的蓄力候选）")

# ═══════════════════════════════════════════════════════════════
# S2：大阳后缩量横盘扫描
# ═══════════════════════════════════════════════════════════════

# V6: S2策略状态检查
s2_role = MODE['strategies'].get('S2', 'disabled')
s2_cutoff = MODE['sector_cutoff'].get('S2', 0.30) or 0.30

print()
print("=" * 80)
if s2_role == 'disabled':
    print(f"  S2 大阳后缩量横盘 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
else:
    role_tag = "🟢主力" if s2_role == 'primary' else "🔵辅助"
    print(f"  S2 大阳后缩量横盘扫描 {role_tag} ({len(codes)}只缓存股票)")
    print(f"  板块淘汰线: 后{int(s2_cutoff*100)}%")
    print("=" * 80)
    print()

s2_results = []
for code in (codes if s2_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
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
    if avg_amt_20 < 1000: continue

    # F1: 市值 30-300亿 (estimate)
    t_last = turns[-1] if len(turns) > 0 else 0
    if t_last <= 0: continue
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: continue

    # F5: 现价 > MA60
    ma60 = np.mean(cls[-60:])
    if last <= ma60: continue

    # F6: 板块不弱 (V6: 按模式调整淘汰线)
    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5)
    if sec_pct < s2_cutoff and ind: continue

    # F2: 近5个交易日内有大阳线
    vol20 = np.mean(vols[-20:])
    big_candle_idx = None
    for di in range(1, min(6, n)):
        idx = n - di
        if idx < 1: break
        day_pct = (cls[idx] / cls[idx-1] - 1) * 100
        day_vol_ratio = vols[idx] / vol20 if vol20 > 0 else 0
        is_yang = cls[idx] > ops[idx]
        if day_pct >= 4 and is_yang and day_vol_ratio >= 1.5:
            big_candle_idx = idx
            break  # take most recent

    if big_candle_idx is None: continue

    bc_close = cls[big_candle_idx]
    bc_open = ops[big_candle_idx]
    bc_vol = vols[big_candle_idx]
    bc_pct = (cls[big_candle_idx] / cls[big_candle_idx-1] - 1) * 100
    bc_date = df['date'].iloc[big_candle_idx]
    days_after = n - 1 - big_candle_idx  # how many days after big candle

    if days_after < 1: continue  # need at least 1 day after

    # F3: 大阳线后缩量
    post_vols = vols[big_candle_idx+1:]
    avg_post_vol = np.mean(post_vols)
    vol_shrink = avg_post_vol / bc_vol if bc_vol > 0 else 999
    if vol_shrink >= 0.7: continue

    # F4: 价格不跌回 (现价 >= 大阳线开盘价)
    if last < bc_open: continue

    # X1: 大阳线后无单日跌>3%
    post_pcts = pcts[big_candle_idx+1:]
    if np.any(post_pcts < -3): continue

    # X2: 大阳线后无放量砸盘
    post_vols_arr = vols[big_candle_idx+1:]
    post_pcts_arr = pcts[big_candle_idx+1:]
    vol_dump = False
    for pi in range(len(post_vols_arr)):
        if post_vols_arr[pi] > bc_vol * 0.8 and post_pcts_arr[pi] < 0:
            vol_dump = True; break
    if vol_dump: continue

    # X3: 换手率
    if np.any(turns[-5:] > 8): continue

    # Scoring (8 points)
    score = 0; details = []

    # ① 缩量程度 (2)
    if vol_shrink <= 0.5: score += 2; details.append("①缩量2")
    elif vol_shrink <= 0.7: score += 1; details.append("①缩量1")
    else: details.append("①缩量0")

    # ② 价格守住 (2)
    price_hold = last / bc_close if bc_close > 0 else 0
    if price_hold >= 0.99: score += 2; details.append("②守住2")
    elif price_hold >= 0.97: score += 1; details.append("②守住1")
    else: details.append("②守住0")

    # ③ 板块强度 (2)
    if sec_pct >= 0.7: score += 2; details.append("③板块2")
    elif sec_pct >= 0.5: score += 1; details.append("③板块1")
    else: details.append("③板块0")

    # ④ 均线配合 (2)
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    if ma5 > ma10 > ma20: score += 2; details.append("④均线2")
    elif ma5 > ma10: score += 1; details.append("④均线1")
    else: details.append("④均线0")

    grade = 'A' if score >= 7 else ('B' if score >= 6 else 'C')
    if grade in ('A', 'B'):
        # V6.1: 梯队定位 + 轮动加成
        t_info = stock_tier.get(code, {})
        tier_label = t_info.get('tier', '—')
        tier_rank = t_info.get('rank_pct', 0.5)
        rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
        s2_results.append({
            'code': code, 'price': last, 'score': score + rot_bonus, 'grade': grade,
            'details': ' '.join(details),
            'bc_date': bc_date, 'bc_pct': bc_pct, 'bc_close': bc_close,
            'bc_open': bc_open, 'vol_shrink': vol_shrink,
            'price_hold': price_hold, 'days_after': days_after,
            'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'industry': ind, 'sector_bonus': 1 if sec_pct >= 0.7 else 0,
            'float_mcap': float_mcap,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            'strategy': 'S2',
        })

s2_results.sort(key=lambda x: (0 if x['grade']=='A' else 1, -x.get('sector_bonus',0), 0 if x.get('tier')=='龙头' else 1, -x['score']))

if s2_role != 'disabled':
    print(f"  通过筛选: {len(s2_results)} 只")
print()
if s2_results:
    print(f"  {'排名':>4s} {'代码':<12s} {'价格':>7s} {'今涨':>7s} {'评分':>4s} {'级':>2s} {'梯队':>4s} {'板块':>10s} {'大阳日':>10s} {'阳线涨':>7s} {'量缩':>6s} {'价守':>6s} {'天后':>4s} 明细")
    print("-" * 140)
    for rank, c in enumerate(s2_results[:20], 1):
        sec_tag = f"🔥{c['industry'][:6]}" if c.get('sector_bonus') else c.get('industry','')[:8]
        tier_tag = c.get('tier', '—')
        rot_tag = '🔺' if c.get('rot_bonus', 0) > 0 else ('🔻' if c.get('rot_bonus', 0) < 0 else '')
        print(f"  {rank:>3d} {c['code']:<12s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['score']:>4d} {c['grade']:>2s} {tier_tag:>4s} {sec_tag:>10s}{rot_tag} {c['bc_date']:>10s} {c['bc_pct']:>+6.1f}% {c['vol_shrink']:>5.2f} {c['price_hold']:>5.2f} {c['days_after']:>3d}d  {c['details']}")

    # Top 3 detailed
    print()
    print("=" * 80)
    print("  S2 TOP 3 详细分析")
    print("=" * 80)
    for rank, c in enumerate(s2_results[:3], 1):
        ind_info = f" [{c.get('industry','')}]" if c.get('industry') else ""
        tier_info = f" 梯队:{c.get('tier','—')}" if c.get('tier') else ""
        print(f"\n  ── S2 #{rank} {c['code']}{ind_info}{tier_info} ──")
        print(f"  价格: {c['price']:.2f}  今日涨跌: {c['pct_today']:+.2f}%  市值: ~{c['float_mcap']:.0f}亿")
        print(f"  大阳线: {c['bc_date']} 涨{c['bc_pct']:+.1f}% (收{c['bc_close']:.2f} 开{c['bc_open']:.2f})")
        print(f"  缩量比: {c['vol_shrink']:.2f}  价格守住: {c['price_hold']:.2%}  已过{c['days_after']}天")
        print(f"  MA5={c['ma5']:.2f}  MA10={c['ma10']:.2f}  MA20={c['ma20']:.2f}  MA60={c['ma60']:.2f}")
        print(f"  评分: {c['score']}/8 ({c['grade']}级)")
        print(f"  明细: {c['details']}")

        # ═══ V7: 基于历史数据的操作计划 ═══
        chip = _load_chip(c['code'], c['price'])
        plan = _compute_plan('S2', c, chip, MODE['mode'])
        _print_plan(plan, MODE['mode'], 'S2')
        _print_chip(chip)
        if chip:
            safe, msg = chip_entry_check(chip, plan['entry_lo'], plan['entry_hi'])
            print(f"  {msg}")
else:
    print("  （无符合条件的S2候选）")


# ═══════════════════════════════════════════════════════════════
# S3：放量突破新高扫描
# ═══════════════════════════════════════════════════════════════

# V6: S3策略状态检查
s3_role = MODE['strategies'].get('S3', 'disabled')
s3_cutoff = MODE['sector_cutoff'].get('S3', 0.50) or 0.50
s3_x2_limit = MODE['s3_x2_limit']
s3_x2_relax = MODE['s3_x2_relax']

print()
print("=" * 80)
if s3_role == 'disabled':
    print(f"  S3 放量突破新高 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
else:
    role_tag = "🟢主力" if s3_role == 'primary' else "🔵辅助"
    print(f"  S3 放量突破新高扫描 {role_tag} ({len(codes)}只缓存股票)")
    print(f"  板块淘汰线: 后{int(s3_cutoff*100)}%  X2限制: 5日涨>{s3_x2_limit}%排除")
    if s3_x2_relax:
        for cond, val in s3_x2_relax.items():
            print(f"  X2放宽: {cond} → 允许到{val}%")
    print("=" * 80)
    print()

s3_results = []
for code in (codes if s3_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
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
    if avg_amt_20 < 1000: continue

    # F1: 市值
    t_last = turns[-1] if len(turns) > 0 else 0
    if t_last <= 0: continue
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: continue

    # F2: 收盘价 > 近20日最高价 (excluding today)
    if n < 21: continue
    high20 = np.max(his[-21:-1])  # previous 20 days high
    if last <= high20: continue
    brk_pct = (last / high20 - 1) * 100

    # F3: 放量
    vol20 = np.mean(vols[-20:])
    vol_ratio = vols[-1] / vol20 if vol20 > 0 else 0
    if vol_ratio < 1.5: continue

    # F4: 收阳
    if last <= ops[-1]: continue

    # F5: 均线多头
    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
    if not (last > ma20 > ma60): continue

    # F6: 板块过滤 (V6: 按模式调整淘汰线)
    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5)
    if sec_pct < s3_cutoff and ind: continue

    # X1: 今日涨幅 <= 7%
    if pcts[-1] > 7: continue

    # X2: 近5日涨幅限制 (V6: 按模式动态调整)
    chg5 = (cls[-1] / cls[-6] - 1) * 100 if n >= 6 else 0
    x2_limit = s3_x2_limit
    # V6: 强势板块放宽
    if s3_x2_relax:
        if 'top30_sector' in s3_x2_relax and sec_pct >= 0.7:
            x2_limit = s3_x2_relax['top30_sector']
        elif 'top20_sector' in s3_x2_relax and sec_pct >= 0.8:
            x2_limit = s3_x2_relax['top20_sector']
    if chg5 > x2_limit: continue

    # X3: 换手率 <= 10%
    if turns[-1] > 10: continue

    # Scoring (6 points)
    score = 0; details = []

    # ① 突破幅度 (2)
    if brk_pct > 3: score += 2; details.append(f"①突破2({brk_pct:+.1f}%)")
    elif brk_pct > 1: score += 1; details.append(f"①突破1({brk_pct:+.1f}%)")
    else: details.append(f"①突破0({brk_pct:+.1f}%)")

    # ② 放量程度 (2)
    if vol_ratio > 2.5: score += 2; details.append(f"②放量2({vol_ratio:.1f}x)")
    elif vol_ratio > 1.5: score += 1; details.append(f"②放量1({vol_ratio:.1f}x)")
    else: details.append(f"②放量0({vol_ratio:.1f}x)")

    # ③ 板块力度 (2)
    if sec_pct >= 0.7: score += 2; details.append("③板块2")
    elif sec_pct >= 0.5: score += 1; details.append("③板块1")
    else: details.append("③板块0")

    grade = 'A' if score >= 5 else ('B' if score >= 4 else 'C')
    if grade in ('A', 'B'):
        ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
        # V6.1: 梯队定位 + 轮动加成
        t_info = stock_tier.get(code, {})
        tier_label = t_info.get('tier', '—')
        tier_rank = t_info.get('rank_pct', 0.5)
        rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
        s3_results.append({
            'code': code, 'price': last, 'score': score + rot_bonus, 'grade': grade,
            'details': ' '.join(details),
            'brk_pct': brk_pct, 'vol_ratio': vol_ratio,
            'chg5': chg5, 'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'industry': ind, 'sector_bonus': 1 if sec_pct >= 0.7 else 0,
            'float_mcap': float_mcap,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            'strategy': 'S3',
        })

s3_results.sort(key=lambda x: (0 if x['grade']=='A' else 1, -x.get('sector_bonus',0), 0 if x.get('tier')=='龙头' else 1, -x['score']))

if s3_role != 'disabled':
    print(f"  通过筛选: {len(s3_results)} 只")

print()
if s3_results:
    print(f"  {'排名':>4s} {'代码':<12s} {'价格':>7s} {'今涨':>7s} {'评分':>4s} {'级':>2s} {'梯队':>4s} {'板块':>10s} {'突破':>7s} {'量比':>6s} {'5日涨':>7s} 明细")
    print("-" * 120)
    for rank, c in enumerate(s3_results[:20], 1):
        sec_tag = f"🔥{c['industry'][:6]}" if c.get('sector_bonus') else c.get('industry','')[:8]
        tier_tag = c.get('tier', '—')
        rot_tag = '🔺' if c.get('rot_bonus', 0) > 0 else ('🔻' if c.get('rot_bonus', 0) < 0 else '')
        print(f"  {rank:>3d} {c['code']:<12s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['score']:>4d} {c['grade']:>2s} {tier_tag:>4s} {sec_tag:>10s}{rot_tag} {c['brk_pct']:>+6.1f}% {c['vol_ratio']:>5.1f}x {c['chg5']:>+6.1f}%  {c['details']}")

    # Top 3 detailed
    print()
    print("=" * 80)
    print("  S3 TOP 3 详细分析")
    print("=" * 80)
    for rank, c in enumerate(s3_results[:3], 1):
        ind_info = f" [{c.get('industry','')}]" if c.get('industry') else ""
        tier_info = f" 梯队:{c.get('tier','—')}" if c.get('tier') else ""
        print(f"\n  ── S3 #{rank} {c['code']}{ind_info}{tier_info} ──")
        print(f"  价格: {c['price']:.2f}  今日涨跌: {c['pct_today']:+.2f}%  市值: ~{c['float_mcap']:.0f}亿")
        print(f"  突破20日高: {c['brk_pct']:+.1f}%  放量: {c['vol_ratio']:.1f}x  近5日涨: {c['chg5']:+.1f}%")
        print(f"  MA5={c['ma5']:.2f}  MA10={c['ma10']:.2f}  MA20={c['ma20']:.2f}  MA60={c['ma60']:.2f}")
        print(f"  评分: {c['score']}/6 ({c['grade']}级)")
        print(f"  明细: {c['details']}")

        # ═══ V7: 基于历史数据的操作计划 ═══
        chip = _load_chip(c['code'], c['price'])
        plan = _compute_plan('S3', c, chip, MODE['mode'])
        _print_plan(plan, MODE['mode'], 'S3')
        _print_chip(chip)
        if chip:
            safe, msg = chip_entry_check(chip, plan['entry_lo'], plan['entry_hi'])
            print(f"  {msg}")
else:
    print("  （无符合条件的S3候选）")

# ═══════════════════════════════════════════════════════════════
# V6.1: 跨策略同行业去重 + 仓位修正 + 最终推荐
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("  V6.1 最终推荐（同行业去重 + 仓位修正）")
print("=" * 80)

# 合并所有候选
all_candidates = []
for r in results:
    if r.get('watchonly'):
        continue  # 观察池不参与最终推荐
    all_candidates.append(r)
for r in s2_results:
    all_candidates.append(r)
for r in s3_results:
    all_candidates.append(r)

# 同行业只保留最高分
seen_industry = {}
dedup_results = []
for c in sorted(all_candidates, key=lambda x: -x['score']):
    ind = c.get('industry', '')
    if ind and ind in seen_industry:
        continue  # 同行业已有更高分的
    if ind:
        seen_industry[ind] = c['code']
    dedup_results.append(c)

# 按策略优先级排序：主力策略 > 辅助策略
strategy_priority = {'primary': 0, 'secondary': 1, 'watchonly': 2}
def get_strategy_priority(c):
    s = c.get('strategy', 'S1')
    role = MODE['strategies'].get(s, 'disabled')
    return strategy_priority.get(role, 9)

dedup_results.sort(key=lambda x: (get_strategy_priority(x), 0 if x.get('tier')=='龙头' else 1, -x['score']))

# 仓位修正提示
pos_mod = MODE.get('position_modifier', 1.0)
if pos_mod < 1.0:
    print(f"\n  ⚠️ 情绪周期警告: {MODE['cycle_phase']}(得分{MODE['cycle_score']}/12)")
    print(f"  ⚠️ 仓位修正器: {pos_mod} {'→ 仓位减半！' if pos_mod == 0.5 else '→ 禁止开仓！' if pos_mod == 0 else ''}")
    if MODE.get('cycle_warning'):
        print(f"  ⚠️ {MODE['cycle_warning']}")
    print()

if dedup_results:
    print(f"\n  去重后推荐: {len(dedup_results)} 只 (同行业仅保留最高分)")
    print(f"  {'排名':>4s} {'策略':>4s} {'代码':<12s} {'价格':>7s} {'评分':>4s} {'梯队':>4s} {'板块':>12s} {'操作建议'}")
    print("-" * 90)
    for rank, c in enumerate(dedup_results[:10], 1):
        tier_tag = c.get('tier', '—')
        rot_tag = '🔺' if c.get('rot_bonus', 0) > 0 else ('🔻' if c.get('rot_bonus', 0) < 0 else '')
        ind_short = c.get('industry', '')[:10]
        s_tag = c.get('strategy', '?')
        # 操作建议
        if pos_mod == 0:
            advice = '❌禁止开仓'
        elif pos_mod < 1:
            pos_cfg = MODE.get('position', {})
            s_key = f"{s_tag}_A"
            normal_pos = pos_cfg.get(s_key, '1/4')
            advice = f"⚠半仓({normal_pos}→减半)"
        else:
            pos_cfg = MODE.get('position', {})
            s_key = f"{s_tag}_A"
            normal_pos = pos_cfg.get(s_key, '1/4')
            advice = f"✅正常({normal_pos})"
        print(f"  {rank:>3d} {s_tag:>4s} {c['code']:<12s} {c['price']:>7.2f} {c['score']:>4d} {tier_tag:>4s} {ind_short:>12s}{rot_tag}  {advice}")
else:
    print("\n  （无最终推荐候选）")

conn.close()
print("\n分析完成。")
