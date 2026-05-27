#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强势反弹日选股器 v2
大盘暴涨日（>2%），原蓄力洗盘策略不适用，改用：
1. 趋势确认回踩模式：均线多头+今日涨幅适中(不追涨停)
2. 同时保留蓄力模式但放宽条件
"""
import sys, warnings, time
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

import requests
import numpy as np
from datetime import datetime
_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_industry_map as _db_get_industry_map

SINGLE_STOCK_MIN_POSITION = 0.5

print("=" * 80)
print(f"  强势日选股器 v2  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 80)

def batch_fetch_qq(codes, batch_size=80):
    results = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        syms = ','.join(batch)
        try:
            resp = requests.get(f"http://qt.gtimg.cn/q={syms}", timeout=10)
            resp.encoding = 'gbk'
            text = resp.text.strip()
        except:
            continue
        for line in text.split('\n'):
            line = line.strip()
            if '="' not in line or '~' not in line:
                continue
            try:
                idx = line.index('="') + 2
                payload = line[idx:].rstrip('";')
                items = payload.split('~')
                if len(items) < 45: continue
                def sf(j):
                    try: return float(items[j])
                    except: return 0.0
                code_raw = items[2]; name = items[1]
                cur = sf(3); pre = sf(4); opn = sf(5)
                hi = sf(33); lo = sf(34); pct = sf(32)
                vol = sf(36); amt = sf(37); turn = sf(38)
                outer = sf(7); inner = sf(8)
                if cur <= 0 or pre <= 0: continue
                sym = ''
                for b in batch:
                    if code_raw in b: sym = b[:2]; break
                results[f"{sym}{code_raw}"] = {
                    'code': code_raw, 'name': name, 'sym': f"{sym}{code_raw}",
                    'cur': cur, 'pre': pre, 'open': opn, 'high': hi, 'low': lo,
                    'pct': pct, 'vol': vol, 'amt': amt, 'turn': turn,
                    'outer': outer, 'inner': inner,
                }
            except: continue
        time.sleep(0.08)
    return results

def fetch_kline_sina(code, days=130):
    try:
        url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if not data or not isinstance(data, list): return None
        return [{
            'date': k['day'],
            'open': float(k['open']), 'close': float(k['close']),
            'high': float(k['high']), 'low': float(k['low']),
            'volume': float(k.get('volume', 0)),
        } for k in data]
    except: return None


def get_market_snapshot(all_data):
    """大盘快照：上证/深成/创业板，用于总开关和仓位修正"""
    idx = {
        'sh000001': all_data.get('sh000001'),
        'sz399001': all_data.get('sz399001'),
        'sz399006': all_data.get('sz399006'),
    }

    sh_pct = idx['sh000001']['pct'] if idx['sh000001'] else 0.0
    sz_pct = idx['sz399001']['pct'] if idx['sz399001'] else 0.0
    cyb_pct = idx['sz399006']['pct'] if idx['sz399006'] else 0.0
    avg_pct = (sh_pct + sz_pct + cyb_pct) / 3

    if sh_pct <= -2:
        state = '恐慌'
        action = '只卖不买'
    elif sh_pct <= -1:
        state = '偏弱'
        action = '轻仓试错'
    elif sh_pct >= 1:
        state = '偏强'
        action = '可正常操作'
    else:
        state = '中性'
        action = '精选个股'

    return {
        'sh_pct': sh_pct,
        'sz_pct': sz_pct,
        'cyb_pct': cyb_pct,
        'avg_pct': avg_pct,
        'state': state,
        'action': action,
    }


def build_industry_map():
    """从DB获取行业映射，失败时返回空映射"""
    try:
        mapping = {}
        raw = _db_get_industry_map()  # dict: bs_code -> industry
        for bs_code, industry in raw.items():
            code_raw = bs_code.replace('sh.', '').replace('sz.', '')
            if industry:
                mapping[code_raw] = industry
        return mapping
    except Exception:
        return {}


def get_sector_snapshot(pool, industry_map):
    """板块快照：按行业统计平均涨幅与上涨家数"""
    if not pool or not industry_map:
        return [], {}

    bucket = {}
    for d in pool:
        industry = industry_map.get(d['code'])
        if not industry:
            continue
        if industry not in bucket:
            bucket[industry] = {'count': 0, 'up': 0, 'sum_pct': 0.0}
        bucket[industry]['count'] += 1
        bucket[industry]['sum_pct'] += d['pct']
        if d['pct'] > 0:
            bucket[industry]['up'] += 1

    rows = []
    for industry, v in bucket.items():
        if v['count'] < 5:
            continue
        avg_pct = v['sum_pct'] / v['count']
        up_ratio = v['up'] / v['count']
        rows.append((industry, v['count'], avg_pct, up_ratio))

    rows.sort(key=lambda x: (x[2], x[3]), reverse=True)
    strength_map = {
        industry: {'count': count, 'avg_pct': avg_pct, 'up_ratio': up_ratio, 'rank': idx + 1}
        for idx, (industry, count, avg_pct, up_ratio) in enumerate(rows)
    }
    return rows[:10], strength_map


def score_buy_priority(score, grade, signals, pct_today, turn, ma5, ma10, ma20, vr520, dd60, mode, sector_meta=None):
    """第二阶段：买入优先级评分（用于回答优先买谁）"""
    p = 0
    reasons = []

    if grade == 'B':
        p += 3
        reasons.append('B级优先')
    elif grade == 'A':
        p += 1
        reasons.append('A级次优先')

    if 12 <= score <= 14:
        p += 2
        reasons.append('B低甜区')
    elif 15 <= score <= 17:
        p += 1
        reasons.append('B高次优')
    elif score >= 18:
        p -= 1
        reasons.append('A级拥挤')

    sig_count = len(signals.split('|')) if signals else 0
    if sig_count > 0:
        p += min(6, sig_count * 3)
        reasons.append('启动信号')

    if 1 <= pct_today <= 5:
        p += 2
        reasons.append('当日强势')
    elif 0 < pct_today < 1 or 5 < pct_today <= 7:
        p += 1
        reasons.append('当日偏强')
    elif pct_today < 0:
        p -= 2
        reasons.append('当日走弱')

    if 1 <= turn <= 6:
        p += 1
        reasons.append('换手健康')

    if ma5 > ma10 > ma20:
        p += 2
        reasons.append('均线多头')
    elif ma5 > ma10:
        p += 1
        reasons.append('短线转强')

    if 0.7 <= vr520 <= 1.3:
        p += 2
        reasons.append('量能适中')
    elif 0.4 <= vr520 <= 1.8:
        p += 1

    if dd60 < -18:
        p -= 1
    elif -15 <= dd60 <= -5:
        p += 2
        reasons.append('浅回撤优先')
    elif -18 <= dd60 < -15:
        p += 1
        reasons.append('中回撤可做')

    if sector_meta:
        if sector_meta['avg_pct'] >= 0.8 and sector_meta['up_ratio'] >= 0.6:
            p += 2
            reasons.append('板块联动')
        elif sector_meta['avg_pct'] > 0:
            p += 1
            reasons.append('板块偏强')

    if mode == '趋势':
        p += 1

    level = '高' if p >= 12 else ('中' if p >= 9 else '低')
    return p, level, reasons

# ═══ Step 1: 拉全市场 ═══
print("\n[1/3] 批量拉取全市场实时行情...")
all_codes = []
all_codes.extend(['sh000001', 'sz399001', 'sz399006'])
for start, end in [(600000, 602000), (603000, 604000), (605000, 605500)]:
    for c in range(start, end):
        all_codes.append(f"sh{c}")
for c in range(1, 3100):
    all_codes.append(f"sz{c:06d}")
t0 = time.time()
all_data = batch_fetch_qq(all_codes)
print(f"  获取 {len(all_data)} 只  耗时 {time.time()-t0:.1f}s")

market = get_market_snapshot(all_data)
print(f"  大盘快照: 上证{market['sh_pct']:+.2f}% 深成{market['sz_pct']:+.2f}% 创业板{market['cyb_pct']:+.2f}%  状态:{market['state']}  建议:{market['action']}")

# ═══ Step 2: 前置过滤（更宽松） ═══
print("[2/3] 前置过滤...")
pool = []
for sym, d in all_data.items():
    if 'ST' in d['name'] or 'st' in d['name'] or '退' in d['name']: continue
    if d['cur'] < 3 or d['cur'] > 200: continue
    if d['amt'] < 1500: continue  # 成交额>1500万
    if d['turn'] < 0.5 or d['turn'] > 10: continue
    # 今日涨幅：排除涨停和跌幅>3%的
    if d['pct'] > 9.5 or d['pct'] < -3: continue
    pool.append(d)

# 按涨幅分层取：优先2-6%区间（趋势确认不追高），其次0-2%（回踩），最后6-9%（强势）
tier1 = [d for d in pool if 2 <= d['pct'] <= 6]    # 主力区
tier2 = [d for d in pool if 0 <= d['pct'] < 2]     # 回踩区
tier3 = [d for d in pool if 6 < d['pct'] <= 9.5]   # 强势区
tier4 = [d for d in pool if -3 <= d['pct'] < 0]    # 逆势区

# 每层按成交额(资金关注度)排序取一批
tier1.sort(key=lambda x: -x['amt'])
tier2.sort(key=lambda x: -x['amt'])
tier3.sort(key=lambda x: -x['amt'])

candidates = tier1[:100] + tier2[:60] + tier3[:40]
print(f"  通过: {len(pool)} 只 → 取 {len(candidates)} 只分析K线")
print(f"  (2-6%区间:{len(tier1[:100])}只  0-2%回踩:{len(tier2[:60])}只  6-9%强势:{len(tier3[:40])}只)")

print("  正在计算板块热度...")
industry_map = build_industry_map()
sector_rows, sector_strength_map = get_sector_snapshot(pool, industry_map)
if sector_rows:
    print("  热门板块TOP5:")
    for i, row in enumerate(sector_rows[:5], 1):
        print(f"    {i}. {row[0]}  样本{row[1]}  均涨{row[2]:+.2f}%  上涨占比{row[3]*100:.0f}%")
else:
    print("  热门板块: 暂无法获取（不影响个股评分）")

# ═══ Step 3: K线分析评分 ═══
print("[3/3] 逐股K线分析...")
results_trend = []   # 趋势确认候选
results_charge = []  # 蓄力候选
errors = 0

for i, d in enumerate(candidates):
    if i % 30 == 0 and i > 0:
        print(f"  已分析 {i}/{len(candidates)}... 趋势:{len(results_trend)} 蓄力:{len(results_charge)}")

    klines = fetch_kline_sina(d['sym'], days=130)
    if not klines or len(klines) < 60:
        errors += 1; continue

    cls = np.array([k['close'] for k in klines])
    ops = np.array([k['open'] for k in klines])
    his = np.array([k['high'] for k in klines])
    los = np.array([k['low'] for k in klines])
    vols = np.array([k['volume'] for k in klines])
    n = len(cls)
    pcts = np.zeros(n)
    pcts[1:] = (cls[1:] - cls[:-1]) / cls[:-1] * 100
    cur = d['cur']

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])

    # ══════ 模式A: 趋势确认回踩 ══════
    # 条件：均线多头排列 + 今日表现适中 + 量价配合
    score_t = 0; td = []

    # A1: 均线多头（MA5>MA10>MA20>MA60 或接近）
    bullish_count = sum([ma5 > ma10, ma10 > ma20, ma20 > ma60, cur > ma5])
    if bullish_count >= 4:
        score_t += 5; td.append("均线多头✅")
    elif bullish_count >= 3:
        score_t += 3; td.append("均线多头⚠️")
    else:
        td.append("均线多头❌")

    # A2: 60日涨幅适中(10-50%)，说明趋势已启动但不过分
    pct60 = (cur - cls[-60]) / cls[-60] * 100 if len(cls) >= 60 else 0
    if 10 <= pct60 <= 50:
        score_t += 3; td.append(f"60日+{pct60:.0f}%✅")
    elif 5 <= pct60 <= 60:
        score_t += 1; td.append(f"60日+{pct60:.0f}%⚠️")
    else:
        td.append(f"60日{pct60:+.0f}%❌")

    # A3: 近期有过放量上涨（主力介入证据）
    max60 = np.max(cls[-60:])
    dd60 = (cur - max60) / max60 * 100

    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999

    # 近20日有过放量日(量>2倍均量)
    big_vol_days = np.sum(vols[-20:] > vol60 * 2) if vol60 > 0 else 0
    if big_vol_days >= 2:
        score_t += 3; td.append(f"放量{big_vol_days}天✅")
    elif big_vol_days >= 1:
        score_t += 1; td.append(f"放量{big_vol_days}天⚠️")
    else:
        td.append("无放量❌")

    # A4: 今日分时强度（开盘到现在是否走强）
    if d['cur'] > d['open'] and d['pct'] > 0:
        if d['low'] >= d['pre'] * 0.995:  # 全天未破昨收
            score_t += 3; td.append("分时强势✅")
        else:
            score_t += 1; td.append("分时偏强⚠️")
    else:
        td.append("分时弱❌")

    # A5: 外盘>内盘（主动买入为主）
    oi_ratio = d['outer'] / max(d['inner'], 1)
    if oi_ratio > 1.1:
        score_t += 2; td.append(f"外/内{oi_ratio:.2f}✅")
    elif oi_ratio > 0.95:
        score_t += 1; td.append(f"外/内{oi_ratio:.2f}⚠️")
    else:
        td.append(f"外/内{oi_ratio:.2f}❌")

    # A6: 回撤适度（从高点回落5-20%是好的买点）
    if -20 <= dd60 <= -5:
        score_t += 3; td.append(f"回撤{dd60:.1f}%✅")
    elif -30 <= dd60 <= -3:
        score_t += 1; td.append(f"回撤{dd60:.1f}%⚠️")
    else:
        td.append(f"回撤{dd60:.1f}%")

    # A7: 5日均价稳定（不是在暴涨暴跌中）
    rng5 = (np.max(cls[-5:]) - np.min(cls[-5:])) / np.mean(cls[-5:]) * 100
    if rng5 <= 8:
        score_t += 2; td.append(f"5日幅{rng5:.1f}%✅")
    else:
        td.append(f"5日幅{rng5:.1f}%❌")

    # 趋势信号
    t_signals = []
    # MA5金叉MA10
    if n >= 12:
        for j in [-1, -2, -3]:
            pm5 = np.mean(cls[j-5:j]); pm10 = np.mean(cls[j-10:j])
            cm5 = np.mean(cls[j-4:j+1]); cm10 = np.mean(cls[j-9:j+1])
            if pm5 <= pm10 and cm5 > cm10:
                t_signals.append("MA5金叉MA10"); break
    # 站上MA20
    if cls[-2] < ma20 and cur > ma20:
        t_signals.append("站上MA20")
    # 突破前高
    if n >= 15 and cur > np.max(cls[-15:-1]) and d['pct'] > 0:
        t_signals.append("突破前高")

    if score_t >= 12:
        grade_t = 'A' if score_t >= 18 else 'B'
        signal_t = '|'.join(t_signals) if t_signals else ''
        sector_meta = sector_strength_map.get(industry_map.get(d['code'], ''))
        bp_score, bp_level, bp_reasons = score_buy_priority(
            score_t, grade_t, signal_t, d['pct'], d['turn'], ma5, ma10, ma20, vr520, dd60, '趋势', sector_meta
        )
        results_trend.append({
            'sym': d['sym'], 'code': d['code'], 'name': d['name'],
            'price': cur, 'score': score_t, 'mode': '趋势',
            'grade': grade_t,
            'details': ' '.join(td),
            'signals': signal_t,
            'pct60': pct60, 'dd60': dd60, 'vr520': vr520, 'rng5': rng5,
            'pct_today': d['pct'],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'vol': d['vol'], 'amt': d['amt'], 'turn': d['turn'],
            'outer': d['outer'], 'inner': d['inner'],
            'buy_priority_score': bp_score,
            'buy_priority_level': bp_level,
            'buy_priority_reasons': bp_reasons,
            'industry': industry_map.get(d['code'], '未知'),
        })

    # ══════ 模式B: 蓄力洗盘 ══════（放宽版）
    if cur <= ma60: continue  # F5
    if not (5 <= pct60 <= 60): continue
    if not (-20 <= dd60 <= -5): continue
    limit_ups = np.sum(pcts[-60:] >= 9.5)
    if limit_ups < 1: continue
    if np.any(pcts[-5:] < -5): continue

    score_c = 0; cd = []

    # 缩量
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    vol_dec = vols[-1] < vols[-2] < vols[-3] if n >= 3 else False
    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, vol_dec])
    if sc1 >= 2: score_c += 5; cd.append("①缩量✅")
    elif sc1 >= 1: score_c += 1; cd.append("①缩量⚠️")
    else: cd.append("①缩量❌")

    # 横盘
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    sc2 = sum([rng5 <= 6, abs(cs) <= 2, cur > ma60])
    if sc2 >= 3: score_c += 4; cd.append("②横盘✅")
    elif sc2 >= 2: score_c += 2; cd.append("②横盘⚠️")
    else: cd.append("②横盘❌")

    # 均线收敛
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 4, cur > ma60, ma5 > ma10 or ma5/ma10 > 0.99])
    if sc3 >= 3: score_c += 4; cd.append("③均线✅")
    elif sc3 >= 2: score_c += 2; cd.append("③均线⚠️")
    else: cd.append("③均线❌")

    # 实体缩小
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.6, pct_abs5 <= 2])
    if sc4 >= 2: score_c += 3; cd.append("④实体✅")
    elif sc4 >= 1: score_c += 1; cd.append("④实体⚠️")
    else: cd.append("④实体❌")

    # 下影线
    lsb = sum(1 for j in range(-5,0) if cls[j]>ops[j] and abs(cls[j]-ops[j])>0 and (min(ops[j],cls[j])-los[j]) >= 2*abs(cls[j]-ops[j]))
    if lsb >= 1: score_c += 3; cd.append("⑤下影✅")
    else: cd.append("⑤下影—")

    if score_c >= 12:
        grade_c = 'A' if score_c >= 18 else 'B'
        sector_meta = sector_strength_map.get(industry_map.get(d['code'], ''))
        bp_score, bp_level, bp_reasons = score_buy_priority(
            score_c, grade_c, '', d['pct'], d['turn'], ma5, ma10, ma20, vr520, dd60, '蓄力', sector_meta
        )
        results_charge.append({
            'sym': d['sym'], 'code': d['code'], 'name': d['name'],
            'price': cur, 'score': score_c, 'mode': '蓄力',
            'grade': grade_c,
            'details': ' '.join(cd),
            'signals': '',
            'pct60': pct60, 'dd60': dd60, 'vr520': vr520, 'rng5': rng5,
            'pct_today': d['pct'],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'vol': d['vol'], 'amt': d['amt'], 'turn': d['turn'],
            'outer': d['outer'], 'inner': d['inner'],
            'buy_priority_score': bp_score,
            'buy_priority_level': bp_level,
            'buy_priority_reasons': bp_reasons,
            'industry': industry_map.get(d['code'], '未知'),
        })

    time.sleep(0.12)

# ═══ 输出结果 ═══
print(f"\n{'='*80}")
print(f"  选股结果  (K线失败: {errors})")
print(f"{'='*80}")
print(f"  大盘判定: {market['state']}  执行建议: {market['action']}")

# 合并两种模式结果并去重
all_results = results_trend + results_charge
best_by_code = {}
for r in all_results:
    old = best_by_code.get(r['code'])
    if old is None or (r['buy_priority_score'], r['score']) > (old['buy_priority_score'], old['score']):
        best_by_code[r['code']] = r
unique = list(best_by_code.values())
unique.sort(key=lambda x: (x['buy_priority_score'], x['score']), reverse=True)

print(f"\n  趋势确认候选: {len(results_trend)} 只")
print(f"  蓄力洗盘候选: {len(results_charge)} 只")
print(f"  合计去重后: {len(unique)} 只")
print()

if unique:
    header = f"{'#':>3s} {'模式':<4s} {'代码':<8s} {'名称':<10s} {'行业':<10s} {'价格':>7s} {'今涨':>7s} {'优先':>4s} {'级':>2s} {'分':>3s} {'级别':>2s} {'60日涨':>7s} {'回撤':>7s} {'量比':>6s} {'成交额':>10s}  指标"
    print(header)
    print("-" * 140)
    for rank, c in enumerate(unique[:25], 1):
        sig = f" >>{c['signals']}" if c['signals'] else ""
        print(f"{rank:>3d} {c['mode']:<4s} {c['code']:<8s} {c['name']:<10s} {c['industry']:<10.10s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['buy_priority_score']:>4d} {c['buy_priority_level']:>2s} {c['score']:>3d} {c['grade']:>2s} {c['pct60']:>+6.1f}% {c['dd60']:>+6.1f}% {c['vr520']:>5.2f} {c['amt']:>9,.0f}万  {c['details']}{sig}")

    print()
    print("=" * 80)
    print("  优先买谁 TOP10")
    print("=" * 80)
    print(f"{'#':>3s} {'代码':<8s} {'名称':<10s} {'行业':<10s} {'模式':<4s} {'优先':>4s} {'级':>2s} {'今涨':>7s} {'分':>3s} {'信号':<20s}  {'优先原因'}")
    print("-" * 140)
    for rank, c in enumerate(unique[:10], 1):
        reason = '、'.join(c['buy_priority_reasons']) if c['buy_priority_reasons'] else '-'
        signal = c['signals'] if c['signals'] else '-'
        print(f"{rank:>3d} {c['code']:<8s} {c['name']:<10s} {c['industry']:<10.10s} {c['mode']:<4s} {c['buy_priority_score']:>4d} {c['buy_priority_level']:>2s} {c['pct_today']:>+6.2f}% {c['score']:>3d} {signal:<20.20s}  {reason}")

    # 详细分析TOP 5
    print()
    print("=" * 80)
    print("  TOP 5 详细分析 + 操作计划")
    print("=" * 80)
    for rank, c in enumerate(unique[:5], 1):
        print(f"\n{'─'*60}")
        print(f"  #{rank} {c['name']}（{c['code']}）  [{c['mode']}模式]")
        print(f"{'─'*60}")
        print(f"  现价: {c['price']:.2f}  今日: {c['pct_today']:+.2f}%  换手: {c['turn']:.2f}%")
        print(f"  MA5={c['ma5']:.2f} MA10={c['ma10']:.2f} MA20={c['ma20']:.2f} MA60={c['ma60']:.2f}")
        print(f"  量比(5/20): {c['vr520']:.2f}  60日涨: {c['pct60']:+.1f}%  回撤: {c['dd60']:+.1f}%")
        print(f"  成交量: {c['vol']:,.0f}手  成交额: {c['amt']:,.0f}万")
        print(f"  外盘: {c['outer']:,.0f} 内盘: {c['inner']:,.0f}  外/内: {c['outer']/max(c['inner'],1):.2f}")
        print(f"  评分: {c['score']}  级别: {c['grade']}")
        print(f"  优先分: {c['buy_priority_score']}  优先级: {c['buy_priority_level']}  原因: {'、'.join(c['buy_priority_reasons']) if c['buy_priority_reasons'] else '-'}")
        print(f"  指标: {c['details']}")
        if c['signals']:
            print(f"  信号: {c['signals']}")
        entry = max(c['ma5'], c['ma10'])
        tp1 = c['price'] * 1.03; tp2 = c['price'] * 1.05
        stop = c['price'] * 0.98; hard = c['ma60'] * 0.98
        print(f"  ┌─ 操作计划 ─┐")
        print(f"  │ 入场参考: {entry:.2f} ~ {c['price']:.2f}")
        print(f"  │ 止盈1(+3%): {tp1:.2f}  止盈2(+5%): {tp2:.2f}")
        print(f"  │ 止损(-2%): {stop:.2f}  硬止损(破MA60): {hard:.2f}")
        if rank == 1:
            print(f"  │ 仓位建议: 若今日只买1支，最小仓位 {SINGLE_STOCK_MIN_POSITION:.0%}（即1/2仓）")
        else:
            print(f"  │ 仓位建议: 多标的并行时单票建议1/3仓，强弱不明先观察")
        print(f"  │ 最长持有: 2个交易日")
        print(f"  └──────────┘")
else:
    print("  （无符合条件的候选）")

print(f"\n完成 {datetime.now().strftime('%H:%M:%S')}")
