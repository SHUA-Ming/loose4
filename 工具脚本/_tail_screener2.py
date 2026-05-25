#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
尾盘选股器 v2 - 使用 baostock 股票列表 + QQ实时行情
"""
import sys, warnings, time, math
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

import requests
import numpy as np
from datetime import datetime, timedelta

print("=" * 70)
print(f"  尾盘选股器 v2  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ── Step 0: 大盘快照 ─────────────────────────────────────────────
def fetch_qq(syms_str):
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={syms_str}", timeout=10)
        resp.encoding = 'gbk'
        return resp.text
    except Exception as e:
        print(f"  [ERROR] {e}")
        return ""

def parse_line(line):
    """解析单条 qq 行情"""
    if '="' not in line or '~' not in line:
        return None
    try:
        idx = line.index('="') + 2
        items = line[idx:].rstrip('";').split('~')
        if len(items) < 38:
            return None
        def sf(i):
            try: return float(items[i])
            except: return 0.0
        return {
            'name': items[1], 'code': items[2],
            'cur': sf(3), 'pre': sf(4), 'open': sf(5),
            'hi': sf(33), 'lo': sf(34),
            'pct': sf(32), 'chg': sf(31),
            'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
            'outer': sf(7), 'inner': sf(8),
            'upd': items[30] if len(items) > 30 else '',
        }
    except:
        return None

def parse_batch(text):
    results = {}
    for line in text.strip().split('\n'):
        d = parse_line(line)
        if d and d['cur'] > 0:
            # 从行首提取完整 sym (如 v_sh600000)
            key = line.split('=')[0].replace('v_', '').replace('pv_', '').strip()
            results[key] = d
    return results

# 大盘
idx_raw = fetch_qq("sh000001,sz399001,sz399006,sh000300")
idx_data = parse_batch(idx_raw)

print("\n【大盘快照】")
sh_pct = 0.0
mkt_state = "中性"
for sym, label in [('sh000001','上证'), ('sz399001','深证'), ('sz399006','创业板'), ('sh000300','沪深300')]:
    d = idx_data.get(sym)
    if d:
        print(f"  {label}: {d['cur']:.2f}  ({d['pct']:+.2f}%)  "
              f"最高{d['hi']:.2f} 最低{d['lo']:.2f}  更新:{d['upd']}")
        if sym == 'sh000001':
            sh_pct = d['pct']

print()
if sh_pct <= -2.0:
    mkt_state = "恐慌"
    print("  ⛔ 大盘下跌>2%，今日不建议开仓！")
elif sh_pct <= -1.0:
    mkt_state = "偏弱"
    print("  ⚠️  大盘偏弱，轻仓或不操作")
elif sh_pct >= 2.0:
    mkt_state = "强势"
    print("  🚀 大盘大涨，可正常操作，注意不追高")
elif sh_pct >= 0.5:
    mkt_state = "偏强"
    print("  ✅ 大盘偏强，正常选股")
else:
    mkt_state = "中性"
    print("  ⚡ 大盘中性，精选个股")

# ── Step 1: 获取沪深主板股票列表 ─────────────────────────────────
print("\n【Step 1】获取沪深主板股票列表 (baostock)...")
import baostock as bs
lg = bs.login()

stock_list = []
for offset in range(0, 5):
    day = (datetime.now() - timedelta(days=offset)).strftime('%Y-%m-%d')
    rs = bs.query_all_stock(day=day)
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if rows:
        for row in rows:
            code = row[0]   # sh.600000
            status = row[1] # 1=上市
            name = row[2]
            if status != '1':
                continue
            if not (code.startswith('sh.60') or code.startswith('sz.00')):
                continue
            if 'ST' in name or 'st' in name or '*' in name:
                continue
            # 转换为qq格式: sh600000 -> sh600000
            sym = code.replace('.', '')
            stock_list.append((sym, name))
        if stock_list:
            print(f"  使用日期 {day}，共 {len(stock_list)} 只主板股票")
            break

bs.logout()

if not stock_list:
    print("  无法获取股票列表，退出")
    sys.exit(1)

# ── Step 2: 批量拉取实时行情 ─────────────────────────────────────
print(f"\n【Step 2】批量拉取实时行情 (每批80只)...")

all_quotes = {}
BATCH = 80
total_batches = math.ceil(len(stock_list) / BATCH)

for i in range(0, len(stock_list), BATCH):
    batch = stock_list[i:i+BATCH]
    syms_str = ','.join([s[0] for s in batch])
    text = fetch_qq(syms_str)
    parsed = parse_batch(text)
    all_quotes.update(parsed)
    done = min(i+BATCH, len(stock_list))
    if (i // BATCH) % 10 == 0:
        print(f"  进度: {done}/{len(stock_list)}")
    time.sleep(0.05)

print(f"  成功解析 {len(all_quotes)} 条行情")

# ── Step 3: 筛选候选股 ───────────────────────────────────────────
print("\n【Step 3】筛选尾盘候选...")

candidates = []

for sym, name in stock_list:
    d = all_quotes.get(sym)
    if not d:
        continue

    cur   = d['cur']
    pre   = d['pre']
    hi    = d['hi']
    lo    = d['lo']
    pct   = d['pct']
    turn  = d['turn']
    amt   = d['amt']  # 万
    outer = d['outer']
    inner = d['inner']

    # 基本有效性
    if cur <= 0 or pre <= 0 or hi <= 0 or lo <= 0:
        continue
    if any(math.isnan(x) for x in [cur, pre, hi, lo, pct, turn, amt]):
        continue

    # 价格范围 5~150
    if not (5 <= cur <= 150):
        continue

    # 成交额 > 3000万
    if amt < 3000:
        continue

    # 换手率 0.3~6%
    if not (0.3 <= turn <= 6.0):
        continue

    # 涨幅 0%~6%（不追涨停）
    if not (0.0 <= pct <= 6.0):
        continue

    # 振幅
    amp = (hi - lo) / pre * 100 if pre > 0 else 0
    if not (1.0 <= amp <= 9.0):
        continue

    # 尾盘距离今日最高回落幅度（≤3.5% 说明没有尾盘抛压）
    from_hi = (hi - cur) / hi * 100 if hi > 0 else 0
    if from_hi > 4.0:
        continue

    # 外盘/内盘比（资金偏流入）
    oi_ratio = outer / max(inner, 1)

    # 评分
    score = 0

    # 涨幅 1~4% 最优（有趋势但不过热）
    if 1.0 <= pct <= 4.0:
        score += 4
    elif 0 <= pct < 1.0:
        score += 1
    elif 4.0 < pct <= 6.0:
        score += 2

    # 距高点 ≤1% 尾盘强势
    if from_hi <= 1.0:
        score += 4
    elif from_hi <= 2.0:
        score += 3
    elif from_hi <= 3.0:
        score += 2
    else:
        score += 1

    # 换手率适中
    if 1.0 <= turn <= 3.5:
        score += 3
    elif 0.5 <= turn < 1.0 or 3.5 < turn <= 5.0:
        score += 1

    # 成交额 ≥ 1亿
    if amt >= 10000:  # 1亿=10000万
        score += 2
    elif amt >= 5000:
        score += 1

    # 外/内比 ≥ 1.1（资金净流入）
    if oi_ratio >= 1.2:
        score += 2
    elif oi_ratio >= 1.0:
        score += 1

    # 振幅适中 2~6%
    if 2.0 <= amp <= 6.0:
        score += 1

    candidates.append({
        'sym': sym, 'code': sym[2:], 'name': name,
        'cur': cur, 'pct': pct, 'amp': amp,
        'from_hi': from_hi, 'turn': turn,
        'amt_yi': amt / 10000,
        'oi_ratio': oi_ratio,
        'score': score,
    })

candidates.sort(key=lambda x: x['score'], reverse=True)
print(f"  候选股共 {len(candidates)} 只")

# ── Step 4: 输出 TOP20 ───────────────────────────────────────────
print("\n" + "=" * 70)
print(f"  【尾盘候选 TOP20】  市场状态: {mkt_state}  上证涨跌: {sh_pct:+.2f}%")
print("=" * 70)
print(f"  {'代码(全)':<12} {'名称':<10} {'现价':>7} {'涨幅':>7} {'振幅':>6} "
      f"{'距高':>6} {'换手':>6} {'成交额':>7} {'外/内':>6} {'分':>4}")
print("-" * 75)

for c in candidates[:20]:
    code_full = ('sh.' if c['code'].startswith('6') else 'sz.') + c['code']
    print(f"  {code_full:<12} {c['name']:<10} {c['cur']:>7.2f} "
          f"{c['pct']:>+6.2f}% {c['amp']:>5.1f}% {c['from_hi']:>5.1f}% "
          f"{c['turn']:>5.2f}% {c['amt_yi']:>6.2f}亿 {c['oi_ratio']:>5.2f}  {c['score']:>3}")

print()

# ── Step 5: TOP5 K线验证 ─────────────────────────────────────────
print("【Step 5】TOP5 候选 K线均线验证...")

def fetch_kline(code_prefix, days=30):
    try:
        url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={code_prefix}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if not data or not isinstance(data, list):
            return None
        return [{'date': k['day'],
                 'close': float(k['close']), 'open': float(k['open']),
                 'high': float(k['high']), 'low': float(k['low']),
                 'volume': float(k.get('volume', 0))} for k in data]
    except:
        return None

print("\n" + "=" * 70)
print("  【重点候选 - 均线结构分析】")
print("=" * 70)

top_stocks = []
for c in candidates[:5]:
    code = c['code']
    prefix = 'sh' if code.startswith('6') else 'sz'
    kdata = fetch_kline(prefix + code, 30)

    ma5 = ma10 = ma20 = None
    vol_ratio = None
    signals = []
    ma_trend = ""

    if kdata and len(kdata) >= 20:
        closes = np.array([d['close'] for d in kdata])
        volumes = np.array([d['volume'] for d in kdata])
        highs  = np.array([d['high'] for d in kdata])
        lows   = np.array([d['low'] for d in kdata])
        ma5  = float(np.mean(closes[-5:]))
        ma10 = float(np.mean(closes[-10:]))
        ma20 = float(np.mean(closes[-20:]))

        avg_vol = float(np.mean(volumes[-10:])) if len(volumes) >= 10 else 1
        today_vol = float(volumes[-1])
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

        cur = c['cur']

        # 均线判断
        if ma5 > ma10 > ma20:
            signals.append("✅均线多头")
            ma_trend = "多头"
        elif ma5 > ma20:
            signals.append("⚡均线初多")
            ma_trend = "初多"
        else:
            signals.append("⚠️均线偏弱")
            ma_trend = "弱"

        # 量比
        if 0.4 <= vol_ratio <= 0.85:
            signals.append("✅缩量洗盘")
        elif vol_ratio <= 1.2:
            signals.append("⚡量持平")
        else:
            signals.append("⬆️放量")

        # 价格相对均线
        if cur > ma5:
            signals.append(f"✅价>{ma5:.2f}(MA5)")
        elif cur > ma10:
            signals.append(f"⚡价>{ma10:.2f}(MA10)")
        elif cur > ma20:
            signals.append(f"⚠️价>{ma20:.2f}(MA20)")
        else:
            signals.append("❌价<MA20")

        # 近期是否创新高
        recent_hi = float(np.max(highs[-5:]))
        if kdata[-1]['high'] >= recent_hi:
            signals.append("⬆️5日新高")

    code_full = ('sh.' if code.startswith('6') else 'sz.') + code
    print(f"\n  ★ {code_full}  {c['name']}")
    print(f"     现价: {c['cur']:.2f}  涨幅: {c['pct']:+.2f}%  评分: {c['score']}")
    if ma5:
        print(f"     MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  量比={vol_ratio:.2f}")
    print(f"     信号: {' | '.join(signals) if signals else '数据不足'}")
    print(f"     振幅{c['amp']:.1f}% | 距高{c['from_hi']:.1f}% | 换手{c['turn']:.2f}% | "
          f"成交{c['amt_yi']:.2f}亿 | 外/内{c['oi_ratio']:.2f}")

    # 止盈止损位
    entry = c['cur']
    tp1 = round(entry * 1.03, 2)
    tp2 = round(entry * 1.05, 2)
    sl_hard = round(entry * 0.97, 2)
    sl_soft = round(entry * 0.985, 2)
    print(f"     入场参考: {entry:.2f}")
    print(f"     止盈1(+3%): {tp1:.2f}  止盈2(+5%): {tp2:.2f}")
    print(f"     软止损(-1.5%收盘): {sl_soft:.2f}  硬止损(-3%盘中): {sl_hard:.2f}")

    top_stocks.append({**c, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'vol_ratio': vol_ratio,
                        'signals': signals, 'ma_trend': ma_trend})
    time.sleep(0.3)

print()
print("=" * 70)
print(f"  市场状态: {mkt_state}  上证: {sh_pct:+.2f}%")
if sh_pct <= -1.0:
    print("  ⚠️  当前大盘偏弱，以上候选仅供参考，建议轻仓或观望！")
else:
    print("  操作建议: 选择均线多头+缩量+评分最高的1~2只，尾盘最后15分钟分批买入")
    print("  仓位控制: 单票不超过总资金30%，设好止损后再入场")
print(f"\n  完成: {datetime.now().strftime('%H:%M:%S')}")
print("=" * 70)
