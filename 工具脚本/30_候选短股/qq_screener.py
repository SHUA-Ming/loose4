#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于腾讯行情HTTP接口的实时选股器
不走HTTPS代理，速度快且稳定
"""
import sys, warnings, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

import requests
import numpy as np
from datetime import datetime, timedelta

print("=" * 80)
print(f"  腾讯行情 实时选股器  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 80)

# ═══ 腾讯行情批量接口 ═══
def batch_fetch_qq(codes, batch_size=80):
    """批量拉取腾讯实时行情，返回dict: code -> parsed data"""
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
                if len(items) < 45:
                    continue

                def sf(j):
                    try: return float(items[j])
                    except: return 0.0

                code_raw = items[2]  # e.g. 600000
                name = items[1]
                cur = sf(3); pre = sf(4)
                opn = sf(5); hi = sf(33); lo = sf(34)
                pct = sf(32); vol = sf(36); amt = sf(37)
                turn = sf(38)
                outer = sf(7); inner = sf(8)

                if cur <= 0 or pre <= 0:
                    continue

                sym = batch[0][:2]  # sh or sz prefix from query
                # reconstruct prefix from the batch
                for b in batch:
                    if code_raw in b:
                        sym = b[:2]
                        break

                results[f"{sym}{code_raw}"] = {
                    'code': code_raw, 'name': name, 'sym': f"{sym}{code_raw}",
                    'cur': cur, 'pre': pre, 'open': opn, 'high': hi, 'low': lo,
                    'pct': pct, 'vol': vol, 'amt': amt, 'turn': turn,
                    'outer': outer, 'inner': inner,
                }
            except:
                continue
        time.sleep(0.1)  # 控制频率
    return results

# ═══ 新浪日K线接口（HTTP，不走代理） ═══
def fetch_daily_kline_sina(code, days=120):
    """
    新浪K线: http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
    code: sh600519 或 sz000001
    返回: list of dict {date, open, close, high, low, volume}
    """
    try:
        url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if not data or not isinstance(data, list):
            return None
        result = []
        for k in data:
            result.append({
                'date': k['day'],
                'open': float(k['open']),
                'close': float(k['close']),
                'high': float(k['high']),
                'low': float(k['low']),
                'volume': float(k['volume']) if k.get('volume') else 0,
            })
        return result
    except Exception as e:
        return None

# ═══ Step 1: 生成全市场股票代码 ═══
print("\n[1/4] 生成全A股票代码...")
all_codes = []
# 上海主板: 600000-601999, 603000-603999, 605000-605499
for prefix in ['sh']:
    for start, end in [(600000, 602000), (603000, 604000), (605000, 605500)]:
        for c in range(start, end):
            all_codes.append(f"{prefix}{c}")
# 深圳主板+中小板: 000001-003100
for prefix in ['sz']:
    for start, end in [(1, 3100)]:
        for c in range(start, end):
            all_codes.append(f"{prefix}{c:06d}")

print(f"  生成 {len(all_codes)} 个代码，开始批量拉取行情...")

# ═══ Step 2: 批量拉取实时行情 ═══
print("[2/4] 批量拉取实时行情...")
t0 = time.time()
all_data = batch_fetch_qq(all_codes)
t1 = time.time()
print(f"  获取到 {len(all_data)} 只有效股票  耗时 {t1-t0:.1f}s")

# ═══ Step 3: 前置过滤 ═══
print("[3/4] 前置过滤 + 逐股K线分析...")
candidates = []
for sym, d in all_data.items():
    # 排除ST
    if 'ST' in d['name'] or 'st' in d['name'] or '退' in d['name']:
        continue
    # 价格范围 3~200
    if d['cur'] < 3 or d['cur'] > 200:
        continue
    # 成交额 > 1000万
    if d['amt'] < 1000:
        continue
    # 换手率 0.3~8%
    if d['turn'] < 0.3 or d['turn'] > 8:
        continue
    # 今日涨幅 -1%~7%（尾盘买不追涨停）
    if d['pct'] < -1 or d['pct'] > 7:
        continue
    candidates.append(d)

# 按接近3%涨幅优先排序（趋势确认但不追高）
candidates.sort(key=lambda x: -abs(x['pct'] - 3))
# 取前150只做K线分析
candidates = candidates[:150]
print(f"  前置过滤通过: {len(candidates)} 只，取前150分析K线")

# ═══ Step 4: 逐股K线评分 ═══
results = []
errors = 0
for i, d in enumerate(candidates):
    if i % 20 == 0 and i > 0:
        print(f"  已分析 {i}/{len(candidates)} 只... 候选 {len(results)} 只")

    klines = fetch_daily_kline_sina(d['sym'], days=130)
    if not klines or len(klines) < 60:
        errors += 1
        continue

    cls = np.array([k['close'] for k in klines])
    ops = np.array([k['open'] for k in klines])
    his = np.array([k['high'] for k in klines])
    los = np.array([k['low'] for k in klines])
    vols = np.array([k['volume'] for k in klines])
    n = len(cls)

    # 计算涨跌幅
    pcts = np.zeros(n)
    pcts[0] = 0
    pcts[1:] = (cls[1:] - cls[:-1]) / cls[:-1] * 100

    # 计算换手率近似（用量比变化）
    cur_price = d['cur']

    # ── 前置条件 F1-F5 ──
    ma60 = np.mean(cls[-60:])
    if cur_price <= ma60:
        continue  # F5

    c60 = cls[-60:]
    pct60 = (cur_price - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60):
        continue  # F3

    max60 = np.max(c60)
    dd60 = (cur_price - max60) / max60 * 100
    if not (-20 <= dd60 <= -5):
        continue  # F4

    # 60日内涨停
    p60 = pcts[-60:]
    limit_ups = np.sum(p60 >= 9.5)
    if limit_ups < 1:
        continue  # F2

    # 安全检查
    if np.any(pcts[-5:] < -5):
        continue
    # 高换手排除（用最近5日成交量变化判断）

    # ── 核心指标评分 ──
    score = 0
    details = []

    # ① 缩量
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    vol_min60 = np.min(vols[-60:])
    floor_vol = vols[-1] <= vol_min60 * 1.2 if vol_min60 > 0 else False

    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, vol_dec, floor_vol])
    if sc1 >= 3:
        score += 5; details.append("①缩量✅")
    elif sc1 >= 1:
        score += 1; details.append("①缩量⚠️")
    else:
        details.append("①缩量❌")

    # ② 横盘
    c5 = cls[-5:]
    rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])

    sc2 = sum([rng5 <= 5, abs(cs) <= 1, cur_price > ma60])
    if sc2 >= 3:
        score += 4; details.append("②横盘✅")
    elif sc2 >= 2:
        score += 2; details.append("②横盘⚠️")
    else:
        details.append("②横盘❌")

    # ③ 均线收敛
    ma_sp = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, cur_price > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3:
        score += 4; details.append("③均线✅")
    elif sc3 >= 2:
        score += 2; details.append("③均线⚠️")
    else:
        details.append("③均线❌")

    # ④ 实体缩小
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 2:
        score += 3; details.append("④实体✅")
    elif sc4 >= 1:
        score += 1; details.append("④实体⚠️")
    else:
        details.append("④实体❌")

    # ⑤ 下影线
    lsb = 0
    for j in range(-5, 0):
        o, c, h, l = ops[j], cls[j], his[j], los[j]
        body = abs(c - o)
        ls_len = min(o, c) - l
        if c > o and body > 0 and ls_len >= 2 * body and pcts[j] <= 2:
            lsb += 1
    if lsb >= 1:
        score += 3; details.append("⑤下影✅")
    else:
        details.append("⑤下影—")

    # ⑥ 十字星
    doji = 0
    for j in range(-5, 0):
        o, c, h, l = ops[j], cls[j], his[j], los[j]
        body = abs(c - o)
        bp = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if bp <= 0.5 and body > 0 and shadow >= 2 * body:
            doji += 1
    if doji >= 2:
        score += 2; details.append("⑥十字✅")
    elif doji >= 1:
        score += 1; details.append("⑥十字⚠️")
    else:
        details.append("⑥十字—")

    # ⑦ 红绿交替
    colors = ['R' if cls[j] >= ops[j] else 'G' for j in range(-5, 0)]
    no3 = all(not (colors[j] == colors[j+1] == colors[j+2]) for j in range(3))
    pct5r = all(-2 <= pcts[j] <= 2 for j in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3:
        score += 2; details.append("⑦交替✅")
    else:
        details.append("⑦交替❌")

    # ── 信号检测 ──
    signals = []
    if n >= 12:
        for j in [-1, -2, -3]:
            pm5 = np.mean(cls[j-5:j])
            pm10 = np.mean(cls[j-10:j])
            cm5 = np.mean(cls[j-4:j+1])
            cm10 = np.mean(cls[j-9:j+1])
            if pm5 <= pm10 and cm5 > cm10:
                signals.append("MA5金叉MA10")
                break
    if vols[-1] >= vol5 * 2 and pcts[-1] > 3:
        signals.append("放量突破")
    if n >= 15 and cls[-1] > np.max(cls[-15:-1]) and pcts[-1] > 0:
        signals.append("突破横盘上沿")

    grade = 'A' if score >= 18 else 'B' if score >= 12 else 'C'

    if score >= 12:
        results.append({
            'sym': d['sym'], 'code': d['code'], 'name': d['name'],
            'price': cur_price, 'score': score, 'grade': grade,
            'details': ' '.join(details),
            'signals': '|'.join(signals) if signals else '',
            'vr520': vr520, 'ma_sp': ma_sp, 'rng5': rng5,
            'pct60': pct60, 'dd60': dd60, 'cs': cs,
            'pct_today': d['pct'],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'vol': d['vol'], 'amt': d['amt'], 'turn': d['turn'],
            'outer': d['outer'], 'inner': d['inner'],
        })

    time.sleep(0.15)  # 控制频率

# ═══ 输出结果 ═══
print(f"\n{'='*80}")
print(f"  选股结果")
print(f"{'='*80}")
results.sort(key=lambda x: x['score'], reverse=True)

print(f"\n  通过筛选: {len(results)} 只  (K线获取失败: {errors})")
print()
if results:
    header = f"{'排名':>4s} {'代码':<8s} {'名称':<10s} {'价格':>7s} {'今涨':>7s} {'评分':>4s} {'级':>2s} {'量比5/20':>8s} {'均线距':>6s} {'5日幅':>6s} {'60日涨':>7s} {'回撤':>7s}  指标明细"
    print(header)
    print("-" * 150)
    for rank, c in enumerate(results[:20], 1):
        sig = f" >> {c['signals']}" if c['signals'] else ""
        print(f"{rank:>4d} {c['code']:<8s} {c['name']:<10s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['score']:>4d} {c['grade']:>2s} {c['vr520']:>8.2f} {c['ma_sp']:>5.2f}% {c['rng5']:>5.2f}% {c['pct60']:>+6.1f}% {c['dd60']:>+6.1f}%  {c['details']}{sig}")

    print()
    print("=" * 80)
    print("  TOP 5 蓄力候选 详细分析")
    print("=" * 80)
    for rank, c in enumerate(results[:5], 1):
        print(f"\n  ── #{rank} {c['name']}（{c['code']}）──")
        print(f"  腾讯代码: {c['sym']}  现价: {c['price']:.2f}  今日: {c['pct_today']:+.2f}%")
        print(f"  MA5={c['ma5']:.2f} MA10={c['ma10']:.2f} MA20={c['ma20']:.2f} MA60={c['ma60']:.2f}")
        print(f"  量比(5/20): {c['vr520']:.2f}  均线间距: {c['ma_sp']:.2f}%")
        print(f"  5日波幅: {c['rng5']:.2f}%  重心偏移: {c['cs']:+.2f}%")
        print(f"  60日涨幅: {c['pct60']:+.1f}%  高点回撤: {c['dd60']:+.1f}%")
        print(f"  成交量: {c['vol']:,.0f}手  成交额: {c['amt']:,.0f}万  换手: {c['turn']:.2f}%")
        print(f"  外盘: {c['outer']:,.0f}  内盘: {c['inner']:,.0f}  外/内比: {c['outer']/max(c['inner'],1):.2f}")
        print(f"  评分: {c['score']}/23 ({c['grade']}级)")
        print(f"  指标: {c['details']}")
        if c['signals']:
            print(f"  信号: {c['signals']}")
        entry = max(c['ma5'], c['ma10'])
        tp1 = c['price'] * 1.03; tp2 = c['price'] * 1.05
        stop = c['price'] * 0.98; hard_stop = c['ma60'] * 0.98
        print(f"  ── 操作计划 ──")
        print(f"  入场参考: {entry:.2f} (均线支撑)  现价: {c['price']:.2f}")
        print(f"  止盈1(+3%): {tp1:.2f}  止盈2(+5%): {tp2:.2f}")
        print(f"  止损(-2%): {stop:.2f}  硬止损(破MA60): {hard_stop:.2f}")
else:
    print("  （无符合条件的蓄力候选）")

print(f"\n分析完成。{datetime.now().strftime('%H:%M:%S')}")
