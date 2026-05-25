#!/usr/bin/env python3
"""早盘选股09:45 - 大盘+持仓+扫描"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import requests
import pandas as pd
import numpy as np
from db_cache import get_connection, init_db

init_db()
conn = get_connection()

def fetch_rt(sym):
    """腾讯实时行情"""
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split("~")
    def sf(i):
        try: return float(items[i])
        except: return 0.0
    return {
        'name': items[1], 'code': items[2],
        'cur': sf(3), 'pre': sf(4), 'open': sf(5),
        'high': sf(33), 'low': sf(34), 'pct': sf(32),
        'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
        'outer': sf(7), 'inner': sf(8),
        'time': items[30] if len(items) > 30 else ""
    }

# ═══════════════════════════════════════
# 第一步：大盘15分钟快照
# ═══════════════════════════════════════
print("=" * 60)
print("  【第一步】大盘15分钟快照")
print("=" * 60)

indices = [
    ('sh000001', '上证指数'),
    ('sz399001', '深证成指'),
    ('sz399006', '创业板指'),
]
idx_data = {}
for sym, label in indices:
    d = fetch_rt(sym)
    idx_data[sym] = d
    print(f"  {label}: {d['cur']:.2f}  {d['pct']:+.2f}%  开{d['open']:.2f}  高{d['high']:.2f} 低{d['low']:.2f}  额{d['amt']/10000:.0f}亿")

sh_pct = idx_data['sh000001']['pct']
sz_pct = idx_data['sz399001']['pct']
cy_pct = idx_data['sz399006']['pct']
avg_pct = (sh_pct + sz_pct + cy_pct) / 3
print(f"\n  综合: 沪{sh_pct:+.2f}% 深{sz_pct:+.2f}% 创{cy_pct:+.2f}% 均值{avg_pct:+.2f}%")

if avg_pct < -1:
    print("  🔴 大盘跌>1%，铁律5：不开新仓！")
    can_open = False
elif avg_pct < -0.3:
    print("  ⚠️ 大盘偏弱，谨慎开仓，减半仓位")
    can_open = True
else:
    print("  ✅ 大盘环境允许开新仓")
    can_open = True

# ═══════════════════════════════════════
# 第二步：持仓紧急检查
# ═══════════════════════════════════════
print(f"\n{'=' * 60}")
print("  【第二步】持仓紧急检查")
print("=" * 60)

holdings = [
    {'sym': 'sz002906', 'name': '华阳集团', 'buy': 31.74, 'pos': '1/3仓', 'D': 'D2(持有/止盈日)', 'buy_date': '04-17'},
    {'sym': 'sz002158', 'name': '汉钟精机', 'buy': 24.74, 'pos': '1/3仓', 'D': 'D5(超期!应清)', 'buy_date': '04-15'},
]

for h in holdings:
    d = fetch_rt(h['sym'])
    pnl = (d['cur'] / h['buy'] - 1) * 100
    stop_hard = h['buy'] * 0.97
    stop_soft = h['buy'] * 0.985
    tp1 = h['buy'] * 1.03
    tp2 = h['buy'] * 1.05
    
    print(f"\n  {'─'*50}")
    print(f"  {h['name']}({h['sym']}) {h['pos']} 买入{h['buy']}  {h['D']}")
    print(f"  现价: {d['cur']:.2f}  涨跌: {d['pct']:+.2f}%  浮盈: {pnl:+.2f}%")
    print(f"  开{d['open']:.2f} 高{d['high']:.2f} 低{d['low']:.2f}")
    
    # 检查信号
    signals = []
    if d['cur'] < stop_hard:
        signals.append(f"🔴 已破硬止损{stop_hard:.2f}! 立即清仓!")
    elif d['low'] < stop_hard:
        signals.append(f"⚠️ 盘中触及硬止损{stop_hard:.2f}附近，密切观察")
    
    if d['cur'] >= tp1:
        signals.append(f"🟢 已触及止盈1({tp1:.2f})! 卖1/3")
    
    if d['cur'] < stop_soft and 'D2' in h['D']:
        signals.append(f"⚠️ 低于软止损线{stop_soft:.2f}")
    
    if 'D5' in h['D'] or 'D3' in h['D']:
        signals.append(f"🔴 {h['D']} → 今日必须清仓!")
    
    if signals:
        for s in signals:
            print(f"  >>> {s}")
    else:
        print(f"  ✅ 暂无触发信号")
    
    print(f"  [止损] 硬{stop_hard:.2f} / 软{stop_soft:.2f}")
    print(f"  [止盈] +3%={tp1:.2f} / +5%={tp2:.2f}")

# ═══════════════════════════════════════
# 第三步：盘中实时选股
# ═══════════════════════════════════════
print(f"\n{'=' * 60}")
print("  【第三步】盘中实时选股扫描")
print("=" * 60)

if not can_open:
    print("  ❌ 大盘环境禁止开新仓，跳过选股")
else:
    # 从上周五晚候选池(4.17.txt)+ screener结果获取候选
    # 先扫描这些已经通过S2/S3筛选的票的实时表现
    candidates = [
        {'sym': 'sh600773', 'bs': 'sh.600773', 'name': '西藏城投', 'strategy': 'S2', 'buy_lo': 20.28, 'buy_hi': 21.06, 'stop': 19.86},
        {'sym': 'sh603613', 'bs': 'sh.603613', 'name': '国联股份', 'strategy': 'S2', 'buy_lo': 26.63, 'buy_hi': 27.71, 'stop': 25.55},
        {'sym': 'sh603660', 'bs': 'sh.603660', 'name': '苏州科达', 'strategy': 'S2', 'buy_lo': 9.82, 'buy_hi': 10.22, 'stop': 9.57},
        {'sym': 'sh603991', 'bs': 'sh.603991', 'name': '至正股份', 'strategy': 'S2', 'buy_lo': 109.76, 'buy_hi': 114.24, 'stop': 107.0},
        {'sym': 'sz002046', 'bs': 'sz.002046', 'name': '国机精工', 'strategy': 'S2', 'buy_lo': 48.56, 'buy_hi': 50.54, 'stop': 47.0},
    ]
    
    print(f"\n  扫描{len(candidates)}只上周五候选票实时表现:")
    print(f"  {'─'*55}")
    
    good = []
    for c in candidates:
        try:
            d = fetch_rt(c['sym'])
            pct = d['pct']
            cur = d['cur']
            pre = d['pre']
            opn = d['open']
            
            # 判断状态
            status = ""
            action = ""
            if pct > 4:
                status = "❌ 高开>4%不追"
                action = "放弃"
            elif cur > c['buy_hi'] * 1.03:
                status = "❌ 超出买入区间>3%"
                action = "放弃"
            elif cur < c['buy_lo'] * 0.97:
                status = "⚠️ 低开>3%有利空?"
                action = "观察"
            elif c['buy_lo'] <= cur <= c['buy_hi']:
                status = "✅ 在买入区间内"
                action = "等回踩确认"
                good.append({**c, 'rt': d})
            elif cur < c['buy_lo']:
                status = "⚠️ 低于买入区间"
                action = "观察是否企稳"
            else:
                status = "⚠️ 略高于区间"
                action = "等回踩"
                if cur <= c['buy_hi'] * 1.02:
                    good.append({**c, 'rt': d})
            
            # 量能
            vol_info = f"量{d['amt']:.0f}万" if d['amt'] > 0 else ""
            
            print(f"  {c['name']:6s} {cur:>8.2f} {pct:+5.2f}% 开{opn:.2f} {vol_info:>10s}  {status} → {action}")
        except Exception as e:
            print(f"  {c['name']:6s} 获取失败: {e}")
    
    # 额外扫描：查数据库中S2形态好的票，看今天是否放量启动
    print(f"\n  {'─'*55}")
    print(f"  额外扫描：数据库S2候选的实时验证")
    print(f"  {'─'*55}")
    
    # 从数据库找近5日有大阳线(>4%)且后续缩量的票
    sql = """
    WITH big_yang AS (
        SELECT code, date, close, open, volume, pctChg,
               ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as rn
        FROM kline_daily 
        WHERE pctChg >= 4.0 
          AND date >= date('2026-04-10')
          AND close > open
    ),
    latest AS (
        SELECT code, MAX(date) as last_date, 
               AVG(volume) as avg_vol
        FROM kline_daily 
        WHERE date >= date('2026-04-13')
        GROUP BY code
    )
    SELECT b.code, b.date as yang_date, b.close as yang_close, b.open as yang_open, 
           b.volume as yang_vol, b.pctChg,
           l.avg_vol as post_avg_vol
    FROM big_yang b
    JOIN latest l ON b.code = l.code
    WHERE b.rn = 1
      AND l.avg_vol < b.volume * 0.7
    ORDER BY b.pctChg DESC
    LIMIT 30
    """
    try:
        extra = pd.read_sql(sql, conn)
        if len(extra) > 0:
            # 获取行业信息
            codes = extra['code'].tolist()
            placeholders = ','.join(['?']*len(codes))
            industries = pd.read_sql(
                f"SELECT code, code_name, industry FROM stock_industry WHERE code IN ({placeholders})",
                conn, params=codes
            )
            extra = extra.merge(industries, on='code', how='left')
            
            # 获取MA数据和市值过滤
            scanned = 0
            for _, row in extra.iterrows():
                if scanned >= 10:
                    break
                code = row['code']
                # 转腾讯格式
                if code.startswith('sh.'):
                    tencent_sym = 'sh' + code[3:]
                elif code.startswith('sz.'):
                    tencent_sym = 'sz' + code[3:]
                else:
                    continue
                
                # 跳过已在候选列表中的
                if tencent_sym in [c['sym'] for c in candidates]:
                    continue
                # 跳过持仓
                if tencent_sym in ['sz002906', 'sz002158']:
                    continue
                
                try:
                    d = fetch_rt(tencent_sym)
                    if d['cur'] <= 0:
                        continue
                    pct = d['pct']
                    # 筛选：涨1-4%，不是高开透支
                    if 1.0 <= pct <= 4.0:
                        name = row.get('code_name', '?')
                        industry = row.get('industry', '?')
                        shrink = row['post_avg_vol'] / row['yang_vol']
                        print(f"  🔍 {name:6s}({code}) {d['cur']:.2f} {pct:+.2f}%  "
                              f"大阳{row['yang_date']}+{row['pctChg']:.1f}%  缩量比{shrink:.2f}  {industry}")
                        scanned += 1
                    elif pct > 4:
                        scanned += 1  # count but skip
                except:
                    pass
        else:
            print("  数据库中未找到符合条件的额外候选")
    except Exception as e:
        print(f"  数据库扫描异常: {e}")

    # 输出汇总
    print(f"\n{'=' * 60}")
    print("  【第四步】候选清单汇总")
    print("=" * 60)
    
    if good:
        for g in good:
            d = g['rt']
            cur = d['cur']
            buy_mid = (g['buy_lo'] + g['buy_hi']) / 2
            tp1 = buy_mid * 1.03
            tp2 = buy_mid * 1.05
            print(f"\n  {g['name']} ({g['sym']})  策略: {g['strategy']}")
            print(f"  现价: {cur:.2f}  涨幅: {d['pct']:+.2f}%")
            print(f"  买入区间: {g['buy_lo']:.2f} ~ {g['buy_hi']:.2f}")
            print(f"  硬止损: {g['stop']:.2f}")
            print(f"  M3止盈1(+3%): {tp1:.2f}  止盈2(+5%): {tp2:.2f}")
            print(f"  >>> 等10:00-10:30回踩到{g['buy_lo']:.2f}~{cur:.2f}区间再进")
    else:
        print("  暂无在买入区间内的候选票")
        print("  → 继续观察到10:00-10:30，看是否有回踩机会")

conn.close()
print(f"\n{'=' * 60}")
print("  【铁律提醒】")
print("=" * 60)
print("  · 09:45看到的票不要马上追! 等10:00-10:30回踩确认")
print("  · 回踩不破今日开盘价 → 买入首仓一半")
print("  · 10:30后继续放量 → 补齐另一半")
print("  · 涨>3%追不上 → 放弃，不追高")
print("  · 仓位减半: A级1/4仓，B级1/8仓")
print("  · 同时最多2只新票")
print()
