#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
尾盘选股器 - 实时筛查适合尾盘买入的股票
逻辑：今日缩量整理、价格在均线支撑附近、不追涨停、当前仍在窗口价位
"""
import sys, warnings, time, math
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

import requests
import numpy as np
from datetime import datetime

print("=" * 70)
print(f"  尾盘选股器  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 70)

# ── Step 0: 大盘快照 ──────────────────────────────────────────────────
def fetch_qq(syms_str):
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={syms_str}", timeout=10)
        resp.encoding = 'gbk'
        return resp.text
    except Exception as e:
        print(f"  [ERROR] 拉数据失败: {e}")
        return ""

def parse_qq(text):
    results = {}
    for line in text.strip().split('\n'):
        if '="' not in line or '~' not in line:
            continue
        try:
            idx = line.index('="') + 2
            items = line[idx:].rstrip('";').split('~')
            if len(items) < 45:
                continue
            def sf(i):
                try: return float(items[i])
                except: return 0.0
            sym_raw = items[2]
            # detect exchange prefix from line key
            prefix = 'sh' if line.startswith('v_sh') or line.startswith('v_pv_sh') else 'sz'
            if 'sh' + sym_raw in line[:20] or line[:6] == 'v_sh' + sym_raw[:1]:
                prefix = 'sh'
            else:
                prefix = 'sz'
            # simpler: detect from line start
            key_part = line.split('=')[0].replace('v_','').replace('pv_','').strip()
            if key_part.startswith('sh'):
                prefix = 'sh'
            elif key_part.startswith('sz'):
                prefix = 'sz'

            results[f"{prefix}{sym_raw}"] = {
                'name': items[1],
                'cur': sf(3), 'pre': sf(4), 'open': sf(5),
                'hi': sf(33), 'lo': sf(34),
                'pct': sf(32), 'chg': sf(31),
                'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
                'outer': sf(7), 'inner': sf(8),
                'upd': items[30] if len(items) > 30 else '',
            }
        except:
            continue
    return results

# 大盘指数
idx_raw = fetch_qq("sh000001,sz399001,sz399006,sh000300")
idx_data = parse_qq(idx_raw)

print("\n【大盘快照】")
market_ok = True
sh_pct = 0.0
for sym, label in [('sh000001','上证'), ('sz399001','深证'), ('sz399006','创业板'), ('sh000300','沪深300')]:
    d = idx_data.get(sym)
    if d:
        print(f"  {label}: {d['cur']:.2f}  ({d['pct']:+.2f}%)  最高{d['hi']:.2f} 最低{d['lo']:.2f}  更新:{d['upd']}")
        if sym == 'sh000001':
            sh_pct = d['pct']

if sh_pct <= -2.0:
    print("\n  ⛔ 大盘恐慌下跌 >2%，今日不建议开新仓！")
    market_ok = False
elif sh_pct <= -1.0:
    print("\n  ⚠️  大盘偏弱 >1%，只能轻仓试错，严格控制")
    market_ok = True
elif sh_pct >= 1.0:
    print("\n  ✅ 大盘偏强，可正常操作")
else:
    print("\n  ⚡ 大盘中性，精选个股")

# ── Step 1: 拉全市场行情（东财接口，akshare封装的底层）──────────────
print("\n【Step 1】拉取全市场实时行情...")
try:
    import akshare as ak
    df_all = ak.stock_zh_a_spot_em()
    print(f"  获取到 {len(df_all)} 只股票")
except Exception as e:
    print(f"  akshare失败，改用新浪接口: {e}")
    df_all = None

if df_all is None:
    print("  无法获取全市场数据，退出")
    sys.exit(1)

import pandas as pd

df = df_all.copy()
# 列名标准化
df.columns = [c.strip() for c in df.columns]
print(f"  列名: {list(df.columns[:15])}")

# 找关键列
code_col = '代码' if '代码' in df.columns else df.columns[0]
name_col = '名称' if '名称' in df.columns else df.columns[1]
price_col = next((c for c in ['最新价','现价','价格'] if c in df.columns), None)
pct_col   = next((c for c in ['涨跌幅','涨跌率'] if c in df.columns), None)
amt_col   = next((c for c in ['成交额'] if c in df.columns), None)
turn_col  = next((c for c in ['换手率'] if c in df.columns), None)
vol_col   = next((c for c in ['成交量'] if c in df.columns), None)
hi_col    = next((c for c in ['最高','今日最高'] if c in df.columns), None)
lo_col    = next((c for c in ['最低','今日最低'] if c in df.columns), None)
pre_col   = next((c for c in ['昨收','昨日收盘'] if c in df.columns), None)
mc_col    = next((c for c in ['流通市值'] if c in df.columns), None)

print(f"  价格列={price_col}, 涨跌幅列={pct_col}, 成交额列={amt_col}")

# ── Step 2: 前置过滤 ──────────────────────────────────────────────
print("\n【Step 2】前置过滤...")

# 只要 60xxxx (沪主板) 和 00xxxx (深主板)
df = df[df[code_col].astype(str).str.match(r'^(60|00)\d{4}$')]
print(f"  主板过滤后: {len(df)}")

# 排除 ST
df = df[~df[name_col].astype(str).str.contains(r'ST|st|\*ST|退', regex=True, na=False)]
print(f"  排除ST后: {len(df)}")

# 数值转换
for col in [price_col, pct_col, amt_col, turn_col, vol_col, hi_col, lo_col, pre_col]:
    if col and col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')

if mc_col:
    df[mc_col] = pd.to_numeric(df[mc_col], errors='coerce')

# 价格 5~150
if price_col:
    df = df[(df[price_col] >= 5) & (df[price_col] <= 150)]
    print(f"  价格5~150过滤后: {len(df)}")

# 成交额 > 5000万（有交易活跃度）
if amt_col:
    df = df[df[amt_col] >= 5000e4]
    print(f"  成交额>5000万过滤后: {len(df)}")

# 换手率 0.3%~8%
if turn_col:
    df = df[(df[turn_col] >= 0.3) & (df[turn_col] <= 8.0)]
    print(f"  换手率0.3~8%过滤后: {len(df)}")

# 流通市值 20亿~500亿
if mc_col:
    df = df[(df[mc_col] >= 20e8) & (df[mc_col] <= 500e8)]
    print(f"  流通市值过滤后: {len(df)}")

# 今日涨幅过滤：-5% ~ +6%（排除涨停、跌停、追高）
if pct_col:
    df = df[(df[pct_col] >= -5.0) & (df[pct_col] <= 6.0)]
    print(f"  涨幅-5~+6%过滤后: {len(df)}")

# ── Step 3: 尾盘特定条件 ─────────────────────────────────────────
print("\n【Step 3】尾盘条件筛选...")

candidates = []

if price_col and pct_col and hi_col and lo_col and pre_col and turn_col:
    for _, row in df.iterrows():
        try:
            code = str(row[code_col])
            name = str(row[name_col])
            cur  = float(row[price_col])
            pct  = float(row[pct_col])
            hi   = float(row[hi_col])
            lo   = float(row[lo_col])
            pre  = float(row[pre_col])
            turn = float(row[turn_col]) if turn_col else 0
            amt  = float(row[amt_col]) if amt_col else 0

            if any(math.isnan(x) for x in [cur, pct, hi, lo, pre]):
                continue

            # 振幅
            amp = (hi - lo) / pre * 100 if pre > 0 else 0

            # 距离最高点回落幅度（尾盘缩量整理特征）
            from_hi = (hi - cur) / hi * 100 if hi > 0 else 0

            # 条件1: 今日涨幅 0~5%（温和上涨，有动力但不追高）
            if not (0.0 <= pct <= 5.0):
                continue

            # 条件2: 振幅适中 1.5~8%（有波动空间）
            if not (1.5 <= amp <= 8.0):
                continue

            # 条件3: 尾盘从高点回落 ≤3%（没有大幅甩卖）
            if from_hi > 3.5:
                continue

            # 条件4: 换手率 0.5~5%（有换手但不过热）
            if not (0.5 <= turn <= 5.0):
                continue

            # 评分
            score = 0

            # 涨幅温和加分（1~3%最优）
            if 1.0 <= pct <= 3.0:
                score += 3
            elif 0 <= pct < 1.0 or 3.0 < pct <= 5.0:
                score += 1

            # 尾盘离高点越近越好
            if from_hi <= 1.0:
                score += 3
            elif from_hi <= 2.0:
                score += 2
            else:
                score += 1

            # 换手率适中最优
            if 1.0 <= turn <= 3.0:
                score += 2
            else:
                score += 1

            # 成交额适中
            if amt >= 1e8:   # 1亿以上
                score += 2
            elif amt >= 5000e4:
                score += 1

            candidates.append({
                'code': code,
                'name': name,
                'cur': cur,
                'pct': pct,
                'amp': amp,
                'from_hi': from_hi,
                'turn': turn,
                'amt': amt / 1e8,  # 转亿
                'score': score,
            })
        except:
            continue

candidates.sort(key=lambda x: x['score'], reverse=True)
print(f"  候选股数量: {len(candidates)}")

# ── Step 4: 输出 TOP20 ───────────────────────────────────────────
print("\n" + "=" * 70)
print("  【尾盘候选股 TOP20】  按综合评分排序")
print("=" * 70)
print(f"  {'代码':<8} {'名称':<10} {'现价':>7} {'涨幅':>7} {'振幅':>6} {'距高':>6} {'换手':>6} {'成交额':>7} {'分'}")
print("-" * 70)

for i, c in enumerate(candidates[:20], 1):
    code_display = ('sh.' if c['code'].startswith('6') else 'sz.') + c['code']
    print(f"  {code_display:<10} {c['name']:<10} {c['cur']:>7.2f} {c['pct']:>+6.2f}% "
          f"{c['amp']:>5.1f}% {c['from_hi']:>5.1f}% {c['turn']:>5.2f}% "
          f"{c['amt']:>6.2f}亿  {c['score']}")

print()

# ── Step 5: 对 TOP5 拉K线深度验证 ─────────────────────────────────
print("【Step 5】对 TOP5 候选拉历史K线验证均线结构...")

def fetch_kline_sina(code_with_prefix, days=30):
    """拉取新浪日K数据"""
    try:
        url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={code_with_prefix}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if not data or not isinstance(data, list):
            return None
        closes = [float(k['close']) for k in data]
        volumes = [float(k.get('volume', 0)) for k in data]
        days_data = [{'date': k['day'], 'close': float(k['close']),
                      'open': float(k['open']), 'high': float(k['high']),
                      'low': float(k['low']), 'volume': float(k.get('volume',0))} for k in data]
        return days_data
    except:
        return None

print()
print("=" * 70)
print("  【重点关注股 - 带均线分析】")
print("=" * 70)

for c in candidates[:5]:
    code = c['code']
    code_with_prefix = ('sh' if code.startswith('6') else 'sz') + code
    kdata = fetch_kline_sina(code_with_prefix, 30)

    ma5 = ma10 = ma20 = None
    vol_ratio = None
    signal = []

    if kdata and len(kdata) >= 20:
        closes = np.array([d['close'] for d in kdata])
        volumes = np.array([d['volume'] for d in kdata])
        ma5  = np.mean(closes[-5:])
        ma10 = np.mean(closes[-10:])
        ma20 = np.mean(closes[-20:])
        avg_vol10 = np.mean(volumes[-10:]) if len(volumes) >= 10 else 1
        today_vol = volumes[-1]
        vol_ratio = today_vol / avg_vol10 if avg_vol10 > 0 else 0

        # 均线多头排列
        if ma5 > ma10 > ma20:
            signal.append("✅均线多头")
        elif ma5 > ma20:
            signal.append("⚡均线初多")
        else:
            signal.append("⚠️均线偏弱")

        # 量比
        if 0.4 <= vol_ratio <= 0.85:
            signal.append("✅缩量")
        elif vol_ratio > 1.2:
            signal.append("⬆️放量")
        else:
            signal.append("⚡量持平")

        # 价格在均线上方
        cur = c['cur']
        if cur > ma5:
            signal.append("✅价>MA5")
        elif cur > ma20:
            signal.append("⚡价>MA20")
        else:
            signal.append("⚠️价<MA20")

    code_display = ('sh.' if code.startswith('6') else 'sz.') + code
    print(f"\n  {code_display} {c['name']}  现价{c['cur']:.2f}  涨幅{c['pct']:+.2f}%  评分{c['score']}")
    if ma5:
        print(f"  MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  量比={vol_ratio:.2f}")
    print(f"  信号: {' | '.join(signal) if signal else '数据不足'}")
    print(f"  振幅{c['amp']:.1f}%  距高点{c['from_hi']:.1f}%  换手{c['turn']:.2f}%  成交{c['amt']:.2f}亿")
    time.sleep(0.3)

print()
print("=" * 70)
print(f"  完成时间: {datetime.now().strftime('%H:%M:%S')}")
print("=" * 70)
