#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
终极回测：6个月数据，对比 旧规则 vs 新规则V2（顶级优化版）
覆盖 2025-10-01 ~ 2026-04-08

V2优化点（基于500+笔回测数据总结）：
  1. 大盘过滤器：全市场MA5<MA10<MA20时禁止开仓（空头排列占20%但贡献大部分亏损）
  2. A级门槛拉高到≥18（满分20），让大部分交易落在B级（胜率更高的区间）
  3. D1止损改为收盘价判断：盘中触-2%不panic sell，收盘低于-1.5%才止损
     但盘中触-3%硬止损（防极端）
  4. 选股优先级：B级优先排序，不再按分数从高到低
  5. 每日最多2笔新仓（原3笔），更精选
  6. F4回撤5%-20%（已验证）
  7. 缩量3分+量比0.4-0.8（已验证）
  8. 下影线2分（已验证）
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

# 构造市场均价作为大盘代理（因无指数数据）
market_daily = all_data.groupby('date').agg(
    mkt_close=('close', 'mean'),
    mkt_pct=('pctChg', 'mean'),
).reset_index().sort_values('date').reset_index(drop=True)
mkt_cls = market_daily['mkt_close'].values
mkt_dates = market_daily['date'].values

# 预计算每个日期的大盘状态
market_state = {}
for i in range(20, len(market_daily)):
    ma5 = np.mean(mkt_cls[i-4:i+1])
    ma10 = np.mean(mkt_cls[i-9:i+1])
    ma20 = np.mean(mkt_cls[i-19:i+1])
    pct5 = (mkt_cls[i] - mkt_cls[i-5]) / mkt_cls[i-5] * 100 if mkt_cls[i-5] > 0 else 0
    bearish = ma5 < ma10 < ma20
    ma5_lt_ma10 = ma5 < ma10
    market_state[mkt_dates[i]] = {
        'bearish': bearish,       # 空头排列
        'ma5_lt_ma10': ma5_lt_ma10,  # MA5拐头向下
        'pct5': pct5,
    }

stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df) >= 60:
        stock_dict[code] = df

print(f"有效股票数: {len(stock_dict)}")

BT_START = '2025-10-01'
BT_END   = '2026-04-08'
all_dates_list = sorted(all_data['date'].unique())
bt_dates = [d for d in all_dates_list if BT_START <= d <= BT_END]
print(f"回测范围: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 天")


# ════════════════════════════════════════════
# 选股函数
# ════════════════════════════════════════════
def _common_prefilter(df, idx, dd_range):
    """公共前置过滤，dd_range = (min, max) 如 (-35, -5)"""
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
    if not (dd_range[0] <= dd60 <= dd_range[1]): return None
    if np.sum(pcts[-60:] >= 9.5) < 1: return None
    if np.any(pcts[-5:] < -5): return None
    if np.any(turns[-5:] > 8): return None

    return {
        'cls': cls, 'ops': ops, 'his': his, 'los': los,
        'vols': vols, 'turns': turns, 'pcts': pcts,
        'n': n, 'last': last, 'ma60': ma60, 'dd60': dd60, 'pct60': pct60,
    }


def screen_old(df, idx):
    """旧规则：满分23, 缩量5, 下影3, A≥18/B≥12, 回撤-35~-5"""
    pre = _common_prefilter(df, idx, (-35, -5))
    if pre is None: return None
    cls, ops, his, los = pre['cls'], pre['ops'], pre['his'], pre['los']
    vols, turns, pcts = pre['vols'], pre['turns'], pre['pcts']
    n, last, ma60 = pre['n'], pre['last'], pre['ma60']
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])

    score = 0
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5/vol20 if vol20>0 else 999; vr560 = vol5/vol60 if vol60>0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = vols[-1]<vols[-2]<vols[-3] if n>=3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:])*1.2
    sc1 = sum([vr520<=0.6, vr560<=0.5, turn5<=2, vol_dec, floor_vol])
    if sc1>=3: score+=5
    elif sc1>=1: score+=2

    rng5 = (np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
    cs = (np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
    sc2 = sum([rng5<=5, abs(cs)<=1, last>ma60])
    if sc2>=3: score+=4
    elif sc2>=2: score+=2

    ma_sp = (max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
    sc3 = sum([ma_sp<=3, last>ma60, ma5>ma10 or ma5/ma10>0.995])
    if sc3>=3: score+=4
    elif sc3>=2: score+=2

    bodies = np.abs(cls-ops)
    br = np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
    amp3 = np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br<=0.5, amp3<=3, pct_abs5<=1.5])
    if sc4>=2: score+=3
    elif sc4>=1: score+=1

    lsb = sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0 and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
    if lsb>=1: score+=3

    doji = sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/ops[i]*100<=0.5 and abs(cls[i]-ops[i])>0 and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
    if doji>=2: score+=2
    elif doji>=1: score+=1

    colors = ['R' if cls[i]>=ops[i] else 'G' for i in range(-5,0)]
    no3 = all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r = all(-2<=pcts[i]<=2 for i in range(-5,0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2<=pct5s<=3: score+=2

    grade = 'A' if score>=18 else 'B' if score>=12 else 'C'
    if grade in ('A','B'):
        return {'score': score, 'grade': grade, 'price': last,
                'dd60': pre['dd60'], 'vr520': vr520}
    return None


def screen_v2(df, idx):
    """V2终极版：满分20, 缩量3, 下影2, A≥18/B≥10, 回撤-20~-5, 量比0.4-0.8"""
    pre = _common_prefilter(df, idx, (-20, -5))
    if pre is None: return None
    cls, ops, his, los = pre['cls'], pre['ops'], pre['his'], pre['los']
    vols, turns, pcts = pre['vols'], pre['turns'], pre['pcts']
    n, last, ma60 = pre['n'], pre['last'], pre['ma60']
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])

    score = 0
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5/vol20 if vol20>0 else 999; vr560 = vol5/vol60 if vol60>0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = vols[-1]<vols[-2]<vols[-3] if n>=3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:])*1.2
    sc1 = sum([0.4<=vr520<=0.8, vr560<=0.7, turn5<=2, vol_dec, floor_vol])
    if sc1>=3: score+=3
    elif sc1>=1: score+=1

    rng5 = (np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
    cs = (np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
    sc2 = sum([rng5<=5, abs(cs)<=1, last>ma60])
    if sc2>=3: score+=4
    elif sc2>=2: score+=2

    ma_sp = (max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
    sc3 = sum([ma_sp<=3, last>ma60, ma5>ma10 or ma5/ma10>0.995])
    if sc3>=3: score+=4
    elif sc3>=2: score+=2

    bodies = np.abs(cls-ops)
    br = np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
    amp3 = np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br<=0.5, amp3<=3, pct_abs5<=1.5])
    if sc4>=2: score+=3
    elif sc4>=1: score+=1

    lsb = sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0 and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
    if lsb>=1: score+=2  # 降到2分

    doji = sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/ops[i]*100<=0.5 and abs(cls[i]-ops[i])>0 and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
    if doji>=2: score+=2
    elif doji>=1: score+=1

    colors = ['R' if cls[i]>=ops[i] else 'G' for i in range(-5,0)]
    no3 = all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r = all(-2<=pcts[i]<=2 for i in range(-5,0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2<=pct5s<=3: score+=2

    grade = 'A' if score>=18 else 'B' if score>=10 else 'C'
    if grade in ('A','B'):
        return {'score': score, 'grade': grade, 'price': last,
                'dd60': pre['dd60'], 'vr520': vr520}
    return None


# ════════════════════════════════════════════
# 交易模拟引擎
# ════════════════════════════════════════════
def sim_trades(screen_fn, max_per_day=3, use_market_filter=False,
               b_grade_first=False, close_based_sl=False, label=""):
    """
    screen_fn: 选股函数
    max_per_day: 每日最大新仓数
    use_market_filter: 空头排列时不开仓
    b_grade_first: B级优先排序
    close_based_sl: D1止损用收盘价判断(盘中-3%硬止损)
    """
    trades = []
    skipped_by_market = 0

    for di, scan_date in enumerate(bt_dates[:-3]):
        future = [d for d in bt_dates if d > scan_date]
        if len(future) < 3: continue
        buy_d, hold_d, exit_d = future[0], future[1], future[2]

        # 大盘过滤
        if use_market_filter:
            ms = market_state.get(scan_date)
            if ms and ms['bearish']:
                skipped_by_market += 1
                continue

        selected = []
        for code, df in stock_dict.items():
            dl = df['date'].values.tolist()
            if scan_date not in dl: continue
            idx = dl.index(scan_date)
            r = screen_fn(df, idx)
            if r:
                r['code'] = code
                selected.append(r)

        # 排序逻辑
        if b_grade_first:
            # B级优先，同级内按分数高→低
            selected.sort(key=lambda x: (0 if x['grade']=='B' else 1, -x['score']))
        else:
            selected.sort(key=lambda x: -x['score'])

        selected = selected[:max_per_day]

        for s in selected:
            code = s['code']
            df = stock_dict[code]
            dl = df['date'].values.tolist()
            if not all(d in dl for d in [buy_d, hold_d, exit_d]): continue

            bi = dl.index(buy_d); hi = dl.index(hold_d); ei = dl.index(exit_d)
            bp = df.iloc[bi]['open']
            if bp <= 0 or np.isnan(bp): continue

            d1h = df.iloc[bi]['high']; d1l = df.iloc[bi]['low']; d1c = df.iloc[bi]['close']
            d2h = df.iloc[hi]['high']; d2l = df.iloc[hi]['low']; d2c = df.iloc[hi]['close']
            d3o = df.iloc[ei]['open']

            tp1 = bp*1.03; tp2 = bp*1.05
            sl = bp*0.98; hard_sl = bp*0.97  # -3%硬止损

            rem = 1.0; ret = 0.0; reason = ""

            if close_based_sl:
                # D1: 盘中跌破-3%(硬止损) → 立即止损在-3%
                if d1l <= hard_sl:
                    ret += rem * (-3.0)
                    rem = 0; reason = "D1硬止损-3%"
                else:
                    # 盘中先看止盈
                    if d1h >= tp2:
                        ret += 0.5*3.0 + 0.5*5.0; rem = 0; reason = "D1止盈+5%"
                    elif d1h >= tp1:
                        ret += 0.5*3.0; rem = 0.5
                    # D1收盘判断止损: 收盘跌幅>1.5%
                    if rem > 0 and d1c < bp * 0.985:
                        d1_ret = (d1c - bp) / bp * 100
                        ret += rem * d1_ret; rem = 0
                        reason = (reason + "+D1收盘止损" if reason else f"D1收盘止损{d1_ret:.1f}%")
            else:
                # 旧的盘中止损逻辑
                if d1l <= sl:
                    ret += rem * (-2.0); rem = 0; reason = "D1止损-2%"
                else:
                    if d1h >= tp2:
                        ret += 0.5*3.0 + 0.5*5.0; rem = 0; reason = "D1止盈+5%"
                    elif d1h >= tp1:
                        ret += 0.5*3.0; rem = 0.5

            # D2
            if rem > 0:
                if d2l <= sl:
                    sl_ret = (sl - bp)/bp*100
                    ret += rem * sl_ret; rem = 0
                    reason = ("D2止损" if not reason else reason+"+D2止损")
                else:
                    if d2h >= tp2:
                        if rem == 1.0: ret += 0.5*3.0+0.5*5.0
                        else: ret += rem*5.0
                        rem = 0
                        reason = ("D2止盈+5%" if not reason else reason+"+D2止盈")
                    elif d2h >= tp1 and rem == 1.0:
                        ret += 0.5*3.0; rem = 0.5; reason = "D2半仓+3%"

            # D3
            if rem > 0:
                d3r = (d3o - bp)/bp*100
                ret += rem * d3r
                reason = (reason+"+D3清仓" if reason else "D3强制清仓")

            trades.append({
                'buy_date': buy_d, 'code': code, 'grade': s['grade'],
                'score': s['score'], 'return_pct': ret, 'exit_reason': reason,
                'dd60': s['dd60'], 'vr520': s['vr520'],
            })

    return trades, skipped_by_market


# ════════════════════════════════════════════
# 运行三种版本
# ════════════════════════════════════════════
print()
print("=" * 80)
print("  终极回测对比: 2025-10 ~ 2026-04 (6个月, 121天)")
print("=" * 80)

configs = [
    ("旧规则（基线）",
     dict(screen_fn=screen_old, max_per_day=3, use_market_filter=False,
          b_grade_first=False, close_based_sl=False)),
    ("V2终极版：大盘过滤+B优先+收盘止损+精选2笔",
     dict(screen_fn=screen_v2, max_per_day=2, use_market_filter=True,
          b_grade_first=True, close_based_sl=True)),
    # 消融实验：只加大盘过滤看效果
    ("旧规则+大盘过滤（只加一项）",
     dict(screen_fn=screen_old, max_per_day=3, use_market_filter=True,
          b_grade_first=False, close_based_sl=False)),
    # 消融：旧规则+收盘止损
    ("旧规则+收盘止损（只加一项）",
     dict(screen_fn=screen_old, max_per_day=3, use_market_filter=False,
          b_grade_first=False, close_based_sl=True)),
]

all_results = {}
for label, cfg in configs:
    print(f"\n  运行: {label} ...")
    trades, skipped = sim_trades(label=label, **cfg)
    all_results[label] = (trades, skipped)
    print(f"    → {len(trades)}笔交易, 跳过{skipped}个空头日")


# ════════════════════════════════════════════
# 输出对比
# ════════════════════════════════════════════
def calc_stats(trades):
    if not trades:
        return None
    rets = [t['return_pct'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    avg_w = np.mean(wins) if wins else 0
    avg_l = np.mean(losses) if losses else 0
    wr = len(wins)/len(rets)*100
    pf = sum(wins)/abs(sum(losses)) if losses and sum(losses)!=0 else 999
    ratio = abs(avg_w/avg_l) if avg_l != 0 else 999
    cum = 0; peak = 0; max_dd = 0
    sorted_t = sorted(trades, key=lambda x: x['buy_date'])
    for t in sorted_t:
        cum += t['return_pct']
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return {
        'n': len(trades), 'wr': wr, 'total': sum(rets),
        'avg': np.mean(rets), 'avg_w': avg_w, 'avg_l': avg_l,
        'ratio': ratio, 'pf': pf, 'max_dd': max_dd,
        'max_win': max(rets), 'max_loss': min(rets),
    }

print()
print("=" * 80)
print("  核心指标对比")
print("=" * 80)
print()
header = f"{'策略':<36s} {'笔数':>4s} {'胜率':>6s} {'总收益':>8s} {'均收益':>7s} {'盈亏比':>6s} {'利润因子':>8s} {'最大回撤':>8s}"
print(header)
print("─" * 100)

for label, (trades, skipped) in all_results.items():
    s = calc_stats(trades)
    if s:
        print(f"  {label:<34s} {s['n']:>4d}  {s['wr']:>5.1f}%  {s['total']:>+7.2f}%  {s['avg']:>+6.3f}%  {s['ratio']:>5.2f}  {s['pf']:>8.2f}  {s['max_dd']:>+7.2f}%")

# 详细分析终极版
print()
print("=" * 80)
print("  V2终极版 详细分析")
print("=" * 80)

trades_v2 = all_results["V2终极版：大盘过滤+B优先+收盘止损+精选2笔"][0]
if trades_v2:
    # 按评级
    print("\n  ── 按评级 ──")
    for g in ['A', 'B']:
        gt = [t for t in trades_v2 if t['grade'] == g]
        if gt:
            s = calc_stats(gt)
            print(f"    {g}级: {s['n']:>3d}笔  胜率{s['wr']:>5.1f}%  总收益{s['total']:>+7.2f}%  盈亏比{s['ratio']:.2f}")

    # 按出场方式
    print("\n  ── 按出场方式 ──")
    exit_map = {}
    for t in trades_v2:
        k = t['exit_reason']
        exit_map.setdefault(k, []).append(t['return_pct'])
    for reason, rets in sorted(exit_map.items(), key=lambda x: -len(x[1])):
        wr = len([r for r in rets if r>0])/len(rets)*100
        print(f"    {reason:<30s}: {len(rets):>3d}笔  胜率{wr:>5.1f}%  总{sum(rets):>+7.2f}%  均{np.mean(rets):>+6.3f}%")

    # 按月
    print("\n  ── 按月 ──")
    months = sorted(set(t['buy_date'][:7] for t in trades_v2))
    cum = 0
    for m in months:
        mt = [t for t in trades_v2 if t['buy_date'].startswith(m)]
        mr = [t['return_pct'] for t in mt]
        mw = [r for r in mr if r > 0]
        wr = len(mw)/len(mt)*100 if mt else 0
        cum += sum(mr)
        bars = int(abs(cum)/3)
        bar_ch = "█" if cum >= 0 else "▒"
        print(f"    {m}: {len(mt):>3d}笔  胜率{wr:>5.1f}%  月收{sum(mr):>+7.2f}%  累计{cum:>+7.2f}% {bar_ch*bars}")

    # 资金模拟
    cap = 100000
    rets = [t['return_pct'] for t in trades_v2]
    total_p = sum(r/100*cap for r in rets)
    n_months = 6
    print(f"\n  ── 资金测算（每笔10万）──")
    print(f"    总盈亏: {total_p:>+,.0f}元")
    print(f"    均每笔: {total_p/len(trades_v2):>+,.0f}元")
    print(f"    月均: {total_p/n_months:>+,.0f}元")
    print(f"    月均交易: {len(trades_v2)/n_months:.0f}笔")

# 对比旧规则的每月表现
print()
print("=" * 80)
print("  旧规则 vs V2 按月收益对比")
print("=" * 80)
trades_old = all_results["旧规则（基线）"][0]
months = sorted(set(t['buy_date'][:7] for t in trades_old))
cum_old = 0; cum_v2 = 0
print(f"\n  {'月份':<10s} {'旧规则笔数':>8s} {'旧收益':>8s} {'旧累计':>8s} {'V2笔数':>8s} {'V2收益':>8s} {'V2累计':>8s} {'差额':>8s}")
print("  " + "─" * 80)
for m in months:
    mt_old = [t for t in trades_old if t['buy_date'].startswith(m)]
    mt_v2 = [t for t in trades_v2 if t['buy_date'].startswith(m)]
    r_old = sum(t['return_pct'] for t in mt_old)
    r_v2 = sum(t['return_pct'] for t in mt_v2)
    cum_old += r_old; cum_v2 += r_v2
    diff = r_v2 - r_old
    print(f"  {m:<10s} {len(mt_old):>8d} {r_old:>+7.2f}% {cum_old:>+7.2f}% {len(mt_v2):>8d} {r_v2:>+7.2f}% {cum_v2:>+7.2f}% {diff:>+7.2f}%")
