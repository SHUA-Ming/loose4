#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测：近1个月（2026-03-10 ~ 2026-04-08），使用优化后的新规则
对比新旧规则在同一区间的表现
更新内容：
  - F4回撤: 5%~20% (原15%~30%)
  - ①缩量: 3分(原5分), 量比0.4~0.8(原≤0.6)
  - ⑤下影线: 2分(原3分)
  - 评级: A≥16, B≥10 (原A≥18, B≥12)
  - 满分: 20分(原23分)
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

BT_START = '2026-03-10'
BT_END   = '2026-04-08'
all_dates = sorted(all_data['date'].unique())
bt_dates = [d for d in all_dates if BT_START <= d <= BT_END]
print(f"回测日期范围: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 个交易日")


# ═══════════════════════════════════════════════
# 选股函数（新旧两套规则）
# ═══════════════════════════════════════════════
def screen_stock_old(df, idx):
    """旧规则: 缩量5分, 下影3分, 满分23, A≥18/B≥12, 回撤-35~-5%"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values; ops = data['open'].values
    his = data['high'].values; los = data['low'].values
    vols = data['volume'].values; turns = data['turn'].values
    pcts = data['pctChg'].values; amts = data['amount'].values
    n = len(data); last = cls[-1]

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-35 <= dd60 <= -5): return None  # 旧: 宽回撤
    if np.sum(pcts[-60:] >= 9.5) < 1: return None
    if np.any(pcts[-5:] < -5): return None
    if np.any(turns[-5:] > 8): return None

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0; indicators = {}

    # ① 缩量 5分（旧）
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:]) * 1.2
    sc1 = sum([vr520 <= 0.6, vr560 <= 0.5, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 3: score += 5; indicators['缩量'] = 'A'
    elif sc1 >= 1: score += 2; indicators['缩量'] = 'B'
    else: indicators['缩量'] = 'C'

    # ② 横盘 4分
    rng5 = (np.max(cls[-5:]) - np.min(cls[-5:])) / np.mean(cls[-5:]) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    sc2 = sum([rng5 <= 5, abs(cs) <= 1, last > ma60])
    if sc2 >= 3: score += 4; indicators['横盘'] = 'A'
    elif sc2 >= 2: score += 2; indicators['横盘'] = 'B'
    else: indicators['横盘'] = 'C'

    # ③ 均线 4分
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, last > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3: score += 4; indicators['均线'] = 'A'
    elif sc3 >= 2: score += 2; indicators['均线'] = 'B'
    else: indicators['均线'] = 'C'

    # ④ 实体 3分
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 2: score += 3; indicators['实体'] = 'A'
    elif sc4 >= 1: score += 1; indicators['实体'] = 'B'
    else: indicators['实体'] = 'C'

    # ⑤ 下影线 3分（旧）
    lsb = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); ls_len = min(o, c) - l
        if c > o and body > 0 and ls_len >= 2 * body and pcts[i] <= 2: lsb += 1
    if lsb >= 1: score += 3; indicators['下影'] = 'A'
    else: indicators['下影'] = 'C'

    # ⑥ 十字 2分
    doji = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); bp = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if bp <= 0.5 and body > 0 and shadow >= 2 * body: doji += 1
    if doji >= 2: score += 2; indicators['十字'] = 'A'
    elif doji >= 1: score += 1; indicators['十字'] = 'B'
    else: indicators['十字'] = 'C'

    # ⑦ 交替 2分
    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3: score += 2; indicators['交替'] = 'A'
    else: indicators['交替'] = 'C'

    grade = 'A' if score >= 18 else 'B' if score >= 12 else 'C'
    if grade in ('A', 'B'):
        return {'score': score, 'grade': grade, 'price': last, 'dd60': dd60,
                'vr520': vr520, 'rng5': rng5, 'indicators': indicators}
    return None


def screen_stock_new(df, idx):
    """新规则: 缩量3分, 下影2分, 满分20, A≥16/B≥10, 回撤-20~-5%, 量比0.4~0.8"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values; ops = data['open'].values
    his = data['high'].values; los = data['low'].values
    vols = data['volume'].values; turns = data['turn'].values
    pcts = data['pctChg'].values; amts = data['amount'].values
    n = len(data); last = cls[-1]

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): return None  # 新: 浅回撤
    if np.sum(pcts[-60:] >= 9.5) < 1: return None
    if np.any(pcts[-5:] < -5): return None
    if np.any(turns[-5:] > 8): return None

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0; indicators = {}

    # ① 缩量 3分（新: 量比0.4~0.8）
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:]) * 1.2
    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 3: score += 3; indicators['缩量'] = 'A'
    elif sc1 >= 1: score += 1; indicators['缩量'] = 'B'
    else: indicators['缩量'] = 'C'

    # ② 横盘 4分
    rng5 = (np.max(cls[-5:]) - np.min(cls[-5:])) / np.mean(cls[-5:]) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    sc2 = sum([rng5 <= 5, abs(cs) <= 1, last > ma60])
    if sc2 >= 3: score += 4; indicators['横盘'] = 'A'
    elif sc2 >= 2: score += 2; indicators['横盘'] = 'B'
    else: indicators['横盘'] = 'C'

    # ③ 均线 4分
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, last > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3: score += 4; indicators['均线'] = 'A'
    elif sc3 >= 2: score += 2; indicators['均线'] = 'B'
    else: indicators['均线'] = 'C'

    # ④ 实体 3分
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 2: score += 3; indicators['实体'] = 'A'
    elif sc4 >= 1: score += 1; indicators['实体'] = 'B'
    else: indicators['实体'] = 'C'

    # ⑤ 下影线 2分（新: 降权）
    lsb = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); ls_len = min(o, c) - l
        if c > o and body > 0 and ls_len >= 2 * body and pcts[i] <= 2: lsb += 1
    if lsb >= 1: score += 2; indicators['下影'] = 'A'
    else: indicators['下影'] = 'C'

    # ⑥ 十字 2分
    doji = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o); bp = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if bp <= 0.5 and body > 0 and shadow >= 2 * body: doji += 1
    if doji >= 2: score += 2; indicators['十字'] = 'A'
    elif doji >= 1: score += 1; indicators['十字'] = 'B'
    else: indicators['十字'] = 'C'

    # ⑦ 交替 2分
    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3: score += 2; indicators['交替'] = 'A'
    else: indicators['交替'] = 'C'

    grade = 'A' if score >= 16 else 'B' if score >= 10 else 'C'
    if grade in ('A', 'B'):
        return {'score': score, 'grade': grade, 'price': last, 'dd60': dd60,
                'vr520': vr520, 'rng5': rng5, 'indicators': indicators}
    return None


# ═══════════════════════════════════════════════
# 交易模拟
# ═══════════════════════════════════════════════
def simulate_trades(screen_func, label):
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
            result = screen_func(df, idx)
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
            d2_high = df.iloc[hold_idx]['high']
            d2_low = df.iloc[hold_idx]['low']
            d3_open = df.iloc[exit_idx]['open']

            tp1_price = buy_price * 1.03
            tp2_price = buy_price * 1.05
            sl_price = buy_price * 0.98

            remaining_pct = 1.0
            total_return = 0.0
            exit_reason = ""

            # D1
            if d1_low <= sl_price:
                total_return += remaining_pct * (-2.0)
                remaining_pct = 0
                exit_reason = "D1止损-2%"
            else:
                if d1_high >= tp2_price:
                    total_return += 0.5 * 3.0 + 0.5 * 5.0
                    remaining_pct = 0
                    exit_reason = "D1止盈+5%"
                elif d1_high >= tp1_price:
                    total_return += 0.5 * 3.0
                    remaining_pct = 0.5

            # D2
            if remaining_pct > 0:
                if d2_low <= sl_price:
                    total_return += remaining_pct * ((sl_price - buy_price) / buy_price * 100)
                    remaining_pct = 0
                    exit_reason = ("D2止损" if not exit_reason else exit_reason + "+D2止损")
                else:
                    if d2_high >= tp2_price:
                        if remaining_pct == 1.0:
                            total_return += 0.5 * 3.0 + 0.5 * 5.0
                        else:
                            total_return += remaining_pct * 5.0
                        remaining_pct = 0
                        exit_reason = ("D2止盈+5%" if not exit_reason else exit_reason + "+D2止盈+5%")
                    elif d2_high >= tp1_price and remaining_pct == 1.0:
                        total_return += 0.5 * 3.0
                        remaining_pct = 0.5
                        exit_reason = "D2半仓+3%"

            # D3
            if remaining_pct > 0:
                d3_ret = (d3_open - buy_price) / buy_price * 100
                total_return += remaining_pct * d3_ret
                exit_reason = (exit_reason + "+D3清仓" if exit_reason else "D3强制清仓")
                remaining_pct = 0

            trades.append({
                'buy_date': buy_date,
                'code': code,
                'grade': s['grade'],
                'score': s['score'],
                'buy_price': buy_price,
                'return_pct': total_return,
                'exit_reason': exit_reason,
                'vr520': s['vr520'],
                'dd60': s['dd60'],
            })

    return trades


# ═══════════════════════════════════════════════
# 运行两套规则
# ═══════════════════════════════════════════════
print()
print("=" * 80)
print(f"  近一个月回测对比: {BT_START} ~ {BT_END}")
print(f"  旧规则 vs 新规则（优化后）")
print("=" * 80)
print()

print("  [1/2] 运行旧规则...")
trades_old = simulate_trades(screen_stock_old, "旧规则")
print(f"        旧规则产生 {len(trades_old)} 笔交易")

print("  [2/2] 运行新规则...")
trades_new = simulate_trades(screen_stock_new, "新规则")
print(f"        新规则产生 {len(trades_new)} 笔交易")


def print_stats(trades, label):
    if not trades:
        print(f"\n  {label}: 没有产生交易")
        return

    returns = [t['return_pct'] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    total_return = sum(returns)
    avg_return = np.mean(returns)
    win_rate = len(wins) / len(returns) * 100
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    max_win = max(returns)
    max_loss = min(returns)
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float('inf')

    print(f"\n{'─' * 70}")
    print(f"  {label}")
    print(f"{'─' * 70}")
    print(f"  总交易笔数:  {len(trades)}")
    print(f"  盈利笔数:    {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
    print(f"  亏损笔数:    {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
    print(f"  总收益率:    {total_return:+.2f}%")
    print(f"  平均每笔:    {avg_return:+.3f}%")
    print(f"  平均盈利:    {avg_win:+.3f}%")
    print(f"  平均亏损:    {avg_loss:+.3f}%")
    print(f"  盈亏比:      {abs(avg_win/avg_loss):.2f}:1" if avg_loss != 0 else "  盈亏比: ∞")
    print(f"  利润因子:    {pf:.2f}" if pf != float('inf') else "  利润因子: ∞")
    print(f"  最大单笔盈:  {max_win:+.2f}%")
    print(f"  最大单笔亏:  {max_loss:+.2f}%")

    # 按评级
    print(f"\n  按评级:")
    for g in ['A', 'B']:
        gt = [t for t in trades if t['grade'] == g]
        if gt:
            gr = [t['return_pct'] for t in gt]
            gw = [r for r in gr if r > 0]
            gl = [r for r in gr if r < 0]
            avg_gw = np.mean(gw) if gw else 0
            avg_gl = np.mean(gl) if gl else 0
            ratio = abs(avg_gw/avg_gl) if avg_gl != 0 else 999
            print(f"    {g}级: {len(gt):>3d}笔 | 胜率{len(gw)/len(gt)*100:>5.1f}% | 总收益{sum(gr):>+8.2f}% | 盈亏比{ratio:.2f}")

    # 按出场方式
    print(f"\n  按出场方式:")
    exit_stats = {}
    for t in trades:
        key = t['exit_reason']
        if key not in exit_stats:
            exit_stats[key] = {'count': 0, 'rets': []}
        exit_stats[key]['count'] += 1
        exit_stats[key]['rets'].append(t['return_pct'])
    for reason, stats in sorted(exit_stats.items(), key=lambda x: -x[1]['count']):
        cnt = stats['count']
        rets = stats['rets']
        wr = len([r for r in rets if r > 0]) / cnt * 100
        print(f"    {reason:<28s}: {cnt:>3d}笔 | 胜率{wr:>5.1f}% | 总收益{sum(rets):>+7.2f}% | 均{np.mean(rets):>+6.3f}%")

    # 回撤分段
    print(f"\n  回撤分段:")
    for low_v, high_v, lbl in [(-35, -20, '-35~-20%深'), (-20, -10, '-20~-10%中'), (-10, -5, '-10~-5%浅')]:
        dt = [t for t in trades if low_v <= t['dd60'] < high_v]
        if dt:
            dr = [t['return_pct'] for t in dt]
            dw = [r for r in dr if r > 0]
            print(f"    {lbl:>12s}: {len(dt):>3d}笔 | 胜率{len(dw)/len(dt)*100:>5.1f}% | 均{np.mean(dr):>+7.3f}%")

    # 量比分段
    print(f"\n  量比(5/20)分段:")
    for low_v, high_v, lbl in [(0.0, 0.4, '<0.4极缩'), (0.4, 0.6, '0.4-0.6'), (0.6, 0.8, '0.6-0.8'), (0.8, 99, '>0.8放量')]:
        vt = [t for t in trades if low_v <= t['vr520'] < high_v]
        if vt:
            vr = [t['return_pct'] for t in vt]
            vw = [r for r in vr if r > 0]
            print(f"    {lbl:>10s}: {len(vt):>3d}笔 | 胜率{len(vw)/len(vt)*100:>5.1f}% | 均{np.mean(vr):>+7.3f}%")

    # 资金模拟
    cap = 100000
    total_p = sum(r / 100 * cap for r in returns)
    print(f"\n  如果每笔10万: 总盈亏{total_p:>+,.0f}元 | 均{total_p/len(trades):>+,.0f}元/笔")


print_stats(trades_old, "旧规则（缩量5分, 回撤-35~-5%, A≥18/B≥12, 满分23）")
print_stats(trades_new, "新规则（缩量3分, 回撤-20~-5%, A≥16/B≥10, 满分20）")

# ═══ 对比汇总 ═══
print()
print("=" * 80)
print("  新旧规则对比汇总")
print("=" * 80)
print()

def summary_line(trades):
    if not trades:
        return "无交易"
    rets = [t['return_pct'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    avg_w = np.mean(wins) if wins else 0
    avg_l = np.mean(losses) if losses else 0
    wr = len(wins)/len(rets)*100
    ratio = abs(avg_w/avg_l) if avg_l != 0 else 999
    return f"{len(trades):>3d}笔 | 胜率{wr:>5.1f}% | 总收益{sum(rets):>+8.2f}% | 均{np.mean(rets):>+7.3f}% | 盈亏比{ratio:.2f}"

print(f"  旧规则: {summary_line(trades_old)}")
print(f"  新规则: {summary_line(trades_new)}")

# 只看B级的对比
print()
old_b = [t for t in trades_old if t['grade'] == 'B']
new_b = [t for t in trades_new if t['grade'] == 'B']
print(f"  旧B级:  {summary_line(old_b)}")
print(f"  新B级:  {summary_line(new_b)}")

old_a = [t for t in trades_old if t['grade'] == 'A']
new_a = [t for t in trades_new if t['grade'] == 'A']
print(f"  旧A级:  {summary_line(old_a)}")
print(f"  新A级:  {summary_line(new_a)}")

# 新规则独有的交易（旧规则选不出来）
new_codes_dates = set((t['buy_date'], t['code']) for t in trades_new)
old_codes_dates = set((t['buy_date'], t['code']) for t in trades_old)
only_new = [t for t in trades_new if (t['buy_date'], t['code']) not in old_codes_dates]
only_old = [t for t in trades_old if (t['buy_date'], t['code']) not in new_codes_dates]

print()
print(f"  新规则独有交易: {len(only_new)}笔  {summary_line(only_new) if only_new else '无'}")
print(f"  旧规则独有交易: {len(only_old)}笔  {summary_line(only_old) if only_old else '无'}")
