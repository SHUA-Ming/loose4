#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D0尾盘买入 vs D1次日开盘买入 对比回测
对 S1/S2/S3 三个策略分别测试两种入场时机
"""
import sys, os, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd, numpy as np

init_db()
conn = get_connection()
print("加载数据...")
all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)

# 加载板块数据
ind_df = pd.read_sql('SELECT code, industry FROM stock_industry', conn)
industry_map = dict(zip(ind_df['code'], ind_df['industry']))
sec_df = pd.read_sql('SELECT * FROM sector_daily ORDER BY industry, date', conn)
conn.close()

for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

# 板块动量排名（按日滚动计算）
sector_rank_by_date = {}
if len(sec_df) > 0:
    for c2 in ['avg_pct','total_amount']:
        sec_df[c2] = pd.to_numeric(sec_df[c2], errors='coerce')
    sec_dates = sorted(sec_df['date'].unique())
    for dt in sec_dates:
        # 取最近5个交易日
        past5 = [d for d in sec_dates if d <= dt][-5:]
        sub = sec_df[sec_df['date'].isin(past5)]
        momentum = sub.groupby('industry')['avg_pct'].mean().sort_values(ascending=False)
        n = len(momentum)
        rank_dict = {}
        for rank_i, (ind_name, _) in enumerate(momentum.items()):
            rank_dict[ind_name] = 1 - rank_i / max(n - 1, 1)  # 1=最强 0=最弱
        sector_rank_by_date[dt] = rank_dict

# 市场状态
market_daily = all_data.groupby('date').agg(mkt_close=('close','mean')).reset_index().sort_values('date').reset_index(drop=True)
mkt_cls = market_daily['mkt_close'].values; mkt_dates = market_daily['date'].values
market_state = {}
for i in range(20, len(market_daily)):
    ma5 = np.mean(mkt_cls[i-4:i+1]); ma10 = np.mean(mkt_cls[i-9:i+1]); ma20 = np.mean(mkt_cls[i-19:i+1])
    market_state[mkt_dates[i]] = {'bearish': ma5 < ma10 < ma20}

stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df) >= 60:
        stock_dict[code] = df
print(f"股票数: {len(stock_dict)}")

BT_START = '2025-10-01'; BT_END = '2026-04-14'
all_dates_list = sorted(all_data['date'].unique())
bt_dates = [d for d in all_dates_list if BT_START <= d <= BT_END]
print(f"范围: {bt_dates[0]}~{bt_dates[-1]}, {len(bt_dates)}天")


# ═══════════════════════════════════════════════════════════════
# S1 选股函数（蓄力候选，V3 20分制）
# ═══════════════════════════════════════════════════════════════
def screen_s1(df, idx, sec_rank):
    if idx < 60: return None
    data = df.iloc[:idx+1]
    cls = data['close'].values; ops = data['open'].values
    his = data['high'].values; los = data['low'].values
    vols = data['volume'].values; turns = data['turn'].values
    pcts = data['pctChg'].values; amts = data['amount'].values
    n = len(data); last = cls[-1]; code = data['code'].iloc[0]

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None
    c60 = cls[-60:]; pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None
    max60 = np.max(c60); dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): return None
    if np.sum(pcts[-60:] >= 9.5) < 1: return None
    if np.any(pcts[-5:] < -5): return None
    if np.any(turns[-5:] > 8): return None

    # 板块过滤
    ind = industry_map.get(code, '')
    sp = sec_rank.get(ind, 0.5)
    if sp < 0.3 and ind: return None

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0

    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5/vol20 if vol20 > 0 else 999
    vr560 = vol5/vol60 if vol60 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = vols[-1] < vols[-2] < vols[-3] if n >= 3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:]) * 1.2
    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 4: score += 3
    elif sc1 >= 3: score += 2
    elif sc1 >= 1: score += 1

    rng5 = (np.max(cls[-5:]) - np.min(cls[-5:])) / np.mean(cls[-5:]) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    hp_days = 0
    for hi2 in range(1, min(21, n)):
        if abs(pcts[-hi2]) <= 1.5:
            hp_days += 1
        else:
            break
    sc2 = sum([rng5 <= 5, abs(cs) <= 1, last > ma60, hp_days >= 5])
    if sc2 >= 4: score += 4
    elif sc2 >= 3: score += 3
    elif sc2 >= 2: score += 2
    elif sc2 >= 1: score += 1

    ma_sp = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, last > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3: score += 4
    elif sc3 >= 2: score += 3
    elif sc3 >= 1: score += 2

    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 2: score += 3
    elif sc4 >= 1: score += 2
    else: score += 1

    lsb = sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0 and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
    if lsb >= 1: score += 2

    doji = sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/max(ops[i],0.01)*100<=0.5 and abs(cls[i]-ops[i])>0 and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
    if doji >= 2: score += 2
    elif doji >= 1: score += 1

    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5,0)]
    no3 = all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5,0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3: score += 2

    grade = 'A' if score >= 16 else ('B' if score >= 15 else 'C')
    if grade in ('A','B'):
        return {'score': score, 'grade': grade, 'price': last, 'strategy': 'S1'}
    return None


# ═══════════════════════════════════════════════════════════════
# S2 选股函数（大阳后缩量横盘）
# ═══════════════════════════════════════════════════════════════
def screen_s2(df, idx, sec_rank):
    if idx < 60: return None
    data = df.iloc[:idx+1]
    cls = data['close'].values; ops = data['open'].values
    his = data['high'].values; los = data['low'].values
    vols = data['volume'].values; turns = data['turn'].values
    pcts = data['pctChg'].values; amts = data['amount'].values
    n = len(data); last = cls[-1]; code = data['code'].iloc[0]

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    t_last = turns[-1] if len(turns) > 0 else 0
    if t_last <= 0: return None
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: return None

    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None

    ind = industry_map.get(code, '')
    sp = sec_rank.get(ind, 0.5)
    if sp < 0.3 and ind: return None

    vol20 = np.mean(vols[-20:])
    big_candle_idx = None
    for di in range(1, min(6, n)):
        i2 = n - di
        if i2 < 1: break
        day_pct = (cls[i2] / cls[i2-1] - 1) * 100
        day_vol_ratio = vols[i2] / vol20 if vol20 > 0 else 0
        is_yang = cls[i2] > ops[i2]
        if day_pct >= 4 and is_yang and day_vol_ratio >= 1.5:
            big_candle_idx = i2; break

    if big_candle_idx is None: return None
    bc_close = cls[big_candle_idx]; bc_open = ops[big_candle_idx]
    bc_vol = vols[big_candle_idx]
    days_after = n - 1 - big_candle_idx
    if days_after < 1: return None

    post_vols = vols[big_candle_idx+1:]
    avg_post_vol = np.mean(post_vols)
    vol_shrink = avg_post_vol / bc_vol if bc_vol > 0 else 999
    if vol_shrink >= 0.7: return None
    if last < bc_open: return None

    post_pcts = pcts[big_candle_idx+1:]
    if np.any(post_pcts < -3): return None
    post_vols_arr = vols[big_candle_idx+1:]
    for pi in range(len(post_vols_arr)):
        if post_vols_arr[pi] > bc_vol * 0.8 and post_pcts[pi] < 0:
            return None
    if np.any(turns[-5:] > 8): return None

    score = 0
    if vol_shrink <= 0.5: score += 2
    elif vol_shrink <= 0.7: score += 1

    price_hold = last / bc_close if bc_close > 0 else 0
    if price_hold >= 0.99: score += 2
    elif price_hold >= 0.97: score += 1

    if sp >= 0.7: score += 2
    elif sp >= 0.5: score += 1

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    if ma5 > ma10 > ma20: score += 2
    elif ma5 > ma10: score += 1

    grade = 'A' if score >= 7 else ('B' if score >= 6 else 'C')
    if grade in ('A','B'):
        return {'score': score, 'grade': grade, 'price': last, 'strategy': 'S2',
                'bc_open': bc_open}
    return None


# ═══════════════════════════════════════════════════════════════
# S3 选股函数（放量突破新高）
# ═══════════════════════════════════════════════════════════════
def screen_s3(df, idx, sec_rank):
    if idx < 60: return None
    data = df.iloc[:idx+1]
    cls = data['close'].values; ops = data['open'].values
    his = data['high'].values; los = data['low'].values
    vols = data['volume'].values; turns = data['turn'].values
    pcts = data['pctChg'].values; amts = data['amount'].values
    n = len(data); last = cls[-1]; code = data['code'].iloc[0]

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    t_last = turns[-1] if len(turns) > 0 else 0
    if t_last <= 0: return None
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: return None

    if n < 21: return None
    high20 = np.max(his[-21:-1])
    if last <= high20: return None
    brk_pct = (last / high20 - 1) * 100

    vol20 = np.mean(vols[-20:])
    vol_ratio = vols[-1] / vol20 if vol20 > 0 else 0
    if vol_ratio < 1.5: return None
    if last <= ops[-1]: return None

    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
    if not (last > ma20 > ma60): return None

    ind = industry_map.get(code, '')
    sp = sec_rank.get(ind, 0.5)
    if sp < 0.5 and ind: return None

    if pcts[-1] > 7: return None
    chg5 = (cls[-1] / cls[-6] - 1) * 100 if n >= 6 else 0
    if chg5 > 20: return None
    if turns[-1] > 10: return None

    score = 0
    if brk_pct > 3: score += 2
    elif brk_pct > 1: score += 1
    if vol_ratio > 2.5: score += 2
    elif vol_ratio > 1.5: score += 1
    if sp >= 0.7: score += 2
    elif sp >= 0.5: score += 1

    grade = 'A' if score >= 5 else ('B' if score >= 4 else 'C')
    if grade in ('A','B'):
        return {'score': score, 'grade': grade, 'price': last, 'strategy': 'S3'}
    return None


# ═══════════════════════════════════════════════════════════════
# 通用模拟引擎
# ═══════════════════════════════════════════════════════════════
def simulate_trade(df, buy_idx, buy_price, mode, max_hold=3):
    """
    mode='D0': 买入当天算D0，持有D1、D2，D3开盘清仓
    mode='D1': D0选股，D1开盘买入，持有D1、D2，D3开盘清仓
    
    对于D0模式：
      buy_price = D0收盘价（尾盘买入近似）
      D1 = buy_idx + 1
      D2 = buy_idx + 2
      D3 = buy_idx + 3  → 开盘清仓
      
    对于D1模式：
      buy_price = D1开盘价
      D1 = buy_idx + 1（买入日本身）
      D2 = buy_idx + 2
      D3 = buy_idx + 3  → 开盘清仓
    """
    n = len(df)
    tp1 = buy_price * 1.03
    tp2 = buy_price * 1.05
    hsl = buy_price * 0.97  # 硬止损-3%
    rem = 1.0; ret = 0.0; reason = ""

    if mode == 'D0':
        # D0尾盘买入，D1是第一个完整交易日
        d1_idx = buy_idx + 1
        d2_idx = buy_idx + 2
        d3_idx = buy_idx + 3
    else:  # D1
        # D1开盘买入，D1本身就有盘中波动
        d1_idx = buy_idx + 1  # 这就是买入日
        d2_idx = buy_idx + 2
        d3_idx = buy_idx + 3

    # D1 day
    if d1_idx >= n: return None
    d1h = df.iloc[d1_idx]['high']; d1l = df.iloc[d1_idx]['low']
    d1c = df.iloc[d1_idx]['close']

    if d1l <= hsl:
        ret += rem * (-3.0); rem = 0; reason = "D1硬止损-3%"
    else:
        if d1h >= tp2:
            ret += 0.5 * 3 + 0.5 * 5; rem = 0; reason = "D1止盈+5%"
        elif d1h >= tp1:
            ret += 0.5 * 3; rem = 0.5
        if rem > 0 and d1c < buy_price * 0.985:
            d1r = (d1c - buy_price) / buy_price * 100
            ret += rem * d1r; rem = 0
            reason = (reason + "+D1收盘止损" if reason else f"D1收盘止损{d1r:.1f}%")

    # D2 day
    if rem > 0 and d2_idx < n:
        d2h = df.iloc[d2_idx]['high']; d2l = df.iloc[d2_idx]['low']
        sl2 = buy_price * 0.98
        if d2l <= sl2:
            ret += rem * (-2.0); rem = 0
            reason = (reason + "+D2止损" if reason else "D2止损-2%")
        else:
            if d2h >= tp2:
                if rem == 1.0: ret += 0.5*3 + 0.5*5
                else: ret += rem * 5
                rem = 0
                reason = (reason + "+D2止盈" if reason else "D2止盈")
            elif d2h >= tp1 and rem == 1.0:
                ret += 0.5 * 3; rem = 0.5

    # D3 forced exit
    if rem > 0 and d3_idx < n:
        d3o = df.iloc[d3_idx]['open']
        d3r = (d3o - buy_price) / buy_price * 100
        ret += rem * d3r
        reason = (reason + "+D3清仓" if reason else "D3强制清仓")
    elif rem > 0:
        return None  # no D3 data

    return {'return_pct': ret, 'exit_reason': reason}


# ═══════════════════════════════════════════════════════════════
# 主回测循环
# ═══════════════════════════════════════════════════════════════
print("\n开始回测...")

results = {
    'S1_D0': [], 'S1_D1': [],
    'S2_D0': [], 'S2_D1': [],
    'S3_D0': [], 'S3_D1': [],
}

for di, scan_date in enumerate(bt_dates[:-4]):  # 需要至少4天余量
    ms = market_state.get(scan_date)
    if ms and ms['bearish']:
        continue

    sec_rank = sector_rank_by_date.get(scan_date, {})
    # 找到scan_date之后的日期序列
    fut = [d for d in bt_dates if d > scan_date]
    if len(fut) < 4:
        continue

    for code, df in stock_dict.items():
        dl = df['date'].values.tolist()
        if scan_date not in dl:
            continue
        idx = dl.index(scan_date)
        if idx + 4 >= len(df):
            continue

        # 尝试三个策略
        for screen_fn, strat_name in [(screen_s1, 'S1'), (screen_s2, 'S2'), (screen_s3, 'S3')]:
            r = screen_fn(df, idx, sec_rank)
            if r is None:
                continue

            # D0模式：尾盘收盘价买入
            d0_price = df.iloc[idx]['close']
            d0_trade = simulate_trade(df, idx, d0_price, 'D0')

            # D1模式：次日开盘价买入
            d1_price = df.iloc[idx+1]['open']
            if d1_price <= 0 or np.isnan(d1_price):
                continue
            d1_trade = simulate_trade(df, idx, d1_price, 'D1')

            if d0_trade and d1_trade:
                base = {
                    'date': scan_date, 'code': code, 'grade': r['grade'],
                    'score': r['score'], 'scan_close': d0_price
                }
                d0_rec = {**base, 'buy_price': d0_price, 'mode': 'D0',
                          'return_pct': d0_trade['return_pct'], 'reason': d0_trade['exit_reason']}
                d1_rec = {**base, 'buy_price': d1_price, 'mode': 'D1',
                          'return_pct': d1_trade['return_pct'], 'reason': d1_trade['exit_reason']}

                # 计算D1开盘跳空
                gap_pct = (d1_price / d0_price - 1) * 100
                d0_rec['gap_pct'] = gap_pct
                d1_rec['gap_pct'] = gap_pct

                results[f'{strat_name}_D0'].append(d0_rec)
                results[f'{strat_name}_D1'].append(d1_rec)

    if (di + 1) % 20 == 0:
        print(f"  已扫描 {di+1}/{len(bt_dates)-4} 天...")

print(f"扫描完成！")


# ═══════════════════════════════════════════════════════════════
# 统计输出
# ═══════════════════════════════════════════════════════════════
def report(trades, label):
    if not trades:
        print(f"\n  {label}: 无交易")
        return {}
    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df['return_pct'] > 0]
    losses = df[df['return_pct'] <= 0]
    wr = len(wins) / n * 100
    avg_ret = df['return_pct'].mean()
    total_ret = df['return_pct'].sum()
    avg_win = wins['return_pct'].mean() if len(wins) > 0 else 0
    avg_loss = losses['return_pct'].mean() if len(losses) > 0 else 0
    sum_win = wins['return_pct'].sum() if len(wins) > 0 else 0
    sum_loss = abs(losses['return_pct'].sum()) if len(losses) > 0 else 0.01
    pf = sum_win / sum_loss if sum_loss > 0 else 999

    # 最大回撤
    cum = np.cumsum([t['return_pct'] for t in trades])
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = np.min(dd)

    # 按级别细分
    a_trades = [t for t in trades if t['grade'] == 'A']
    b_trades = [t for t in trades if t['grade'] == 'B']

    print(f"\n  {'='*70}")
    print(f"  {label}")
    print(f"  {'='*70}")
    print(f"  交易数: {n}  胜率: {wr:.1f}%  平均收益: {avg_ret:+.2f}%")
    print(f"  总收益: {total_ret:+.1f}%  平均盈利: {avg_win:+.2f}%  平均亏损: {avg_loss:.2f}%")
    print(f"  利润因子: {pf:.2f}  最大回撤: {max_dd:.1f}%")

    if a_trades:
        a_df = pd.DataFrame(a_trades)
        a_wr = len(a_df[a_df['return_pct'] > 0]) / len(a_df) * 100
        print(f"    A级: {len(a_trades)}笔  胜率{a_wr:.1f}%  均收益{a_df['return_pct'].mean():+.2f}%")
    if b_trades:
        b_df = pd.DataFrame(b_trades)
        b_wr = len(b_df[b_df['return_pct'] > 0]) / len(b_df) * 100
        print(f"    B级: {len(b_trades)}笔  胜率{b_wr:.1f}%  均收益{b_df['return_pct'].mean():+.2f}%")

    # 退出原因分布
    reasons = {}
    for t in trades:
        r_key = t['reason'].split('+')[0] if '+' in t['reason'] else t['reason']
        reasons[r_key] = reasons.get(r_key, 0) + 1
    print(f"  退出原因: ", end='')
    for r_key, cnt in sorted(reasons.items(), key=lambda x: -x[1])[:5]:
        print(f"{r_key}:{cnt}", end='  ')
    print()

    return {'n': n, 'wr': wr, 'avg_ret': avg_ret, 'total_ret': total_ret,
            'pf': pf, 'max_dd': max_dd, 'avg_win': avg_win, 'avg_loss': avg_loss}


# 逐策略报告
print("\n" + "=" * 80)
print("  D0 (尾盘收盘价买入) vs D1 (次日开盘价买入) 对比回测")
print(f"  回测期: {BT_START} ~ {BT_END}")
print("=" * 80)

all_stats = {}
for strat in ['S1', 'S2', 'S3']:
    all_stats[f'{strat}_D0'] = report(results[f'{strat}_D0'], f'{strat} · D0尾盘买入')
    all_stats[f'{strat}_D1'] = report(results[f'{strat}_D1'], f'{strat} · D1次日买入')

    # 配对分析：同一信号D0 vs D1差异
    d0_list = results[f'{strat}_D0']
    d1_list = results[f'{strat}_D1']
    if d0_list and d1_list:
        diffs = []
        for t0, t1 in zip(d0_list, d1_list):
            diffs.append(t0['return_pct'] - t1['return_pct'])
        diffs = np.array(diffs)
        gap_pcts = np.array([t['gap_pct'] for t in d0_list])
        print(f"\n  ── {strat} 配对差异（D0 - D1）──")
        print(f"  配对数: {len(diffs)}")
        print(f"  D0更优次数: {np.sum(diffs > 0)} ({np.sum(diffs > 0)/len(diffs)*100:.1f}%)")
        print(f"  D1更优次数: {np.sum(diffs < 0)} ({np.sum(diffs < 0)/len(diffs)*100:.1f}%)")
        print(f"  平均差异: {np.mean(diffs):+.3f}%")
        print(f"  中位差异: {np.median(diffs):+.3f}%")
        print(f"  次日平均跳空: {np.mean(gap_pcts):+.3f}% (正=高开, 负=低开)")
        print(f"  次日跳空中位: {np.median(gap_pcts):+.3f}%")

        # 按跳空方向分组
        gap_up = [(d, g) for d, g in zip(diffs, gap_pcts) if g > 0.5]
        gap_dn = [(d, g) for d, g in zip(diffs, gap_pcts) if g < -0.5]
        gap_flat = [(d, g) for d, g in zip(diffs, gap_pcts) if -0.5 <= g <= 0.5]
        if gap_up:
            print(f"  高开(>+0.5%): {len(gap_up)}次  D0多赚{np.mean([x[0] for x in gap_up]):+.3f}%")
        if gap_dn:
            print(f"  低开(<-0.5%): {len(gap_dn)}次  D0多赚{np.mean([x[0] for x in gap_dn]):+.3f}%")
        if gap_flat:
            print(f"  平开(±0.5%):  {len(gap_flat)}次  D0多赚{np.mean([x[0] for x in gap_flat]):+.3f}%")


# 综合对比表
print("\n\n" + "=" * 80)
print("  综合对比汇总表")
print("=" * 80)
print(f"  {'策略':>8s} {'模式':>6s} {'交易数':>6s} {'胜率':>7s} {'均收益':>8s} {'总收益':>8s} {'利润因子':>8s} {'最大DD':>8s}")
print("-" * 80)
for strat in ['S1', 'S2', 'S3']:
    for mode in ['D0', 'D1']:
        key = f'{strat}_{mode}'
        s = all_stats.get(key, {})
        if s:
            tag = "尾盘" if mode == 'D0' else "次日"
            print(f"  {strat:>8s} {tag:>6s} {s['n']:>6d} {s['wr']:>6.1f}% {s['avg_ret']:>+7.2f}% {s['total_ret']:>+7.1f}% {s['pf']:>8.2f} {s['max_dd']:>+7.1f}%")
    print()

# 结论
print("=" * 80)
print("  结论")
print("=" * 80)
for strat in ['S1', 'S2', 'S3']:
    d0s = all_stats.get(f'{strat}_D0', {})
    d1s = all_stats.get(f'{strat}_D1', {})
    if d0s and d1s:
        d0_better = d0s['avg_ret'] > d1s['avg_ret']
        winner = 'D0尾盘' if d0_better else 'D1次日'
        diff = abs(d0s['avg_ret'] - d1s['avg_ret'])
        print(f"  {strat}: {winner}更优 (每笔多{diff:.3f}%, 胜率差{d0s['wr']-d1s['wr']:+.1f}pp)")

print("\n回测完成。")
