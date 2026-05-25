#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时：尾盘全市场实时扫描（MySQL股票池 + QQ实时行情 + 05/25实时K线合成）"""
import sys, math, time
from datetime import datetime
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests
import pandas as pd
import numpy as np
from db_cache import get_connection

TODAY = datetime.now().strftime('%Y-%m-%d')
TODAY_QQ = datetime.now().strftime('%Y%m%d')


def fetch_qq(syms):
    r = requests.get('https://qt.gtimg.cn/q=' + ','.join(syms), timeout=12)
    r.encoding = 'gbk'
    return r.text


def parse_batch(text):
    out = {}
    for line in text.strip().split('\n'):
        if '="' not in line or '~' not in line:
            continue
        key = line.split('=')[0].replace('v_', '').replace('pv_', '').strip()
        items = line[line.index('="')+2:].rstrip('";').split('~')
        if len(items) < 39:
            continue
        def sf(i):
            try:
                return float(items[i])
            except Exception:
                return 0.0
        d = {
            'name': items[1], 'code6': items[2],
            'cur': sf(3), 'pre': sf(4), 'open': sf(5),
            'outer': sf(7), 'inner': sf(8),
            'upd': items[30] if len(items) > 30 else '',
            'chg': sf(31), 'pct': sf(32), 'hi': sf(33), 'lo': sf(34),
            'vol_lot': sf(36), 'amt_wan': sf(37), 'turn': sf(38),
        }
        if d['cur'] > 0 and d['pre'] > 0:
            out[key] = d
    return out


def qqsym(code):
    return code.replace('.', '')


def grade_pos(strategy, grade):
    if grade == 'A':
        return '1/4'
    if grade == 'B':
        return '1/8'
    return '观察'


print('='*88)
print(f'尾盘全市场实时扫描  {datetime.now():%Y-%m-%d %H:%M:%S}')
print('='*88)

# 1) 大盘实时
idx_syms = ['sh000001','sz399001','sz399006','sh000300']
idx = parse_batch(fetch_qq(idx_syms))
print('\n【实时大盘】')
market_ok = True
for s, n in [('sh000001','上证'),('sz399001','深证'),('sz399006','创业板'),('sh000300','沪深300')]:
    d = idx.get(s)
    if not d:
        continue
    print(f"  {n}: {d['cur']:.2f} {d['pct']:+.2f}%  今开{d['open']:.2f} 昨收{d['pre']:.2f} 高{d['hi']:.2f} 低{d['lo']:.2f} 更新{d['upd']}")
    if s in ('sh000001','sz399001','sz399006') and d['pct'] <= -1.0:
        market_ok = False
if not market_ok:
    print('  ⛔ 有主要指数跌超1%，按规则不建议尾盘新开仓。')
else:
    print('  ✅ 指数未触发跌>1%禁开，允许精选。')

# 2) 股票池
conn = get_connection()
rows = conn.execute("""
    SELECT code, code_name, industry
    FROM stock_industry
    WHERE (code LIKE 'sh.60%' OR code LIKE 'sz.00%')
      AND code_name NOT LIKE '%ST%' AND code_name NOT LIKE '%退%'
""").fetchall()
stock_info = {r[0]: {'name': r[1], 'industry': r[2] or '未分类'} for r in rows}
print(f'\n【股票池】主板非ST：{len(stock_info)} 只')

# 3) 全市场实时行情
all_quotes = {}
syms = [qqsym(c) for c in stock_info]
for i in range(0, len(syms), 80):
    batch = syms[i:i+80]
    try:
        all_quotes.update(parse_batch(fetch_qq(batch)))
    except Exception as e:
        print(f'  批次{i//80}失败: {e}')
    if i % 800 == 0:
        print(f'  实时行情进度 {min(i+80, len(syms))}/{len(syms)}')
    time.sleep(0.035)
quotes_by_code = {('sh.' if k.startswith('sh') else 'sz.') + k[2:]: v for k, v in all_quotes.items()}
print(f'  成功解析实时行情：{len(quotes_by_code)} 条')

# 4) 历史K线：一次性读近120自然日
codes = list(stock_info.keys())
parts = []
start_date = '2026-01-01'
for i in range(0, len(codes), 500):
    chunk = codes[i:i+500]
    ph = ','.join(['?'] * len(chunk))
    cur = conn.execute(f"""
        SELECT code, date, open, high, low, close, volume, amount, turn, pctChg
        FROM kline_daily
        WHERE date >= ? AND code IN ({ph})
        ORDER BY code, date
    """, [start_date] + chunk)
    part = cur.fetchall()
    if part:
        parts.extend(part)
conn.close()
cols = ['code','date','open','high','low','close','volume','amount','turn','pctChg']
hist = pd.DataFrame(parts, columns=cols)
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    hist[c] = pd.to_numeric(hist[c], errors='coerce')
hist['date'] = hist['date'].astype(str)
print(f'  历史K线：{len(hist)} 行，最新缓存日 {hist["date"].max() if len(hist) else "NA"}')

# 5) 行业实时排名
sector = defaultdict(lambda: {'sum':0.0, 'n':0, 'up':0})
for code, q in quotes_by_code.items():
    info = stock_info.get(code)
    if not info:
        continue
    ind = info['industry']
    sector[ind]['sum'] += q['pct']
    sector[ind]['n'] += 1
    if q['pct'] > 0.5:
        sector[ind]['up'] += 1
sector_rows = []
for ind, v in sector.items():
    if v['n'] >= 3:
        sector_rows.append((ind, v['sum']/v['n'], v['n'], v['up']))
sector_rows.sort(key=lambda x: x[1], reverse=True)
sector_rank = {ind: (i+1, len(sector_rows), avg, n, up) for i, (ind, avg, n, up) in enumerate(sector_rows)}
print('\n【实时行业TOP10】')
for i, (ind, avg, n, up) in enumerate(sector_rows[:10], 1):
    print(f'  {i:>2}. {ind[:18]:<18} avg {avg:+.2f}%  同涨>{0.5}%: {up}/{n}')

# 6) 计算全市场5日收益，供梯队定位
ret5_by_code = {}
for code, g in hist.groupby('code', sort=False):
    q = quotes_by_code.get(code)
    if q is None or len(g) < 6:
        continue
    gg = g.sort_values('date')
    base = float(gg['close'].iloc[-5])
    if base > 0:
        ret5_by_code[code] = (q['cur'] / base - 1) * 100
ret5_by_ind = defaultdict(list)
for code, r5 in ret5_by_code.items():
    ret5_by_ind[stock_info[code]['industry']].append(r5)

def tier_of(code):
    ind = stock_info[code]['industry']
    arr = sorted(ret5_by_ind.get(ind, []))
    if not arr or code not in ret5_by_code:
        return '—'
    r = ret5_by_code[code]
    pctile = sum(1 for x in arr if x <= r) / len(arr)
    if pctile >= 0.85:
        return '龙头'
    if pctile >= 0.50:
        return '跟风'
    return '补涨'

# 7) 策略评分
cands = []
reject = []
for code, info in stock_info.items():
    q = quotes_by_code.get(code)
    if not q:
        continue
    cur, pre, opn, hi, lo = q['cur'], q['pre'], q['open'], q['hi'], q['lo']
    pct, turn = q['pct'], q['turn']
    if not (5 <= cur <= 150 and 0.3 <= turn <= 8 and 0.5 <= pct <= 4.0):
        continue
    if hi <= lo or pre <= 0:
        continue
    amp = (hi - lo) / pre * 100
    from_hi = (hi - cur) / hi * 100
    tail_pos = (cur - lo) / (hi - lo) if hi > lo else 0
    if from_hi > 3.2 or tail_pos < 0.55:
        continue
    ind = info['industry']
    sr = sector_rank.get(ind)
    if not sr:
        continue
    rank, total, sec_avg, sec_n, sec_up = sr
    sec_pctile = rank / total
    if sec_pctile > 0.50:  # M2按前50%行业准入
        continue
    g = hist[hist['code'] == code].sort_values('date').copy()
    if len(g) < 60:
        continue
    # 历史只到上一交易日，今日实时合成
    today_amt_yuan = q['amt_wan'] * 10000.0
    avg10_amt = float(g['amount'].tail(10).mean()) if len(g) >= 10 else 0.0
    vol_ratio = today_amt_yuan / avg10_amt if avg10_amt > 0 else 0.0
    closes_with_today = list(g['close'].astype(float).values) + [cur]
    ma5 = float(np.mean(closes_with_today[-5:]))
    ma10 = float(np.mean(closes_with_today[-10:]))
    ma20 = float(np.mean(closes_with_today[-20:]))
    ma60 = float(np.mean(closes_with_today[-60:]))
    if cur < ma5:
        continue
    # 排除放量滞涨/缩量反弹/逼近追高
    if vol_ratio > 2.2 and pct < 1.2:
        continue
    if vol_ratio < 0.55 and pct > 1.5:
        continue
    if pct > 3.0:
        # 可列候选，但标记追高风险，最终一般不进推荐
        pass

    strategies = []

    # S1 16分制简化：蓄力后尾盘站上MA5
    conv = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / cur * 100
    s1 = 0; s1_detail = []
    if 0.8 <= vol_ratio <= 1.5:
        s1 += 3; s1_detail.append('缩量/温和量3')
    elif 0.55 <= vol_ratio <= 2.0:
        s1 += 2; s1_detail.append('量能2')
    elif vol_ratio <= 2.2:
        s1 += 1; s1_detail.append('量能1')
    if 1.2 <= amp <= 4.5:
        s1 += 5; s1_detail.append('波动收敛5')
    elif amp <= 6.5:
        s1 += 3; s1_detail.append('波动3')
    if sec_pctile <= 0.15:
        s1 += 3; s1_detail.append('板块3')
    elif sec_pctile <= 0.30:
        s1 += 2; s1_detail.append('板块2')
    else:
        s1 += 1; s1_detail.append('板块1')
    last5 = list(g['pctChg'].tail(5).astype(float).values)
    if any(x > 0 for x in last5) and any(x < 0 for x in last5):
        s1 += 2; s1_detail.append('红绿交替2')
    ma_bull = cur > ma5 >= ma10 >= ma20 or cur > ma5 and ma5 > ma20
    if ma_bull and ret5_by_code.get(code, 0) <= 12:
        s1 += 3; s1_detail.append('结构3')
    elif cur > ma20:
        s1 += 2; s1_detail.append('结构2')
    if conv <= 8 and 0.5 <= pct <= 3.5 and s1 >= 11:
        strategies.append(('S1', s1, 'A' if s1 >= 13 else 'B', ';'.join(s1_detail)))

    # S2：近5个完成交易日有大阳，今日守住且不追高
    completed = g.tail(12).reset_index(drop=True)
    best_bc = None
    for j in range(max(5, len(completed)-6), len(completed)):
        row = completed.iloc[j]
        prev5 = completed.iloc[max(0, j-5):j]
        if len(prev5) < 3:
            continue
        vr_bc = row['amount'] / max(prev5['amount'].mean(), 1)
        if row['pctChg'] >= 4.0 and row['close'] > row['open'] and vr_bc >= 1.35:
            best_bc = row
    if best_bc is not None and cur > ma60:
        bc_open, bc_close, bc_amt = float(best_bc['open']), float(best_bc['close']), float(best_bc['amount'])
        shrink = today_amt_yuan / max(bc_amt, 1)
        s2 = 0; d2 = []
        if shrink <= 0.70:
            s2 += 2; d2.append('缩量2')
        elif shrink <= 1.00:
            s2 += 1; d2.append('缩量1')
        if cur >= bc_close:
            s2 += 2; d2.append('守收2')
        elif cur >= bc_open:
            s2 += 1; d2.append('守开1')
        if sec_pctile <= 0.30:
            s2 += 2; d2.append('板块2')
        elif sec_pctile <= 0.50:
            s2 += 1; d2.append('板块1')
        if cur > ma5 and ma5 > ma20 and ma20 > ma60:
            s2 += 2; d2.append('均线2')
        elif cur > ma20 > ma60:
            s2 += 1; d2.append('均线1')
        if s2 >= 6 and shrink <= 1.05:
            strategies.append(('S2', s2, 'A' if s2 >= 7 else 'B', ';'.join(d2) + f';大阳{best_bc["date"]}'))

    if not strategies:
        continue
    # 尾盘三项
    tail_checks = []
    if vol_ratio > 1.2:
        tail_checks.append('量比>1.2')
    if cur >= opn and tail_pos >= 0.60 and from_hi <= 2.5:
        tail_checks.append('K线上半段')
    if sec_up >= 2:
        tail_checks.append('板块同步')
    if len(tail_checks) < 2:
        reject.append((code, info['name'], '尾盘三项不足', pct, cur, ','.join(tail_checks)))
        continue
    # 取最优策略
    strategy, score, grade, detail = sorted(strategies, key=lambda x: (x[2]=='A', x[1]), reverse=True)[0]
    if pct > 3.0:
        reject.append((code, info['name'], '涨幅>3%接近追高线', pct, cur, strategy))
        continue
    entry_lo = max(cur * 0.985, ma5 * 0.995)
    entry_hi = min(cur * 1.003, cur * 1.015)
    hard_stop = round(cur * 0.97, 2)
    soft_stop = round(cur * 0.985, 2)
    tp1 = round(cur * 1.04, 2)
    tp2 = round(cur * 1.065, 2)
    cands.append({
        'code': code, 'name': info['name'], 'industry': ind, 'cur': cur, 'pct': pct,
        'strategy': strategy, 'score': score, 'grade': grade, 'detail': detail,
        'rank': rank, 'total': total, 'sec_avg': sec_avg, 'sec_up': sec_up, 'sec_n': sec_n,
        'tier': tier_of(code), 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'vol_ratio': vol_ratio, 'tail_checks': tail_checks, 'entry_lo': entry_lo, 'entry_hi': entry_hi,
        'hard_stop': hard_stop, 'soft_stop': soft_stop, 'tp1': tp1, 'tp2': tp2,
        'from_hi': from_hi, 'tail_pos': tail_pos, 'turn': turn, 'amp': amp,
        'final': score + (3 if rank <= max(1, total*0.15) else 2 if rank <= total*0.3 else 1) + (2 if tier_of(code)=='龙头' else 1 if tier_of(code)=='跟风' else 0) + (1 if vol_ratio>1.2 else 0)
    })

# 同行业去重
cands.sort(key=lambda x: (x['final'], x['score'], -x['pct']), reverse=True)
kept = []
used_ind = set()
for c in cands:
    if c['industry'] in used_ind:
        continue
    kept.append(c)
    used_ind.add(c['industry'])

print('\n【达标候选（同行业去重后TOP10）】')
print(f"{'#':>2} {'代码':<9} {'名称':<8} {'价':>7} {'涨幅':>7} {'策略':<3} {'分':>3} {'级':>2} {'板块排':>8} {'梯队':<4} {'量比':>5} {'尾盘确认'}")
for i, c in enumerate(kept[:10], 1):
    print(f"{i:>2} {c['code']:<9} {c['name'][:8]:<8} {c['cur']:>7.2f} {c['pct']:>+6.2f}% {c['strategy']:<3} {c['score']:>3} {c['grade']:>2} {c['rank']:>2}/{c['total']:<3} {c['tier']:<4} {c['vol_ratio']:>5.2f} {'+'.join(c['tail_checks'])}")

print('\n【推荐详情TOP5】')
for i, c in enumerate(kept[:5], 1):
    print('-'*88)
    print(f"#{i} {c['name']} {c['code']}  现价{c['cur']:.2f}  涨幅{c['pct']:+.2f}%  {c['strategy']} {c['grade']}级 {c['score']}分  板块{c['rank']}/{c['total']}  梯队:{c['tier']}")
    print(f"  行业: {c['industry']}  行业实时均涨{c['sec_avg']:+.2f}% 同涨{c['sec_up']}/{c['sec_n']}")
    print(f"  均线: MA5={c['ma5']:.2f} MA10={c['ma10']:.2f} MA20={c['ma20']:.2f} MA60={c['ma60']:.2f}  量比={c['vol_ratio']:.2f} 振幅={c['amp']:.1f}% 距高={c['from_hi']:.1f}%")
    print(f"  评分明细: {c['detail']}  尾盘三项: {'、'.join(c['tail_checks'])}")
    print(f"  买入价: {c['entry_lo']:.2f} ~ {c['entry_hi']:.2f}  仓位:{grade_pos(c['strategy'], c['grade'])}")
    print(f"  止损: -3%硬止损{c['hard_stop']:.2f} / 收盘-1.5%软止损{c['soft_stop']:.2f}")
    print(f"  止盈: +4%卖50% → {c['tp1']:.2f} / 移动止盈(高点回落2.5%全清，二档参考{c['tp2']:.2f})")
    if c['strategy'] == 'S2':
        print('  逻辑失效: 跌回大阳线开盘/放量跌破MA5，或尾盘放量滞涨转弱')
    else:
        print('  逻辑失效: 收盘跌破MA5且板块同步转弱，或放量滞涨/跌回整理区')

print('\n【未入选样例】')
for r in reject[:12]:
    print(f'  {r[0]} {r[1]} {r[4]:.2f} {r[3]:+.2f}% → {r[2]} ({r[5]})')

print('\n说明：实时价/指数来自腾讯接口；历史K线/行业映射来自本地MySQL缓存(最新日2026-05-22)，今日K线用14点后实时价合成。')
