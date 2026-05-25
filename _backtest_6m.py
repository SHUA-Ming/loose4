#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测：近半年（2025-10 ~ 2026-04），按选股手册选股 + 短波策略止盈止损
完整复现选股手册第一关~第四关 + 操作手册短波五条铁律
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

# ═══════════════════════════════════════════════
# 获取数据
# ═══════════════════════════════════════════════
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

# 近半年回测区间
BT_START = '2025-10-01'
BT_END   = '2026-04-08'
all_dates = sorted(all_data['date'].unique())
bt_dates = [d for d in all_dates if BT_START <= d <= BT_END]
print(f"回测日期范围: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 个交易日")


# ═══════════════════════════════════════════════
# 选股函数（完全按选股参数手册实现）
# ═══════════════════════════════════════════════
def screen_stock(df, idx):
    """在df的第idx行位置，用往前看的数据判断是否符合选股条件
    返回详细指标值用于后续分析"""
    if idx < 60:
        return None

    data = df.iloc[:idx+1]
    cls = data['close'].values
    ops = data['open'].values
    his = data['high'].values
    los = data['low'].values
    vols = data['volume'].values
    turns = data['turn'].values
    pcts = data['pctChg'].values
    amts = data['amount'].values
    n = len(data)
    last = cls[-1]

    if last < 3 or last > 200:
        return None
    avg_amt_20 = np.mean(amts[-20:]) / 10000
    if avg_amt_20 < 1000:
        return None

    # === 第一关 ===
    ma60 = np.mean(cls[-60:])
    if last <= ma60:
        return None
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60):
        return None
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-35 <= dd60 <= -5):
        return None
    p60 = pcts[-60:]
    if np.sum(p60 >= 9.5) < 1:
        return None
    if np.any(pcts[-5:] < -5):
        return None
    if np.any(turns[-5:] > 8):
        return None

    # === 第二关 ===
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0
    indicator_flags = {}

    # ① 缩量
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    vol_min60 = np.min(vols[-60:])
    floor_vol = vols[-1] <= vol_min60 * 1.2
    sc1 = sum([vr520 <= 0.6, vr560 <= 0.5, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 3: score += 5; indicator_flags['缩量'] = 'A'
    elif sc1 >= 1: score += 2; indicator_flags['缩量'] = 'B'
    else: indicator_flags['缩量'] = 'C'

    # ② 横盘
    c5 = cls[-5:]
    rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    sc2 = sum([rng5 <= 5, abs(cs) <= 1, last > ma60])
    if sc2 >= 3: score += 4; indicator_flags['横盘'] = 'A'
    elif sc2 >= 2: score += 2; indicator_flags['横盘'] = 'B'
    else: indicator_flags['横盘'] = 'C'

    # ③ 均线粘合
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, last > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3: score += 4; indicator_flags['均线'] = 'A'
    elif sc3 >= 2: score += 2; indicator_flags['均线'] = 'B'
    else: indicator_flags['均线'] = 'C'

    # ④ 实体缩小
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 2: score += 3; indicator_flags['实体'] = 'A'
    elif sc4 >= 1: score += 1; indicator_flags['实体'] = 'B'
    else: indicator_flags['实体'] = 'C'

    # ⑤ 下影线
    lsb = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); ls_len = min(o, c) - l
        if c > o and body > 0 and ls_len >= 2 * body and pcts[i] <= 2:
            lsb += 1
    if lsb >= 1: score += 3; indicator_flags['下影'] = 'A'
    else: indicator_flags['下影'] = 'C'

    # ⑥ 十字星
    doji = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); bp = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if bp <= 0.5 and body > 0 and shadow >= 2 * body:
            doji += 1
    if doji >= 2: score += 2; indicator_flags['十字'] = 'A'
    elif doji >= 1: score += 1; indicator_flags['十字'] = 'B'
    else: indicator_flags['十字'] = 'C'

    # ⑦ 红绿交替
    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3: score += 2; indicator_flags['交替'] = 'A'
    else: indicator_flags['交替'] = 'C'

    grade = 'A' if score >= 18 else 'B' if score >= 12 else 'C'
    if grade in ('A', 'B'):
        return {
            'score': score, 'grade': grade, 'price': last,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'vr520': vr520, 'vr560': vr560, 'turn5': turn5,
            'rng5': rng5, 'cs': cs, 'ma_sp': ma_sp, 'dd60': dd60, 'pct60': pct60,
            'pct_abs5': pct_abs5,
            'indicators': indicator_flags,
        }
    return None


# ═══════════════════════════════════════════════
# 短波回测
# ═══════════════════════════════════════════════
print()
print("=" * 80)
print(f"  开始回测: {BT_START} ~ {BT_END}  选股手册 + 短波策略")
print("=" * 80)
print()

trades = []
MAX_NEW_PER_DAY = 3

for di, scan_date in enumerate(bt_dates[:-3]):
    future_dates = [d for d in bt_dates if d > scan_date]
    if len(future_dates) < 3:
        continue

    buy_date = future_dates[0]
    hold_date = future_dates[1]
    exit_date = future_dates[2]

    selected = []
    for code, df in stock_dict.items():
        date_list = df['date'].values.tolist()
        if scan_date not in date_list:
            continue
        idx = date_list.index(scan_date)
        result = screen_stock(df, idx)
        if result:
            result['code'] = code
            selected.append(result)

    selected.sort(key=lambda x: x['score'], reverse=True)
    selected = selected[:MAX_NEW_PER_DAY]

    for s in selected:
        code = s['code']
        df = stock_dict[code]
        date_list = df['date'].values.tolist()
        if buy_date not in date_list or hold_date not in date_list or exit_date not in date_list:
            continue

        buy_idx = date_list.index(buy_date)
        hold_idx = date_list.index(hold_date)
        exit_idx = date_list.index(exit_date)

        buy_price = df.iloc[buy_idx]['open']
        if buy_price <= 0 or np.isnan(buy_price):
            continue

        d1_high = df.iloc[buy_idx]['high']
        d1_low = df.iloc[buy_idx]['low']
        d1_close = df.iloc[buy_idx]['close']
        d2_high = df.iloc[hold_idx]['high']
        d2_low = df.iloc[hold_idx]['low']
        d2_close = df.iloc[hold_idx]['close']
        d3_open = df.iloc[exit_idx]['open']

        tp1_price = buy_price * 1.03
        tp2_price = buy_price * 1.05
        sl_price = buy_price * 0.98

        remaining_pct = 1.0
        total_return = 0.0
        exit_reason = ""
        actual_exit_date = ""

        # D1
        if d1_low <= sl_price:
            total_return += remaining_pct * ((sl_price - buy_price) / buy_price * 100)
            remaining_pct = 0
            exit_reason = "D1止损-2%"
            actual_exit_date = buy_date
        else:
            if d1_high >= tp2_price:
                half = remaining_pct / 2
                total_return += half * 3.0 + half * 5.0
                remaining_pct = 0
                exit_reason = "D1止盈+5%"
                actual_exit_date = buy_date
            elif d1_high >= tp1_price:
                half = remaining_pct / 2
                total_return += half * 3.0
                remaining_pct -= half

        # D2
        if remaining_pct > 0:
            if d2_low <= sl_price:
                total_return += remaining_pct * ((sl_price - buy_price) / buy_price * 100)
                remaining_pct = 0
                exit_reason = ("D2止损" if not exit_reason else exit_reason + "+D2止损")
                actual_exit_date = hold_date
            else:
                if d2_high >= tp2_price:
                    if remaining_pct == 1.0:
                        total_return += 0.5 * 3.0 + 0.5 * 5.0
                    else:
                        total_return += remaining_pct * 5.0
                    remaining_pct = 0
                    exit_reason = ("D2止盈+5%" if not exit_reason else exit_reason + "+D2止盈+5%")
                    actual_exit_date = hold_date
                elif d2_high >= tp1_price and remaining_pct == 1.0:
                    total_return += 0.5 * 3.0
                    remaining_pct = 0.5
                    exit_reason = "D2半仓+3%"

        # D3 强制清仓
        if remaining_pct > 0:
            d3_ret = (d3_open - buy_price) / buy_price * 100
            total_return += remaining_pct * d3_ret
            exit_reason = (exit_reason + "+D3清仓" if exit_reason else "D3强制清仓")
            actual_exit_date = exit_date
            remaining_pct = 0

        trades.append({
            'scan_date': scan_date,
            'buy_date': buy_date,
            'exit_date': actual_exit_date or exit_date,
            'code': code,
            'grade': s['grade'],
            'score': s['score'],
            'buy_price': buy_price,
            'return_pct': total_return,
            'exit_reason': exit_reason,
            'vr520': s['vr520'],
            'turn5': s['turn5'],
            'rng5': s['rng5'],
            'ma_sp': s['ma_sp'],
            'dd60': s['dd60'],
            'pct60': s['pct60'],
            'pct_abs5': s['pct_abs5'],
            'indicators': s['indicators'],
        })

    if (di + 1) % 20 == 0:
        print(f"  扫描进度: {di+1}/{len(bt_dates)} 天，已产生 {len(trades)} 笔交易")
        sys.stdout.flush()

# ═══════════════════════════════════════════════
# 统计结果
# ═══════════════════════════════════════════════
print(f"\n总交易笔数: {len(trades)}\n")

if not trades:
    print("没有产生交易")
    sys.exit(0)

returns = [t['return_pct'] for t in trades]
wins = [r for r in returns if r > 0]
losses = [r for r in returns if r < 0]
evens = [r for r in returns if r == 0]

total_return = sum(returns)
avg_return = np.mean(returns)
win_rate = len(wins) / len(returns) * 100
avg_win = np.mean(wins) if wins else 0
avg_loss = np.mean(losses) if losses else 0
max_win = max(returns)
max_loss = min(returns)
profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float('inf')

print("=" * 80)
print(f"  回测结果汇总: {BT_START} ~ {BT_END}")
print("=" * 80)
print()
print(f"  总交易笔数:  {len(trades)}")
print(f"  盈利笔数:    {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
print(f"  亏损笔数:    {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
print(f"  平局笔数:    {len(evens)}")
print()
print(f"  总收益率:    {total_return:+.2f}%")
print(f"  平均每笔:    {avg_return:+.3f}%")
print(f"  平均盈利:    {avg_win:+.3f}%")
print(f"  平均亏损:    {avg_loss:+.3f}%")
print(f"  盈亏比:      {abs(avg_win/avg_loss):.2f}:1" if avg_loss != 0 else "  盈亏比: ∞")
print(f"  利润因子:    {profit_factor:.2f}" if profit_factor != float('inf') else "  利润因子: ∞")
print(f"  最大单笔盈:  {max_win:+.2f}%")
print(f"  最大单笔亏:  {max_loss:+.2f}%")
print()

# === 按月统计 ===
print("─" * 70)
print("  按月统计")
print("─" * 70)
months = sorted(set(t['buy_date'][:7] for t in trades))
for m in months:
    mt = [t for t in trades if t['buy_date'].startswith(m)]
    if mt:
        mr = [t['return_pct'] for t in mt]
        mw = [r for r in mr if r > 0]
        ml = [r for r in mr if r < 0]
        wr = len(mw)/len(mt)*100
        avg_w = np.mean(mw) if mw else 0
        avg_l = np.mean(ml) if ml else 0
        pf_ratio = abs(avg_w/avg_l) if avg_l != 0 else 999
        print(f"  {m}: {len(mt):>3d}笔 | 胜率{wr:>5.1f}% | 总收益{sum(mr):>+8.2f}% | 均{np.mean(mr):>+7.3f}% | 盈亏比{pf_ratio:.2f}")

# === 按出场方式 ===
print()
print("─" * 70)
print("  按出场方式统计")
print("─" * 70)
exit_stats = {}
for t in trades:
    key = t['exit_reason']
    if key not in exit_stats:
        exit_stats[key] = {'count': 0, 'total_ret': 0, 'rets': []}
    exit_stats[key]['count'] += 1
    exit_stats[key]['total_ret'] += t['return_pct']
    exit_stats[key]['rets'].append(t['return_pct'])

for reason, stats in sorted(exit_stats.items(), key=lambda x: -x[1]['count']):
    cnt = stats['count']
    ret = stats['total_ret']
    wr = len([r for r in stats['rets'] if r > 0]) / cnt * 100
    print(f"  {reason:<30s}: {cnt:>4d}笔 | 胜率{wr:>5.1f}% | 总收益{ret:>+8.2f}% | 均{ret/cnt:>+7.3f}%")

# === 按评级 ===
print()
print("─" * 70)
print("  按评级统计")
print("─" * 70)
for g in ['A', 'B']:
    gt = [t for t in trades if t['grade'] == g]
    if gt:
        gr = [t['return_pct'] for t in gt]
        gw = [r for r in gr if r > 0]
        gl = [r for r in gr if r < 0]
        avg_gw = np.mean(gw) if gw else 0
        avg_gl = np.mean(gl) if gl else 0
        ratio = abs(avg_gw/avg_gl) if avg_gl != 0 else 999
        print(f"  {g}级: {len(gt):>3d}笔 | 胜率{len(gw)/len(gt)*100:>5.1f}% | 总收益{sum(gr):>+8.2f}% | 均{np.mean(gr):>+7.3f}% | 盈亏比{ratio:.2f}")

# === 按评分区间 ===
print()
print("─" * 70)
print("  按评分区间统计")
print("─" * 70)
for low, high, label in [(18,23,'18-23(A级)'), (15,17,'15-17(B高)'), (12,14,'12-14(B低)')]:
    st = [t for t in trades if low <= t['score'] <= high]
    if st:
        sr = [t['return_pct'] for t in st]
        sw = [r for r in sr if r > 0]
        sl_list = [r for r in sr if r < 0]
        avg_sw = np.mean(sw) if sw else 0
        avg_sl2 = np.mean(sl_list) if sl_list else 0
        ratio = abs(avg_sw/avg_sl2) if avg_sl2 != 0 else 999
        print(f"  {label:>12s}: {len(st):>3d}笔 | 胜率{len(sw)/len(st)*100:>5.1f}% | 总收益{sum(sr):>+8.2f}% | 均{np.mean(sr):>+7.3f}% | 盈亏比{ratio:.2f}")

# === 关键指标对胜率的影响 ===
print()
print("─" * 70)
print("  各核心指标对胜率的影响（该指标达标A vs 不达标C）")
print("─" * 70)
for ind_name in ['缩量', '横盘', '均线', '实体', '下影', '十字', '交替']:
    a_trades = [t for t in trades if t['indicators'].get(ind_name) == 'A']
    c_trades = [t for t in trades if t['indicators'].get(ind_name) == 'C']
    if a_trades:
        a_rets = [t['return_pct'] for t in a_trades]
        a_wr = len([r for r in a_rets if r > 0]) / len(a_rets) * 100
        a_avg = np.mean(a_rets)
    else:
        a_wr = 0; a_avg = 0
    if c_trades:
        c_rets = [t['return_pct'] for t in c_trades]
        c_wr = len([r for r in c_rets if r > 0]) / len(c_rets) * 100
        c_avg = np.mean(c_rets)
    else:
        c_wr = 0; c_avg = 0
    diff = a_avg - c_avg
    print(f"  {ind_name}: 达标{len(a_trades):>4d}笔 胜率{a_wr:>5.1f}% 均收益{a_avg:>+6.3f}% | 未达标{len(c_trades):>4d}笔 胜率{c_wr:>5.1f}% 均收益{c_avg:>+6.3f}% | 差异{diff:>+6.3f}%")

# === 缩量程度 vs 胜率 ===
print()
print("─" * 70)
print("  量比(5/20)分段 vs 胜率")
print("─" * 70)
for low_v, high_v, label in [(0, 0.4, '<0.4极缩量'), (0.4, 0.6, '0.4-0.6缩量'), (0.6, 0.8, '0.6-0.8正常'), (0.8, 99, '>0.8放量')]:
    vt = [t for t in trades if low_v <= t['vr520'] < high_v]
    if vt:
        vr = [t['return_pct'] for t in vt]
        vw = [r for r in vr if r > 0]
        print(f"  {label:>12s}: {len(vt):>3d}笔 | 胜率{len(vw)/len(vt)*100:>5.1f}% | 均{np.mean(vr):>+7.3f}%")

# === 5日波幅 vs 胜率 ===
print()
print("─" * 70)
print("  5日波幅分段 vs 胜率")
print("─" * 70)
for low_v, high_v, label in [(0, 3, '<3%极窄'), (3, 5, '3-5%正常'), (5, 8, '5-8%偏宽'), (8, 99, '>8%很宽')]:
    rt = [t for t in trades if low_v <= t['rng5'] < high_v]
    if rt:
        rr = [t['return_pct'] for t in rt]
        rw = [r for r in rr if r > 0]
        print(f"  {label:>12s}: {len(rt):>3d}笔 | 胜率{len(rw)/len(rt)*100:>5.1f}% | 均{np.mean(rr):>+7.3f}%")

# === 高点回撤 vs 胜率 ===
print()
print("─" * 70)
print("  高点回撤分段 vs 胜率")
print("─" * 70)
for low_v, high_v, label in [(-35, -25, '-35~-25%深回撤'), (-25, -15, '-25~-15%中回撤'), (-15, -5, '-15~-5%浅回撤')]:
    dt = [t for t in trades if low_v <= t['dd60'] < high_v]
    if dt:
        dr = [t['return_pct'] for t in dt]
        dw = [r for r in dr if r > 0]
        print(f"  {label:>16s}: {len(dt):>3d}笔 | 胜率{len(dw)/len(dt)*100:>5.1f}% | 均{np.mean(dr):>+7.3f}%")

# === 连续亏损分析 ===
print()
print("─" * 70)
print("  最大连续亏损/连续盈利")
print("─" * 70)
sorted_trades = sorted(trades, key=lambda x: x['buy_date'])
max_consec_loss = 0; max_consec_win = 0
cur_loss = 0; cur_win = 0
max_drawdown_trades = []; cur_dd = 0
for t in sorted_trades:
    if t['return_pct'] < 0:
        cur_loss += 1; cur_win = 0
    elif t['return_pct'] > 0:
        cur_win += 1; cur_loss = 0
    else:
        cur_loss = 0; cur_win = 0
    max_consec_loss = max(max_consec_loss, cur_loss)
    max_consec_win = max(max_consec_win, cur_win)

cum = 0; peak = 0; max_dd = 0
for t in sorted_trades:
    cum += t['return_pct']
    peak = max(peak, cum)
    dd = cum - peak
    if dd < max_dd:
        max_dd = dd

print(f"  最大连续亏损笔数: {max_consec_loss}")
print(f"  最大连续盈利笔数: {max_consec_win}")
print(f"  最大回撤: {max_dd:.2f}%")

# === 累计收益曲线 ===
print()
print("─" * 70)
print("  月度累计收益变化")
print("─" * 70)
cum = 0
month_data = {}
for t in sorted_trades:
    cum += t['return_pct']
    m = t['buy_date'][:7]
    month_data[m] = cum

for m, c in month_data.items():
    bars = int(abs(c) / 3)
    bar = "█" * bars if c >= 0 else "▒" * bars
    print(f"  {m}: {c:>+8.2f}% {bar}")

# === 假设资金测算 ===
print()
capital_per_trade = 100000
total_profit = sum(r / 100 * capital_per_trade for r in returns)
print(f"═══ 如果每笔投入10万元 ═══")
print(f"  总盈亏金额: {total_profit:>+,.0f} 元")
print(f"  平均每笔:   {total_profit/len(trades):>+,.0f} 元")
print(f"  最大单笔盈: {max_win/100*capital_per_trade:>+,.0f} 元 ({max_win:+.2f}%)")
print(f"  最大单笔亏: {max_loss/100*capital_per_trade:>+,.0f} 元 ({max_loss:+.2f}%)")
print(f"  月均交易:   {len(trades)/6:.0f} 笔")
print(f"  月均收益:   {total_profit/6:>+,.0f} 元")
