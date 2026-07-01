#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线选股器 V10主线趋势版 - 市场模式+情绪周期+梯队定位

根据 market_mode.py 判定的市场模式(M1-M5)：
    - 自动启用/禁用/试探策略
    - 动态调整板块准入线
  - 动态调整S3 X2放宽阈值
当前能力：
  - 情绪周期仓位修正(position_modifier)
  - 板块轮动加成/惩罚
  - 梯队定位(龙头/跟风/补涨)
  - 跨策略同行业去重
    - V8 S1评分、统一止盈和概念阶段过滤
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
# V12: concept_rotation/theme_family 已弃用（个股加成改走 stock_industry 行业轮动，不再依赖 concept_member）
import pandas as pd
import numpy as np


def _limit_up_threshold(code):
    """涨停判定阈值(%)：主板±10%→9.5；创业板(sz.30)/科创板(sh.68)±20%→19.0。
    用于统计近N日涨停次数，避免把创/科的普通大涨误计为涨停。"""
    c = str(code)
    if c.startswith('sz.30') or c.startswith('sh.68'):
        return 19.0
    return 9.5

# ═══ V6: 先判定市场模式 ═══
mode_key, mode_cfg, mode_details = detect_market_mode(verbose=True)
MODE = get_mode_params()
print(f"\n  >>> offline_screener V10主线趋势版 已加载模式: {MODE['mode']} | 综合情绪: {MODE['composite_emotion']}/10")
print(f"  >>> 策略: S1={MODE['strategies']['S1']} S2={MODE['strategies']['S2']} S3={MODE['strategies']['S3']} S4={MODE['strategies'].get('S4', 'disabled')}")
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

conn = get_connection(readonly=True)  # 纯查询脚本：只读连接，不开长事务、不建表

# ═══ 历史数据驱动的入场/止盈/止损计算 ═══

def _load_chip(code, price):
    """加载筹码数据，返回 chip_result 或 None。筹码分布需全历史，走 _get_kline_full（按需查库+缓存）。"""
    try:
        chip_df = _get_kline_full(code)  # 全历史，已数值化、dropna
        if chip_df.empty:
            return None
        return analyze_chip_cost(chip_df, current_price=price)
    except Exception:
        return None


def _mode_tp_tiers(mode, strategy):
    """返回策略止盈档位：S4给主线趋势更多空间，其余沿用V8统一止盈。"""
    if strategy == 'S4':
        return 0.06, 0.03
    return 0.04, 0.025


def _role_enabled(role):
    return role in ('primary', 'secondary', 'trial', 'watchonly')


def _role_label(role):
    return {'primary': '🟢主力', 'secondary': '🔵辅助', 'trial': '🟡试探', 'watchonly': '👀观察', 'disabled': '⛔禁用'}.get(role, role)


def _position_for_candidate(candidate, pos_mod=1.0):
    if pos_mod == 0:
        return '禁止开仓'
    pos_cfg = MODE.get('position', {})
    key = f"{candidate.get('strategy', 'S1')}_{candidate.get('grade', 'A')}"
    base_pos = pos_cfg.get(key, '1/4' if candidate.get('grade') == 'A' else '1/8')
    role = MODE['strategies'].get(candidate.get('strategy', 'S1'), 'disabled')
    note = _role_label(role)
    if pos_mod < 1:
        return f"{base_pos}×{pos_mod:g}（{note}，情绪修正）"
    return f"{base_pos}（{note}）"


def _sector_cutoff_for(strategy):
    cutoff = MODE.get('sector_cutoff', {}).get(strategy)
    if cutoff is not None:
        return cutoff
    role = MODE['strategies'].get(strategy, 'disabled')
    if role == 'trial':
        return {'M1': 0.50, 'M2': 0.60, 'M3': 0.70, 'M4': 0.70}.get(MODE['mode'], 0.70)
    return 0.50


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
    elif strategy == 'S4':
        # 回踩买入区固定锚在 MA5+0~5%，不随现价漂移(现价远离MA5时旧算法会区间倒挂)
        zone_lo = c['ma5'] * 0.995   # MA5 附近
        zone_hi = c['ma5'] * 1.05    # MA5 上方 5%
        if price <= zone_hi:
            # 现价已在回踩区内 → 以现价为中心的可买区，夹在 MA5+0~5% 内
            entry_lo = max(price * 0.98, zone_lo)
            entry_hi = min(price * 1.01, zone_hi)
            entry_basis = "主线趋势跟随区(MA5上方0~5%，不等缩量)"
        else:
            # 现价高于 MA5+5% → 等回踩到 MA5+0~5% 固定区间
            entry_lo = zone_lo
            entry_hi = zone_hi
            entry_basis = f"等回踩MA5上方0~5%区({zone_lo:.2f}~{zone_hi:.2f})，现价+{(price/c['ma5']-1)*100:.1f}%偏离勿追"
    else:  # S3
        entry_lo = price * 0.985
        entry_hi = price * 1.015
        entry_basis = "突破日收盘±1.5%"
        # 如果有筹码支撑且紧贴现价，用它作为下沿
        if sz and sz[1] >= price * 0.97:
            entry_lo = max(entry_lo, sz[1] * 0.99)
            entry_basis += f" 筹码{sz[1]:.2f}托底"

    # ═══ 2. 止盈目标 → V8统一+4%，筹码压力只做风险提示 ═══
    tp1_pct, trail_pct = _mode_tp_tiers(mode, strategy)
    fallback_tp1 = price * (1 + tp1_pct)
    fallback_tp2 = price * (1 + tp1_pct + trail_pct)
    tp_label = f"+{tp1_pct:.0%}"
    tp_prefix = "S4主线趋势" if strategy == 'S4' else "V8统一"

    if rz:
        tp1 = fallback_tp1
        tp2 = fallback_tp2
        if rz[0] <= fallback_tp1:
            tp1_basis = f"{tp_prefix}{tp_label}，但压力区{rz[0]:.2f}~{rz[1]:.2f}在目标内，需提前减仓"
        else:
            tp1_basis = f"{tp_prefix}{tp_label}，上方压力{rz[0]:.2f}~{rz[1]:.2f}作参考"
        tp2_basis = f"移动止盈：最高点回落{trail_pct:.1%}全清"
    else:
        tp1 = fallback_tp1
        tp2 = fallback_tp2
        tp1_basis = tp_label
        tp2_basis = f"移动止盈：最高点回落{trail_pct:.1%}全清"

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
    elif strategy == 'S4':
        hard_stop = max(price * 0.97, c['ma5'] * 0.985)
        stop_basis = "MA5×0.985或盘中-3%较近者(跌破=主线趋势失效)"
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


def _buy_status_for_candidate(candidate):
    chip = _load_chip(candidate['code'], candidate['price'])
    plan = _compute_plan(candidate.get('strategy', 'S1'), candidate, chip, MODE['mode'])
    price = candidate['price']
    if price > plan['entry_hi']:
        return '等回踩'
    if price < plan['entry_lo']:
        return '等确认'
    reward = plan['tp1'] - price
    risk = price - plan['hard_stop']
    rr_now = reward / risk if risk > 0 else 0
    if rr_now >= 1.25:
        return '可买'
    if rr_now >= 1.0:
        return '小仓'
    return '仅观察'


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

    plan_title = "S4主线趋势止盈" if strategy == 'S4' else "V8统一止盈"
    print(f"  ── 操作计划 ({plan_title}) ──")
    print(f"  入场区间: {p['entry_lo']:.2f} ~ {p['entry_hi']:.2f}  ← {p['entry_basis']}")
    tp1_pct, _trail_pct = _mode_tp_tiers(mode, strategy)
    tp_action = "+6%卖1/3" if strategy == 'S4' else "+4%卖50%"
    print(f"  止盈1: {p['tp1']:.2f}  ← {p['tp1_basis']}  │ {tp_action}")
    print(f"  止盈2: {p['tp2']:.2f}  ← {p['tp2_basis']}")
    print(f"  软止损(收盘-1.5%): {p['soft_stop']:.2f}  硬止损: {p['hard_stop']:.2f}  ← {p['stop_basis']}")
    # 盈亏比
    mid_entry = (p['entry_lo'] + p['entry_hi']) / 2
    reward = p['tp1'] - mid_entry
    risk = mid_entry - p['hard_stop']
    if risk > 0:
        rr = reward / risk
        rr_tag = "✅" if rr >= 1.25 else "⚠️" if rr >= 1.0 else "❌"
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

# ═══ 性能优化：K线分两级加载 ═══
# 扫描（S1~S4 的 [-60:] 等计算）只需近端数据 → 一次性读近 300 天入内存(~7s)，按 code 切片复用；
# 筹码分析（analyze_chip_cost）需要全历史的衰减分布 → 仅对进入分析的候选(~数十只)按需查全量并缓存。
# 原实现是四策略各自逐股全量查询(3205×4 趟)，已废弃。
import datetime as _dt
print("  >>> 加载近端K线到内存 ...", flush=True)
_kl_max_date = conn.execute('SELECT MAX(date) FROM kline_daily').fetchone()[0]
_kl_cutoff = (_dt.date.fromisoformat(str(_kl_max_date)) - _dt.timedelta(days=300)).isoformat()
# FORCE INDEX(PRIMARY)：钉死走主键顺序扫描，避免优化器在某些 cutoff 改走 idx_kline_date+filesort(实测会慢到 40s+)。
# 窗口取 300 天（≈200 交易日，远超扫描所需 60 根），且实测对"停牌复牌不足60根"的边界股零偏差。
_kl_all = pd.read_sql(
    'SELECT * FROM kline_daily FORCE INDEX(PRIMARY) WHERE date>=%s ORDER BY code, date',
    conn._conn, params=[_kl_cutoff]
)
if 'date' in _kl_all.columns:
    _kl_all['date'] = _kl_all['date'].astype(str)
for _c in ['open','high','low','close','volume','amount','turn','pctChg']:
    if _c in _kl_all.columns:
        _kl_all[_c] = pd.to_numeric(_kl_all[_c], errors='coerce')
# 预清洗一次：dropna(close/volume) 在加载期按 code 完成，避免 S1~S4 四策略主循环各自逐股 dropna
# （原实现：12772 次 dropna ≈ 累计 15s；现合并为加载期一次 groupby 内 dropna）
KLINE_BY_CODE = {code: grp.dropna(subset=['close', 'volume']).reset_index(drop=True)
                 for code, grp in _kl_all.groupby('code', sort=False)}
del _kl_all
print(f"  >>> 近端K线缓存就绪：{len(KLINE_BY_CODE)} 只股票（近300日）\n", flush=True)

_FULL_KLINE_CACHE = {}  # code → 全历史(已数值化、dropna)，供筹码分析按需使用


def _get_kline(code):
    """从内存缓存取单只股票近端K线（已在加载期数值化+dropna，按日期升序）。
    返回缓存引用，调用方只读（全部走 df['col'].values 取值，不原地改）——省去原每股 .copy()（×12820）。
    如需修改请自行 .copy()。"""
    df = KLINE_BY_CODE.get(code)
    if df is None:
        return pd.DataFrame()
    return df


def _get_kline_full(code):
    """取单只股票全历史K线（已数值化、dropna），按需查库并进程内缓存。筹码衰减分布需要全量，不能用近端窗口。"""
    cached = _FULL_KLINE_CACHE.get(code)
    if cached is not None:
        return cached
    try:
        df = pd.read_sql('SELECT * FROM kline_daily WHERE code=%s ORDER BY date', conn._conn, params=[code])
        if 'date' in df.columns:
            df['date'] = df['date'].astype(str)
        for _c in ['open','high','low','close','volume','amount','turn','pctChg']:
            if _c in df.columns:
                df[_c] = pd.to_numeric(df[_c], errors='coerce')
        df = df.dropna(subset=['close','volume']).reset_index(drop=True)
    except Exception:
        df = pd.DataFrame()
    _FULL_KLINE_CACHE[code] = df
    return df

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

# === V12: 板块归属退化为行业轮动（弃 concept_member，改走可靠的 sector_daily 行业强度）===
# 个股加成不再依赖稀疏/脏的 concept_member，改由所属行业的轮动强度推导。
# 行业轮动信号 = market_mode 的 sector_rotation(rotation_rising/falling) + 本脚本 sector_rank(5日动量分位)。
CONCEPT_HARD_EXCLUDE = set()  # 行业模式不按概念阶段硬排除；退潮由 rot_bonus -1 体现


def _industry_stage_bonus(ind):
    """按行业轮动强度返回 (stage, bonus)：持续主线+2 / 上升轮动+1 / 强势板块+1 / 退潮·普通0。"""
    if not ind:
        return '—', 0
    rank = sector_rank.get(ind)
    if ind in rotation_rising:
        if rank is not None and rank >= 0.8:
            return '持续主线', 2
        return '上升轮动', 1
    if rank is not None and rank >= 0.85:
        return '强势板块', 1
    if ind in rotation_falling:
        return '退潮', 0
    return '普通轮动', 0


def _load_industry_stage_by_code():
    """返回 code → 行业轮动元数据（沿用 concept_* 字段名，下游打分/输出无需改）。"""
    meta_by_code = {}
    try:
        rows = conn.execute(
            "SELECT code, industry FROM stock_industry WHERE industry IS NOT NULL"
        ).fetchall()
    except Exception as exc:
        print(f"  行业轮动: 读取 stock_industry 失败 ({exc})，跳过行业加成")
        return meta_by_code
    graded = 0
    for code, ind in rows:
        stage, bonus = _industry_stage_bonus(ind)
        meta_by_code[code] = {
            'concept_name': ind or '',
            'concept_stage': stage,
            'concept_bonus': bonus,
            'concept_score': round(sector_rank.get(ind, 0.5) * 100, 1),
            'family': '',
            'family_status': '',
            'family_active_days20': 0,
            'family_leader_concept': '',
        }
        if bonus > 0:
            graded += 1
    print(f"  行业轮动: {len(meta_by_code)} 只股票映射行业阶段，其中 {graded} 只处上升/强势行业(加成>0)")
    return meta_by_code


concept_stage_by_code = _load_industry_stage_by_code()

# 产业链家族加成（V11）已随 concept_member 一并弃用：家族归属同样依赖脏的 concept_member，
# 其"持续主线"语义已由行业轮动的 '持续主线' 档(上升轮动且 sector_rank≥0.8, +2)承接。

def _concept_meta(code):
    return concept_stage_by_code.get(code, {
        'concept_name': '',
        'concept_stage': '—',
        'concept_bonus': 0,
        'concept_score': 0,
        'family': '',
        'family_status': '',
        'family_active_days20': 0,
        'family_leader_concept': '',
    })


def _concept_blocked(concept_meta):
    return concept_meta.get('concept_stage') in CONCEPT_HARD_EXCLUDE

# === 大盘环境 ===
print("=" * 80)
print("  大盘环境速览 (缓存数据)")
print("=" * 80)
for idx_code, idx_name in [('sh.000001','上证指数'),('sz.399001','深证成指'),('sz.399006','创业板指')]:
    df = _get_kline(idx_code)
    if df.empty:
        print(f"  {idx_name}: 无缓存数据")
        continue
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
s1_cutoff = _sector_cutoff_for('S1')

print()
print("=" * 80)
if s1_role == 'disabled':
    print(f"  S1 蓄力候选 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
elif s1_role == 'trial':
    print(f"  S1 蓄力候选 🟡 当前{MODE['mode']}模式仅A级试探仓 ({len(codes)}只缓存股票)")
    print(f"  板块准入线: 强度分位≥{s1_cutoff:.2f}")
    print("=" * 80)
elif s1_role == 'watchonly':
    print(f"  S1 蓄力候选 👀 当前{MODE['mode']}模式降为观察池 ({len(codes)}只缓存股票)")
    print(f"  板块准入线: 强度分位≥{s1_cutoff:.2f}")
    print("=" * 80)
else:
    print(f"  蓄力候选扫描 {_role_label(s1_role)} ({len(codes)}只缓存股票)")
    print(f"  板块准入线: 强度分位≥{s1_cutoff:.2f}")
    print("=" * 80)
print()

results = []
for code in (codes if s1_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = _get_kline(code)          # 已在加载期 dropna，无需再逐股清洗
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
    limit_ups = np.sum(p60 >= _limit_up_threshold(code))
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

    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5)
    if sec_pct < s1_cutoff and ind:
        continue
    sec_bonus = 1 if sec_pct >= 0.7 else 0
    t_info = stock_tier.get(code, {})
    tier_label = t_info.get('tier', '—')
    tier_rank = t_info.get('rank_pct', 0.5)
    rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
    concept_meta = _concept_meta(code)
    if _concept_blocked(concept_meta):
        continue
    concept_bonus = concept_meta.get('concept_bonus', 0)

    # V8新增排除项：放量滞涨、缩量假反弹、逼近套牢密集区
    vol_stall = False
    for j in range(max(0, n - 3), n):
        start = max(0, j - 5)
        prev_avg_vol = np.mean(vols[start:j]) if j > start else vol20
        if prev_avg_vol > 0 and vols[j] > prev_avg_vol * 1.5 and pcts[j] < 1:
            vol_stall = True
            break
    if vol_stall:
        continue

    shrink_rebound = np.sum(pcts[-3:]) > 3 and vols[-1] < vols[-2] < vols[-3]
    if shrink_rebound:
        continue

    try:
        chip_for_score = analyze_chip_cost(_get_kline_full(code), current_price=last)
    except Exception:
        chip_for_score = None
    if chip_for_score and chip_for_score.get('resistance_zone'):
        rz = chip_for_score['resistance_zone']
        if rz[0] <= last * 1.05:
            continue

    score = 0; details = []
    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 4: score += 3; details.append("①缩量3")
    elif sc1 >= 3: score += 2; details.append("①缩量2")
    elif sc1 >= 1: score += 1; details.append("①缩量1")
    else: details.append("①缩量0")

    # ② 波动收敛（5分）：ATR、价格区间、均线间距、重心、支撑
    c5 = cls[-5:]; rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    true_ranges = []
    for tr_i in range(1, n):
        true_ranges.append(max(
            his[tr_i] - los[tr_i],
            abs(his[tr_i] - cls[tr_i - 1]),
            abs(los[tr_i] - cls[tr_i - 1]),
        ))
    atr5 = np.mean(true_ranges[-5:]) if len(true_ranges) >= 5 else 999
    atr20 = np.mean(true_ranges[-20:]) if len(true_ranges) >= 20 else atr5
    atr_ratio = atr5 / atr20 if atr20 > 0 else 999
    prev_low = np.min(los[-25:-5]) if n >= 25 else np.min(los[:-5]) if n > 5 else ma60
    support_ok = bool(np.all(cls[-5:] > ma60) or np.all(cls[-5:] >= prev_low))
    sc2 = sum([atr_ratio <= 0.6, rng5 <= 5, ma_sp <= 3, abs(cs) <= 1, support_ok])
    if sc2 >= 5: score += 5; details.append("②收敛5")
    elif sc2 >= 4: score += 4; details.append("②收敛4")
    elif sc2 >= 3: score += 3; details.append("②收敛3")
    elif sc2 >= 2: score += 1; details.append("②收敛1")
    else: details.append("②收敛0")

    # ③ 板块内排名（3分）：龙头/跟风 + 板块准入
    if tier_rank <= 0.15 and sec_pct >= 0.50:
        score += 3; details.append("③板块3")
    elif tier_rank <= 0.50 and sec_pct >= 0.50:
        score += 2; details.append("③板块2")
    elif tier_rank <= 0.50 or sec_pct >= 0.50:
        score += 1; details.append("③板块1")
    else:
        details.append("③板块0")

    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    sc4 = sum([no3, pct5r, -2 <= pct5s <= 3])
    if sc4 >= 3: score += 2; details.append("④交替2")
    elif sc4 >= 1: score += 1; details.append("④交替1")
    else: details.append("④交替0")

    # ⑤ 筹码结构（3分）：获利盘、上方压力、集中度
    if chip_for_score:
        chip_checks = [
            chip_for_score.get('profit_ratio', 0) >= 0.70,
            not chip_for_score.get('resistance_zone') or chip_for_score['resistance_zone'][0] > last * 1.05,
            chip_for_score.get('chip_concentration', 1) <= 0.15,
        ]
        sc5 = sum(chip_checks)
        if sc5 >= 3: score += 3; details.append("⑤筹码3")
        elif sc5 == 2: score += 2; details.append("⑤筹码2")
        elif sc5 == 1: score += 1; details.append("⑤筹码1")
        else: details.append("⑤筹码0")
    else:
        details.append("⑤筹码0")

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

    grade = 'A' if score >= 13 else 'B' if score >= 11 else 'C'

    if grade == 'A' or (grade == 'B' and s1_role != 'trial'):
        recent_low5 = float(np.min(los[-5:]))
        base_score = score
        rank_score = base_score + rot_bonus + concept_bonus
        results.append({
            'code': code, 'price': last, 'score': rank_score, 'base_score': base_score, 'grade': grade,
            'details': ' '.join(details),
            'signals': '|'.join(signals) if signals else '',
            'vr520': vr520, 'turn5': turn5, 'ma_sp': ma_sp, 'rng5': rng5,
            'pct60': pct60, 'dd60': dd60, 'cs': cs, 'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'industry': ind, 'sector_bonus': sec_bonus,
            'recent_low5': recent_low5,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            **concept_meta,
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
elif s1_role == 'trial':
    print(f"  通过筛选: {len(results)} 只 (🟡试探仓，仅A级进入)")
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
        rank_note = f" 排序分:{c['score']}" if c.get('score') != c.get('base_score') else ""
        print(f"  评分: {c.get('base_score', c['score'])}/16 ({c['grade']}级){rank_note}")
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
s2_cutoff = _sector_cutoff_for('S2')

print()
print("=" * 80)
if s2_role == 'disabled':
    print(f"  S2 大阳后缩量横盘 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
else:
    role_tag = _role_label(s2_role)
    print(f"  S2 大阳后缩量横盘扫描 {role_tag} ({len(codes)}只缓存股票)")
    print(f"  板块准入线: 强度分位≥{s2_cutoff:.2f}")
    print("=" * 80)
    print()

s2_results = []
for code in (codes if s2_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = _get_kline(code)          # 已在加载期 dropna，无需再逐股清洗
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
    if grade == 'A' or (grade == 'B' and s2_role != 'trial'):
        # V6.1: 梯队定位 + 轮动加成
        t_info = stock_tier.get(code, {})
        tier_label = t_info.get('tier', '—')
        tier_rank = t_info.get('rank_pct', 0.5)
        rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
        concept_meta = _concept_meta(code)
        if _concept_blocked(concept_meta):
            continue
        concept_bonus = concept_meta.get('concept_bonus', 0)
        base_score = score
        rank_score = base_score + rot_bonus + concept_bonus
        s2_results.append({
            'code': code, 'price': last, 'score': rank_score, 'base_score': base_score, 'grade': grade,
            'details': ' '.join(details),
            'bc_date': bc_date, 'bc_pct': bc_pct, 'bc_close': bc_close,
            'bc_open': bc_open, 'vol_shrink': vol_shrink,
            'price_hold': price_hold, 'days_after': days_after,
            'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'industry': ind, 'sector_bonus': 1 if sec_pct >= 0.7 else 0,
            'float_mcap': float_mcap,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            **concept_meta,
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
        rank_note = f" 排序分:{c['score']}" if c.get('score') != c.get('base_score') else ""
        print(f"  评分: {c.get('base_score', c['score'])}/8 ({c['grade']}级){rank_note}")
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
s3_cutoff = _sector_cutoff_for('S3')
s3_x2_limit = MODE['s3_x2_limit']
s3_x2_relax = MODE['s3_x2_relax']

print()
print("=" * 80)
if s3_role == 'disabled':
    print(f"  S3 放量突破新高 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
else:
    role_tag = _role_label(s3_role)
    print(f"  S3 放量突破新高扫描 {role_tag} ({len(codes)}只缓存股票)")
    print(f"  板块准入线: 强度分位≥{s3_cutoff:.2f}  X2限制: 5日涨>{s3_x2_limit}%排除")
    if s3_x2_relax:
        for cond, val in s3_x2_relax.items():
            print(f"  X2放宽: {cond} → 允许到{val}%")
    print("=" * 80)
    print()

s3_results = []
for code in (codes if s3_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = _get_kline(code)          # 已在加载期 dropna，无需再逐股清洗
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
    if grade == 'A' or (grade == 'B' and s3_role != 'trial'):
        ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
        # V6.1: 梯队定位 + 轮动加成
        t_info = stock_tier.get(code, {})
        tier_label = t_info.get('tier', '—')
        tier_rank = t_info.get('rank_pct', 0.5)
        rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
        concept_meta = _concept_meta(code)
        if _concept_blocked(concept_meta):
            continue
        concept_bonus = concept_meta.get('concept_bonus', 0)
        base_score = score
        rank_score = base_score + rot_bonus + concept_bonus
        s3_results.append({
            'code': code, 'price': last, 'score': rank_score, 'base_score': base_score, 'grade': grade,
            'details': ' '.join(details),
            'brk_pct': brk_pct, 'vol_ratio': vol_ratio,
            'chg5': chg5, 'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'industry': ind, 'sector_bonus': 1 if sec_pct >= 0.7 else 0,
            'float_mcap': float_mcap,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            **concept_meta,
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
        rank_note = f" 排序分:{c['score']}" if c.get('score') != c.get('base_score') else ""
        print(f"  评分: {c.get('base_score', c['score'])}/6 ({c['grade']}级){rank_note}")
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
# S4：主线趋势跟随扫描
# ═══════════════════════════════════════════════════════════════

s4_role = MODE['strategies'].get('S4', 'disabled')
s4_cutoff = _sector_cutoff_for('S4')
# V12对齐修复：concept_stage 现由 _industry_stage_bonus 产出，取值只有
# 持续主线/上升轮动/强势板块/退潮/普通轮动/—。旧白名单(主线/主线分歧/新晋发酵/强势观察)
# 来自已弃用的 concept_rotation，与新档位零交集，曾导致 S4 恒空(死代码)。改放行三档强势行业。
s4_allowed_stages = {'持续主线', '上升轮动', '强势板块'}

print()
print("=" * 80)
if s4_role == 'disabled':
    print(f"  S4 主线趋势跟随 ⛔ 当前{MODE['mode']}模式禁用")
    print("=" * 80)
else:
    role_tag = _role_label(s4_role)
    print(f"  S4 主线趋势跟随扫描 {role_tag} ({len(codes)}只缓存股票)")
    print(f"  板块准入线: 强度分位≥{s4_cutoff:.2f}  只做持续主线/上升轮动/强势板块里的龙头或强跟风")
    print("=" * 80)
    print()

s4_results = []
# 追高门槛拒因统计：以下计数都发生在 stage+梯队 闸门之后，故均为"强势主线+前排"龙头被追高线拦下的数量
s4_diag = {'stage_block': 0, 'chg5_hi': 0, 'pct_hi': 0, 'dist_hi': 0, 'close_weak': 0, 'vol_bad': 0, 'grade_low': 0}
for code in (codes if s4_role != 'disabled' else []):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = _get_kline(code)          # 已在加载期 dropna，无需再逐股清洗
    if len(df) < 60:
        continue

    cls = df['close'].values; ops = df['open'].values
    his = df['high'].values; los = df['low'].values
    vols = df['volume'].values; turns = df['turn'].values
    pcts = df['pctChg'].values; amts = df['amount'].values
    n = len(df); last = cls[-1]

    if last < 3 or last > 200:
        continue
    avg_amt_20 = np.mean(amts[-20:]) / 10000
    if avg_amt_20 < 2000:
        continue

    t_last = turns[-1] if len(turns) > 0 else 0
    if t_last <= 0:
        continue
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 800:
        continue

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
    if not (last > ma5 > ma10 and ma20 > ma60):
        continue

    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5)
    if sec_pct < s4_cutoff and ind:
        continue

    concept_meta = _concept_meta(code)
    if _concept_blocked(concept_meta):
        continue
    concept_stage = concept_meta.get('concept_stage', '—')
    if concept_stage not in s4_allowed_stages:
        s4_diag['stage_block'] += 1
        continue

    t_info = stock_tier.get(code, {})
    tier_label = t_info.get('tier', '—')
    tier_rank = t_info.get('rank_pct', 0.5)
    if tier_rank > 0.50:
        continue
    if concept_stage != '持续主线' and tier_rank > 0.35:
        continue

    chg5 = (cls[-1] / cls[-6] - 1) * 100 if n >= 6 else 0
    chg10 = (cls[-1] / cls[-11] - 1) * 100 if n >= 11 else 0
    # P2: 强势主线成员放宽反追高门槛(持续主线放最松)，让走强的龙头也能入选；
    # 离MA5较远者由 _buy_status 自动标'等回踩'，止损/仓位/D2D3 纪律不变。
    is_core_line = concept_stage == '持续主线'
    chg5_cap = 50 if is_core_line else 40
    dist_cap = 11 if is_core_line else 10
    if chg5 < 5 or chg5 > chg5_cap:
        if chg5 > chg5_cap:
            s4_diag['chg5_hi'] += 1
        continue
    if pcts[-1] > 9.2 or turns[-1] > 12:
        s4_diag['pct_hi'] += 1
        continue

    dist_ma5 = (last / ma5 - 1) * 100 if ma5 > 0 else 999
    if dist_ma5 > dist_cap:
        s4_diag['dist_hi'] += 1
        continue
    day_range = his[-1] - los[-1]
    close_pos = (last - los[-1]) / day_range if day_range > 0 else 0.5
    if close_pos < 0.50:
        s4_diag['close_weak'] += 1
        continue
    # 原'主线分歧'分歧日强确认过滤随 V12 档位迁移失效(新档位无分歧态)；
    # 是否对'强势板块'档加"今日翻红+强收"确认留作 P2 决策，P0 不引入新排除。

    vol20 = np.mean(vols[-20:])
    vol_ratio = vols[-1] / vol20 if vol20 > 0 else 0
    if vol_ratio < 0.80 or vol_ratio > 4.50:
        s4_diag['vol_bad'] += 1
        continue

    score = 0; details = []
    if concept_stage == '持续主线':
        score += 2; details.append("①持续主线2")
    else:
        score += 1; details.append(f"①{concept_stage}1")

    trend_checks = sum([last > ma5, ma5 > ma10, ma10 > ma20, ma20 > ma60])
    if trend_checks >= 4:
        score += 2; details.append("②趋势2")
    elif trend_checks >= 3:
        score += 1; details.append("②趋势1")
    else:
        details.append("②趋势0")

    if tier_rank <= 0.15 and sec_pct >= 0.75:
        score += 2; details.append("③龙头2")
    elif tier_rank <= 0.35 and sec_pct >= 0.70:
        score += 1; details.append("③前排1")
    else:
        details.append("③梯队0")

    if 8 <= chg5 <= 25 and pcts[-1] > 0:
        score += 2; details.append(f"④强度2({chg5:+.1f}%)")
    elif 5 <= chg5 <= 35 and pcts[-1] >= 0:
        score += 1; details.append(f"④强度1({chg5:+.1f}%)")
    else:
        details.append(f"④强度0({chg5:+.1f}%)")

    if dist_ma5 <= 3 and close_pos >= 0.65:
        score += 2; details.append(f"⑤位置2(MA5+{dist_ma5:.1f}%)")
    elif dist_ma5 <= 5.5 and close_pos >= 0.50:
        score += 1; details.append(f"⑤位置1(MA5+{dist_ma5:.1f}%)")
    else:
        details.append(f"⑤位置0(MA5+{dist_ma5:.1f}%)")

    if 1.1 <= vol_ratio <= 3.5:
        score += 1; details.append(f"⑥量能1({vol_ratio:.1f}x)")
    else:
        details.append(f"⑥量能0({vol_ratio:.1f}x)")

    grade = 'A' if score >= 8 else ('B' if score >= 7 else 'C')
    if grade == 'A' or (grade == 'B' and s4_role != 'trial'):
        rot_bonus = 1 if ind in rotation_rising else (-1 if ind in rotation_falling else 0)
        concept_bonus = concept_meta.get('concept_bonus', 0)
        base_score = score
        rank_score = base_score + rot_bonus + concept_bonus
        s4_results.append({
            'code': code, 'price': last, 'score': rank_score, 'base_score': base_score, 'grade': grade,
            'details': ' '.join(details),
            'chg5': chg5, 'chg10': chg10, 'pct_today': pcts[-1],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'dist_ma5': dist_ma5, 'close_pos': close_pos, 'vol_ratio': vol_ratio,
            'industry': ind, 'sector_bonus': 1 if sec_pct >= 0.75 else 0,
            'float_mcap': float_mcap,
            'tier': tier_label, 'tier_rank': tier_rank, 'rot_bonus': rot_bonus,
            **concept_meta,
            'strategy': 'S4',
        })
    else:
        s4_diag['grade_low'] += 1

s4_results.sort(key=lambda x: (0 if x['grade']=='A' else 1, -x.get('sector_bonus',0), 0 if x.get('tier')=='龙头' else 1, -x['score']))

if s4_role != 'disabled':
    print(f"  通过筛选: {len(s4_results)} 只")
    print(f"  ├─ 追高线拒掉的强势前排龙头: 距MA5超限{s4_diag['dist_hi']}只 / 5日涨超限{s4_diag['chg5_hi']}只 / 今日封板级{s4_diag['pct_hi']}只")
    print(f"  ├─ 过追高线后又被后续闸门刷掉: 今日收在下半段{s4_diag['close_weak']}只 / 量比异常{s4_diag['vol_bad']}只 / 评分未达档{s4_diag['grade_low']}只")
    print(f"  └─ 行业档位未达强势(非持续主线/上升轮动/强势板块)被挡: {s4_diag['stage_block']}只")
print()
if s4_results:
    print(f"  {'排名':>4s} {'代码':<12s} {'价格':>7s} {'今涨':>7s} {'评分':>4s} {'级':>2s} {'梯队':>4s} {'板块':>10s} {'概念':>12s} {'5日涨':>7s} {'距MA5':>7s} {'量比':>6s} 明细")
    print("-" * 150)
    for rank, c in enumerate(s4_results[:20], 1):
        sec_tag = f"🔥{c['industry'][:6]}" if c.get('sector_bonus') else c.get('industry','')[:8]
        tier_tag = c.get('tier', '—')
        rot_tag = '🔺' if c.get('rot_bonus', 0) > 0 else ('🔻' if c.get('rot_bonus', 0) < 0 else '')
        concept_text = c.get('concept_stage', '—')
        if c.get('concept_name'):
            concept_text = f"{concept_text}:{c['concept_name'][:6]}"
        print(f"  {rank:>3d} {c['code']:<12s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['score']:>4d} {c['grade']:>2s} {tier_tag:>4s} {sec_tag:>10s}{rot_tag} {concept_text:>12s} {c['chg5']:>+6.1f}% {c['dist_ma5']:>6.1f}% {c['vol_ratio']:>5.1f}x  {c['details']}")

    print()
    print("=" * 80)
    print("  S4 TOP 3 详细分析")
    print("=" * 80)
    for rank, c in enumerate(s4_results[:3], 1):
        ind_info = f" [{c.get('industry','')}]" if c.get('industry') else ""
        tier_info = f" 梯队:{c.get('tier','—')}" if c.get('tier') else ""
        print(f"\n  ── S4 #{rank} {c['code']}{ind_info}{tier_info} ──")
        print(f"  价格: {c['price']:.2f}  今日涨跌: {c['pct_today']:+.2f}%  5日涨: {c['chg5']:+.1f}%  10日涨: {c['chg10']:+.1f}%")
        print(f"  概念: {c.get('concept_stage','—')} {c.get('concept_name','')}  距MA5: {c['dist_ma5']:.1f}%  收盘位置: {c['close_pos']:.0%}  量比: {c['vol_ratio']:.1f}x")
        print(f"  MA5={c['ma5']:.2f}  MA10={c['ma10']:.2f}  MA20={c['ma20']:.2f}  MA60={c['ma60']:.2f}")
        rank_note = f" 排序分:{c['score']}" if c.get('score') != c.get('base_score') else ""
        print(f"  评分: {c.get('base_score', c['score'])}/10 ({c['grade']}级){rank_note}")
        print(f"  明细: {c['details']}")
        chip = _load_chip(c['code'], c['price'])
        plan = _compute_plan('S4', c, chip, MODE['mode'])
        _print_plan(plan, MODE['mode'], 'S4')
        _print_chip(chip)
else:
    print("  （无符合条件的S4主线趋势候选）")

# ═══════════════════════════════════════════════════════════════
# V6.1: 跨策略同行业去重 + 仓位修正 + 最终推荐
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("  V10主线趋势版 最终推荐（同行业去重 + 仓位修正）")
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
for r in s4_results:
    all_candidates.append(r)

# V12档位对齐：按行业轮动强度排序(强→弱)，让持续主线/上升轮动/强势板块的候选排在前面
concept_priority_order = {'持续主线': 0, '上升轮动': 1, '强势板块': 2, '普通轮动': 3, '退潮': 4}
def get_concept_priority(c):
    return concept_priority_order.get(c.get('concept_stage', '—'), 9)

def get_mainline_bias(c):
    return 0 if c.get('strategy') == 'S4' and get_concept_priority(c) <= 2 else 1

strategy_priority = {'primary': 0, 'secondary': 1, 'trial': 2, 'watchonly': 3}
def get_strategy_priority(c):
    s = c.get('strategy', 'S1')
    role = MODE['strategies'].get(s, 'disabled')
    return strategy_priority.get(role, 9)

buy_status_priority = {'可买': 0, '小仓': 1, '等回踩': 2, '等确认': 3, '仅观察': 4}
for c in all_candidates:
    c['_buy_status'] = _buy_status_for_candidate(c)

# 同行业只保留最高分
seen_industry = {}
dedup_results = []
for c in sorted(all_candidates, key=lambda x: (buy_status_priority.get(x.get('_buy_status'), 9), get_strategy_priority(x), get_concept_priority(x), get_mainline_bias(x), 0 if x.get('tier')=='龙头' else 1, -x['score'])):
    ind = c.get('industry', '')
    if ind and ind in seen_industry:
        continue  # 同行业已有更高分的
    if ind:
        seen_industry[ind] = c['code']
    dedup_results.append(c)

# 按策略优先级排序：主力策略 > 辅助策略 > 试探策略
dedup_results.sort(key=lambda x: (buy_status_priority.get(x.get('_buy_status'), 9), get_strategy_priority(x), get_concept_priority(x), get_mainline_bias(x), 0 if x.get('tier')=='龙头' else 1, -x['score']))

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
    print(f"  {'排名':>4s} {'策略':>4s} {'代码':<12s} {'价格':>7s} {'评分':>6s} {'梯队':>4s} {'板块':>12s} {'概念阶段':>10s} {'买点/仓位'}")
    print("-" * 115)
    for rank, c in enumerate(dedup_results[:10], 1):
        tier_tag = c.get('tier', '—')
        rot_tag = '🔺' if c.get('rot_bonus', 0) > 0 else ('🔻' if c.get('rot_bonus', 0) < 0 else '')
        ind_short = c.get('industry', '')[:10]
        s_tag = c.get('strategy', '?')
        if pos_mod == 0:
            advice = '❌禁止开仓'
        else:
            advice = f"{c.get('_buy_status', _buy_status_for_candidate(c))}｜{_position_for_candidate(c, pos_mod=pos_mod)}"
        score_max = {'S1': 16, 'S2': 8, 'S3': 6, 'S4': 10}.get(s_tag, '?')
        score_text = f"{c.get('base_score', c['score'])}/{score_max}"
        if c.get('score') != c.get('base_score', c['score']):
            score_text += f"→{c['score']}"
        concept_text = c.get('concept_stage', '—')
        if c.get('concept_name'):
            concept_text = f"{concept_text}:{c['concept_name'][:6]}"
        fam = c.get('family', '')
        fam_tag = f" 〔{fam}·{c.get('family_status','')}{c.get('family_active_days20',0)}/20〕" if fam else ''
        print(f"  {rank:>3d} {s_tag:>4s} {c['code']:<12s} {c['price']:>7.2f} {score_text:>6s} {tier_tag:>4s} {ind_short:>12s}{rot_tag} {concept_text:>10s}{fam_tag}  {advice}")
else:
    print("\n  （无最终推荐候选）")

conn.close()
print("\n分析完成。")
