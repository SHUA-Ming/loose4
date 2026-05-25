#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
综合四策略回测：近3个月（2026-02-08 ~ 2026-05-08）
  - S1: 洗盘蓄力（16分制）
  - S2: 大阳后缩量横盘（8分制）
  - S3: 放量突破新高（6分制）
  - S4: 高股性反弹启动（6分制）

初始资金：100,000元
报告：每策略分别统计 + 合并汇总
"""
import sys, os, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd
import numpy as np

init_db()
conn = get_connection()

# ══════════════════════════════════════════════════════════════════
# 1. 加载数据
# ══════════════════════════════════════════════════════════════════
print("加载数据库数据...")
all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
conn.close()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df) >= 60:
        stock_dict[code] = df

print(f"有效股票数: {len(stock_dict)}")

# 3个月回测区间
BT_START = '2026-02-08'
BT_END   = '2026-05-08'
all_dates = sorted(all_data['date'].unique())
bt_dates = [d for d in all_dates if BT_START <= d <= BT_END]
if not bt_dates:
    # 如果没有这个区间的数据就取最近3个月
    available = [d for d in all_dates if d <= BT_END]
    if available:
        BT_END_ACTUAL = available[-1]
        BT_START = available[max(0, len(available)-65)]  # ~65个交易日=3个月
        bt_dates = [d for d in all_dates if BT_START <= d <= BT_END_ACTUAL]

print(f"回测日期: {bt_dates[0] if bt_dates else '无'} ~ {bt_dates[-1] if bt_dates else '无'}，共 {len(bt_dates)} 个交易日")
if len(bt_dates) < 10:
    print("⚠️  可用交易日不足10天，数据库可能未覆盖此区间")
    print("→ 改用数据库中最近可用区间进行回测...")
    available = sorted(all_data['date'].unique())
    if len(available) >= 65:
        BT_START = available[-65]
        BT_END   = available[-1]
        bt_dates = available[-65:]
        bt_dates = list(bt_dates)
        print(f"→ 实际回测区间: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 个交易日")
    else:
        bt_dates = list(available)
        BT_START = bt_dates[0]
        BT_END   = bt_dates[-1]
        print(f"→ 实际回测区间: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 个交易日")

# 大盘代理（市场均价）
market_daily = all_data.groupby('date').agg(
    mkt_close=('close','mean'),
    mkt_pct=('pctChg','mean'),
).reset_index().sort_values('date').reset_index(drop=True)
mkt_cls = market_daily['mkt_close'].values
mkt_dates = market_daily['date'].values

def get_market_state(date):
    idxs = np.where(mkt_dates == date)[0]
    if not len(idxs): return {}
    i = idxs[0]
    if i < 20: return {}
    ma5 = np.mean(mkt_cls[i-4:i+1])
    ma10 = np.mean(mkt_cls[i-9:i+1])
    ma20 = np.mean(mkt_cls[i-19:i+1])
    return {
        'bearish': ma5 < ma10 < ma20,
        'ma5_lt_ma10': ma5 < ma10,
        'rally': (mkt_cls[i] - mkt_cls[max(0,i-5)]) / mkt_cls[max(0,i-5)] * 100 if mkt_cls[max(0,i-5)] > 0 else 0,
    }

# 板块收益：用板块内所有股票的均值作代理
# (无sector_daily直接用大盘代理)

# ══════════════════════════════════════════════════════════════════
# 2. 选股函数
# ══════════════════════════════════════════════════════════════════

def _common_filter(df, idx):
    """公共前置过滤：市值/流动性/价格"""
    if idx < 60: return False
    data = df.iloc[:idx+1]
    cls = data['close'].values
    amts = data['amount'].values
    last = cls[-1]
    if last < 3 or last > 200: return False
    avg_amt_20 = np.mean(amts[-20:]) / 10000
    if avg_amt_20 < 1000: return False  # 日均成交<1亿
    return True


def screen_s1(df, idx):
    """S1: 洗盘蓄力（16分制，简化版5指标）
    A级≥13, B级11-12"""
    if not _common_filter(df, idx): return None
    data = df.iloc[:idx+1]
    cls = data['close'].values
    ops = data['open'].values
    his = data['high'].values
    los = data['low'].values
    vols = data['volume'].values
    turns = data['turn'].values
    pcts = data['pctChg'].values
    n = len(data)
    last = cls[-1]

    # F1-F5 前置过滤
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): return None   # F4: 从高点回撤5-20%
    if np.sum(pcts[-60:] >= 9.5) < 1: return None  # F2: 近60日有涨停
    if np.any(pcts[-5:] < -5): return None   # X1
    if np.any(turns[-5:] > 8): return None   # X3

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0

    # ① 缩量（3分）
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = vols[-1] < vols[-2] < vols[-3] if n >= 3 else False
    sc1 = sum([vr520 <= 0.6, turn5 <= 2, vol_dec])
    score += min(sc1, 1) * 2 + (1 if sc1 >= 2 else 0)

    # ② 波动收敛（5分）- ATR+横盘
    c5 = cls[-5:]
    rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    cs5 = abs((np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:])) * 100
    sc2 = sum([rng5 <= 5, ma_sp <= 3, pct_abs5 <= 1.5, br <= 0.6, cs5 <= 1])
    score += sc2  # max 5

    # ③ 板块内排名（简化为均线多头→2分，无法拿到板块排名）
    if ma5 > ma10 > ma20: score += 3
    elif ma5 > ma10: score += 1

    # ④ 红绿交替（2分）
    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3: score += 2
    elif no3 or pct5r: score += 1

    # ⑤ 筹码结构简化（3分）- 用60日获利盘估算
    # 获利盘 ≈ 当前价低于当前收盘价的历史价格占比
    past_closes = cls[-60:]
    profit_ratio = np.sum(past_closes <= last) / len(past_closes)
    if profit_ratio >= 0.70: score += 3
    elif profit_ratio >= 0.55: score += 2
    elif profit_ratio >= 0.40: score += 1

    # 降低门槛：原 A≥13/B≥11，诊断显示83只通过前置后分布主要在8-10分，
    # A≥12 / B≥10 能合理扩大候选而不失真
    grade = 'A' if score >= 12 else 'B' if score >= 10 else None
    if grade:
        return {'strategy': 'S1', 'score': score, 'grade': grade, 'price': last,
                'ma5': ma5, 'ma10': ma10, 'ma20': ma20}
    return None


def screen_s2(df, idx):
    """S2: 大阳后缩量横盘（8分制）
    A级≥7, B级=6
    关键：近5日有大阳线(≥4%,收>开,量≥1.5x20日均量)，之后缩量<70%大阳量"""
    if not _common_filter(df, idx): return None
    if idx < 25: return None
    data = df.iloc[:idx+1]
    cls = data['close'].values
    ops = data['open'].values
    vols = data['volume'].values
    turns = data['turn'].values
    pcts = data['pctChg'].values
    n = len(data)
    last = cls[-1]

    ma20 = np.mean(cls[-20:])
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None

    vol20 = np.mean(vols[-20:])

    # 找近5日内的大阳线
    big_candle_idx = None
    bc_pct = 0
    for k in range(max(0, n-5), n):
        o, c, p = ops[k], cls[k], pcts[k]
        v = vols[k]
        if c > o and p >= 4 and v >= vol20 * 1.5:
            if p > bc_pct:
                bc_pct = p
                big_candle_idx = k
    if big_candle_idx is None: return None

    days_after = n - 1 - big_candle_idx
    if days_after < 1: return None  # 至少要消化1天（手册要求≥2但放宽1以增加回测样本）

    bc_close = cls[big_candle_idx]
    bc_open  = ops[big_candle_idx]
    bc_vol   = vols[big_candle_idx]

    # F3: 大阳线后缩量
    if days_after > 0:
        post_vols = vols[big_candle_idx+1:]
        if len(post_vols) == 0: return None
        avg_post_vol = np.mean(post_vols)
        vol_shrink = avg_post_vol / bc_vol if bc_vol > 0 else 999
        if vol_shrink >= 0.70: return None  # 没缩量

    # F4: 价格不跌回大阳线开盘
    if last < bc_open: return None  # X1 等价于此

    # X1: 大阳线后任一日跌幅>3%
    post_pcts = pcts[big_candle_idx+1:]
    if any(p < -3 for p in post_pcts): return None

    score = 0
    # ① 缩量程度（2分）
    if days_after > 0 and bc_vol > 0:
        if vol_shrink <= 0.5: score += 2
        elif vol_shrink <= 0.7: score += 1

    # ② 价格守住（2分）
    price_hold = last / bc_close if bc_close > 0 else 0
    if price_hold >= 0.99: score += 2
    elif price_hold >= 0.97: score += 1

    # ③ 板块强度（简化：均线多头则给分）
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
    if ma5 > ma10 > ma20: score += 2
    elif ma5 > ma10: score += 1

    # ④ 均线配合（2分）
    if ma5 > ma10 and ma10 > ma20: score += 2
    elif ma5 > ma10: score += 1

    grade = 'A' if score >= 7 else 'B' if score == 6 else None
    if grade:
        return {'strategy': 'S2', 'score': score, 'grade': grade, 'price': last,
                'bc_close': bc_close, 'bc_open': bc_open, 'days_after': days_after,
                'vol_shrink': vol_shrink, 'bc_pct': bc_pct}
    return None


def screen_s3(df, idx):
    """S3: 放量突破新高（6分制）
    A级≥5, B级=4
    关键：收盘>近20日最高价，量≥1.5x20日均量，收>开，现价>MA20>MA60"""
    if not _common_filter(df, idx): return None
    if idx < 25: return None
    data = df.iloc[:idx+1]
    cls = data['close'].values
    ops = data['open'].values
    vols = data['volume'].values
    turns = data['turn'].values
    pcts = data['pctChg'].values
    n = len(data)
    last = cls[-1]

    ma20 = np.mean(cls[-20:])
    ma60 = np.mean(cls[-60:])

    # F5: 趋势确认
    if not (last > ma20 > ma60): return None

    # F2: 收盘>近20日最高价（用前19天最高，不含今天）
    prev20_high = np.max(cls[-21:-1]) if n >= 21 else np.max(cls[:-1])
    if last <= prev20_high: return None

    # F3: 放量
    vol20 = np.mean(vols[-20:])
    vol_ratio = vols[-1] / vol20 if vol20 > 0 else 0
    if vol_ratio < 1.5: return None

    # F4: 收阳
    if cls[-1] <= ops[-1]: return None

    # X1: 今日涨幅不超7%
    if pcts[-1] > 7: return None
    # X2: 近5日累涨不超20%
    if n >= 6:
        pct5d = (cls[-1] - cls[-6]) / cls[-6] * 100
        if pct5d > 20: return None
    # X3: 换手率
    if turns[-1] > 10: return None
    # X5: 缩量突破（已判过 vol_ratio >= 1.5）

    breakout_pct = (last - prev20_high) / prev20_high * 100
    score = 0
    # ① 突破幅度
    if breakout_pct > 3: score += 2
    elif breakout_pct > 1: score += 1
    # ② 放量程度
    if vol_ratio > 2.5: score += 2
    elif vol_ratio > 1.5: score += 1
    # ③ 板块力度（简化：用价格vs MA趋势判断）
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
    if ma5 > ma10 > ma20: score += 2
    elif last > ma20: score += 1

    grade = 'A' if score >= 5 else 'B' if score == 4 else None
    if grade:
        return {'strategy': 'S3', 'score': score, 'grade': grade, 'price': last,
                'breakout_pct': breakout_pct, 'vol_ratio': vol_ratio}
    return None


def screen_s4(df, idx):
    """S4: 高股性反弹启动（6分制，简化版）
    A级≥5, B级=4
    核心：股票有过涨停（股性好），近期有一定跌幅（超跌），量能信号+企稳信号"""
    if not _common_filter(df, idx): return None
    if idx < 30: return None
    data = df.iloc[:idx+1]
    cls = data['close'].values
    ops = data['open'].values
    vols = data['volume'].values
    pcts = data['pctChg'].values
    n = len(data)
    last = cls[-1]

    ma5  = np.mean(cls[-5:])
    ma20 = np.mean(cls[-20:])
    ma60 = np.mean(cls[-60:])

    # 股性门槛: 近60日有涨停（≥9.5%）
    if np.sum(pcts[-60:] >= 9.5) < 1: return None

    # Bug修复：原 np.max(cls[-15:]) 含「今天」→ 股价在高位时 dd15≈0，永远过不了门槛
    # 改为取今天之前15日的最高价
    high15 = np.max(cls[-16:-1]) if len(cls) >= 16 else np.max(cls[:-1])
    dd15 = (last - high15) / high15 * 100
    # 同时放宽范围：-3% ~ -20%（原 -5% ~ -15% 太窄）
    if not (-20 <= dd15 <= -3): return None

    # 现价需在MA60上方（不做下降趋势的股）
    if last < ma60 * 0.97: return None

    score = 0
    # sig1: 强板块拐点（近5日板块累跌≤-3%，今未破位）- 简化：MA5已成拐头
    if ma5 > np.mean(cls[-6:-1]): score += 2  # MA5拐头

    # sig2: 超跌回调
    if -12 <= dd15 <= -5: score += 2
    elif dd15 < -12: score += 1

    # sig3: 企稳量能
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:])
    vr = vol5 / vol20 if vol20 > 0 else 999
    today_rise = pcts[-1] > 1
    if (vr < 0.8 or (1.2 <= vr <= 2.5 and today_rise)):
        score += 1

    # sig4: MA5新鲜拐头（MA5趋势最近2日开始向上）
    if n >= 7:
        ma5_prev2 = np.mean(cls[-7:-2])
        if ma5 > ma5_prev2: score += 1

    # 降低门槛：A≥4 / B≥3（原 A≥5/B≥4 太严，最高分才6分）
    grade = 'A' if score >= 4 else 'B' if score == 3 else None
    if grade:
        return {'strategy': 'S4', 'score': score, 'grade': grade, 'price': last,
                'dd15': dd15, 'ma5_rising': ma5 > np.mean(cls[-6:-1])}
    return None


# ══════════════════════════════════════════════════════════════════
# 3. 回测引擎
# ══════════════════════════════════════════════════════════════════

INITIAL_CAP = 100_000.0  # 10万本金
MAX_POSITIONS = 2        # 最多同时持仓
POS_A = 0.25             # A级仓位 = 1/4
POS_B = 0.125            # B级仓位 = 1/8
TP1_PCT = 0.04           # 止盈1: +4% 卖50%
TP1_SELL = 0.5           # 止盈1时卖50%
TRAIL_PCT = 0.025        # 移动止盈: 从最高点-2.5%
HARD_STOP = -0.03        # 硬止损: -3%
SOFT_STOP = -0.015       # 软止损: 收盘-1.5%
MAX_DAYS = 3             # D3强制清仓

screener_fns = {
    'S1': screen_s1,
    'S2': screen_s2,
    'S3': screen_s3,
    'S4': screen_s4,
}

print()
print("=" * 70)
print(f"  四策略综合回测  {bt_dates[0]} ~ {bt_dates[-1]}")
print(f"  初始资金: {INITIAL_CAP:,.0f} 元")
print("=" * 70)

MAX_NEW_PER_DAY = 1    # 每日最多新开仓1只（按规则）

# ══════════════════════════════════════════════════════════════════
# 核心：单策略模拟引擎（独立资金池，不受其他策略干扰）
# ══════════════════════════════════════════════════════════════════
def run_strategy_solo(strat_name, screen_fn):
    """用独立 10万 资金跑单一策略全程"""
    capital = INITIAL_CAP
    portfolio = []
    all_trades = []
    eq_curve = []

    for di, scan_date in enumerate(bt_dates[:-1]):
        future_dates = [d for d in bt_dates if d > scan_date]
        if not future_dates:
            continue
        buy_date = future_dates[0]
        ms = get_market_state(scan_date)

        # ── 管理持仓 ──
        new_port = []
        for pos in portfolio:
            code = pos['code']
            if code not in stock_dict:
                continue
            sdf = stock_dict[code]
            today_rows = sdf[sdf['date'] == scan_date]
            if today_rows.empty:
                new_port.append(pos)
                continue
            td = today_rows.iloc[0]
            hi = float(td['high']); lo = float(td['low'])
            cl = float(td['close']); op = float(td['open'])
            entry = pos['entry_price']
            pos['day_count'] = pos.get('day_count', 0) + 1
            pos['peak'] = max(pos.get('peak', entry), hi)
            peak = pos['peak']

            trail_stop = peak * (1 - TRAIL_PCT)
            hard_stop_price = entry * (1 + HARD_STOP)
            reason = None
            exit_price = cl

            if lo <= hard_stop_price:
                exit_price = hard_stop_price
                reason = '硬止损-3%'
            elif cl < entry * (1 + SOFT_STOP):
                exit_price = cl
                reason = '软止损-1.5%'
            elif pos['day_count'] >= MAX_DAYS:
                profit_ok = (cl - entry) / entry >= 0.05
                if pos['day_count'] >= 5:
                    exit_price = op
                    reason = f'D{pos["day_count"]}终极强平'
                elif not profit_ok:
                    exit_price = op
                    reason = f'D{pos["day_count"]}强制清仓'
            elif pos.get('tp1_hit') and cl <= trail_stop:
                exit_price = cl
                reason = '移动止盈'
            elif not pos.get('tp1_hit') and hi >= entry * (1 + TP1_PCT):
                exit_price = entry * (1 + TP1_PCT)
                sell_sh = pos['shares'] * TP1_SELL
                pnl = (exit_price - entry) * sell_sh
                capital += exit_price * sell_sh
                all_trades.append({
                    'code': code, 'grade': pos['grade'],
                    'buy_date': pos['buy_date'], 'sell_date': scan_date,
                    'entry': entry, 'exit': exit_price, 'shares': sell_sh,
                    'pnl_pct': (exit_price - entry) / entry * 100,
                    'pnl_yuan': pnl, 'reason': '止盈1(半仓)',
                })
                pos['shares'] -= sell_sh
                pos['tp1_hit'] = True
                new_port.append(pos)
                continue

            if reason:
                pnl = (exit_price - entry) * pos['shares']
                capital += exit_price * pos['shares']
                all_trades.append({
                    'code': code, 'grade': pos['grade'],
                    'buy_date': pos['buy_date'], 'sell_date': scan_date,
                    'entry': entry, 'exit': exit_price, 'shares': pos['shares'],
                    'pnl_pct': (exit_price - entry) / entry * 100,
                    'pnl_yuan': pnl, 'reason': reason,
                })
            else:
                new_port.append(pos)
        portfolio = new_port

        # ── 扫描新候选（当日收盘后）──
        if len(portfolio) >= MAX_POSITIONS:
            mv = _portfolio_mv(portfolio, scan_date)
            eq_curve.append((scan_date, capital + mv))
            continue

        cands = []
        for code, df in stock_dict.items():
            date_rows = df[df['date'] == scan_date]
            if date_rows.empty: continue
            idx = date_rows.index[0]
            if any(p['code'] == code for p in portfolio): continue
            try:
                result = screen_fn(df, idx)
                if result:
                    if ms.get('bearish', False) and result['grade'] == 'B':
                        continue
                    cands.append({**result, 'code': code})
            except Exception:
                continue

        # 同策略内按分数排序，A级优先
        cands.sort(key=lambda c: (0 if c['grade'] == 'A' else 1, -c['score']))

        new_today = 0
        for cand in cands[:5]:
            if len(portfolio) >= MAX_POSITIONS: break
            if new_today >= MAX_NEW_PER_DAY: break
            code = cand['code']
            sdf = stock_dict[code]
            next_rows = sdf[sdf['date'] == buy_date]
            if next_rows.empty: continue
            entry_price = float(next_rows.iloc[0]['open'])
            if entry_price <= 0: continue
            pos_pct = POS_A if cand['grade'] == 'A' else POS_B
            total_eq = capital + sum(p['entry_price'] * p['shares'] for p in portfolio)
            invest = min(total_eq * pos_pct, capital)
            if invest < 5000: continue
            shares = invest / entry_price
            capital -= shares * entry_price
            portfolio.append({
                'code': code, 'grade': cand['grade'], 'score': cand['score'],
                'entry_price': entry_price, 'shares': shares,
                'buy_date': buy_date, 'peak': entry_price,
                'tp1_hit': False, 'day_count': 0,
            })
            new_today += 1

        mv = _portfolio_mv(portfolio, scan_date)
        eq_curve.append((scan_date, capital + mv))

    # 强制平最后持仓
    last_date = bt_dates[-1]
    for pos in portfolio:
        code = pos['code']
        if code not in stock_dict: continue
        rows = stock_dict[code][stock_dict[code]['date'] == last_date]
        exit_price = float(rows.iloc[0]['close']) if not rows.empty else pos['entry_price']
        pnl = (exit_price - pos['entry_price']) * pos['shares']
        capital += exit_price * pos['shares']
        all_trades.append({
            'code': code, 'grade': pos['grade'],
            'buy_date': pos['buy_date'], 'sell_date': last_date,
            'entry': pos['entry_price'], 'exit': exit_price, 'shares': pos['shares'],
            'pnl_pct': (exit_price - pos['entry_price']) / pos['entry_price'] * 100,
            'pnl_yuan': pnl, 'reason': '回测结束强平',
        })
    eq_curve.append((last_date, capital))
    return pd.DataFrame(all_trades), capital, eq_curve


def _portfolio_mv(portfolio, date):
    mv = 0
    for p in portfolio:
        if p['code'] in stock_dict:
            rows = stock_dict[p['code']][stock_dict[p['code']]['date'] == date]
            mv += float(rows.iloc[0]['close']) * p['shares'] if not rows.empty else p['entry_price'] * p['shares']
    return mv


# ══════════════════════════════════════════════════════════════════
# 依次跑4个策略
# ══════════════════════════════════════════════════════════════════
strategies_cfg = [
    ('S1', screen_s1, '洗盘蓄力'),
    ('S2', screen_s2, '大阳横盘'),
    ('S3', screen_s3, '放量突破'),
    ('S4', screen_s4, '高股性反弹'),
]

results = {}
for sname, sfn, sdesc in strategies_cfg:
    print(f"  运行 {sname} {sdesc}...", end='', flush=True)
    tdf, final_cap, eq = run_strategy_solo(sname, sfn)
    ret = (final_cap - INITIAL_CAP) / INITIAL_CAP * 100
    results[sname] = {'desc': sdesc, 'tdf': tdf, 'final': final_cap, 'ret': ret, 'eq': eq}
    print(f" {len(tdf)}笔 {ret:+.2f}%")

print()
print("=" * 70)
print(f"  各策略独立回测结果（各自 10万 本金，{bt_dates[0]} ~ {bt_dates[-1]}）")
print("=" * 70)

# ── 汇总表 ──
print()
print("┌────────┬────────┬──────┬──────┬──────────┬──────────┬──────────┬─────────┐")
print("│ 策略   │  名称  │ 笔数 │ 胜率 │   EV均值 │   总盈亏 │  最大盈  │  最大亏 │")
print("├────────┼────────┼──────┼──────┼──────────┼──────────┼──────────┼─────────┤")

for sname, _, sdesc in strategies_cfg:
    r = results[sname]
    tdf = r['tdf']
    if tdf.empty:
        print(f"│ {sname:<6} │{sdesc[:6]:<8}│  --  │  --  │   --     │   --     │   --     │   --    │")
        continue
    n = len(tdf); wins = tdf[tdf['pnl_pct'] > 0]
    wr = len(wins) / n * 100
    ev = tdf['pnl_pct'].mean()
    tot = tdf['pnl_yuan'].sum()
    mw = tdf['pnl_pct'].max(); ml = tdf['pnl_pct'].min()
    print(f"│ {sname:<6} │{sdesc[:6]:<8}│ {n:>4} │{wr:>5.1f}%│{ev:>+9.2f}%│{tot:>+9,.0f}│{mw:>+9.2f}%│{ml:>+8.2f}%│")

print("└────────┴────────┴──────┴──────┴──────────┴──────────┴──────────┴─────────┘")
print()

# ── 10万本金最终结果对比 ──
print("┌────────────────────────────────────────────────────────────────────┐")
print("│               10万本金 · 3个月结果对比                             │")
print("├────────┬────────┬───────────────┬───────────────┬──────────────────┤")
print("│ 策略   │  名称  │    最终资金   │    绝对盈亏   │   总收益率       │")
print("├────────┼────────┼───────────────┼───────────────┼──────────────────┤")
for sname, _, sdesc in strategies_cfg:
    r = results[sname]
    print(f"│ {sname:<6} │{sdesc[:6]:<8}│ {r['final']:>12,.0f}元│ {r['final']-INITIAL_CAP:>+12,.0f}元│ {r['ret']:>+14.2f}% │")
print("└────────┴────────┴───────────────┴───────────────┴──────────────────┘")

days = len(bt_dates)
print()
print(f"回测区间 : {bt_dates[0]} ~ {bt_dates[-1]}（{days}个交易日）")
print(f"年化估算 :")
for sname, _, sdesc in strategies_cfg:
    rr = results[sname]['ret']
    ann = rr * (252 / days) if days > 0 else 0
    print(f"  {sname} {sdesc}: {ann:>+.2f}%/年")

# ── 各策略出场原因分布 ──
print()
print("── 各策略出场原因 ──")
for sname, _, sdesc in strategies_cfg:
    tdf = results[sname]['tdf']
    if tdf.empty:
        print(f"  {sname}: (无成交)")
        continue
    print(f"  {sname} {sdesc}:")
    rc = tdf['reason'].value_counts()
    for reason, cnt in rc.items():
        avg = tdf[tdf['reason'] == reason]['pnl_pct'].mean()
        print(f"    {reason:<20} {cnt:>4}笔  均盈亏{avg:>+7.2f}%")

# ── 各策略回撤 ──
print()
print("── 各策略最大回撤 ──")
for sname, _, sdesc in strategies_cfg:
    eq = results[sname]['eq']
    if not eq:
        print(f"  {sname}: --")
        continue
    eq_vals = [v for _, v in eq]
    peak = INITIAL_CAP
    mdd = 0
    for v in eq_vals:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak * 100)
    print(f"  {sname} {sdesc}: 最大回撤 {mdd:+.2f}%  峰值 {max(eq_vals):>10,.0f}元  谷底 {min(eq_vals):>10,.0f}元")

# ── 最近成交明细（每策略最多10笔）──
print()
print("── 各策略成交明细（最多10笔）──")
for sname, _, sdesc in strategies_cfg:
    tdf = results[sname]['tdf']
    if tdf.empty:
        print(f"  {sname}: (无成交)")
        continue
    print(f"  {sname} {sdesc}:")
    print(f"  {'代码':<12}{'等级':<4}{'买入日':<12}{'卖出日':<12}{'进价':>8}{'出价':>8}{'涨跌':>9}{'盈亏元':>9}  出场")
    for _, row in tdf.tail(10).iterrows():
        print(f"  {row['code']:<12}{row['grade']:<4}{row['buy_date']:<12}{row['sell_date']:<12}"
              f"{row['entry']:>8.2f}{row['exit']:>8.2f}{row['pnl_pct']:>+8.2f}%{row['pnl_yuan']:>9.1f}  {row['reason']}")
    print()

