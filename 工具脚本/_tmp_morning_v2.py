#!/usr/bin/env python3
"""早盘实时全市场扫描V2 - 修正版：放宽S2条件适配数据库延迟"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import requests
import pandas as pd
import numpy as np
from db_cache import get_connection, init_db
import time

init_db()
conn = get_connection()

def fetch_rt(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    try:
        idx = text.index('="') + 2
        payload = text[idx:].rstrip('";')
        items = payload.split("~")
    except:
        return None
    if len(items) < 35:
        return None
    def sf(i):
        try: return float(items[i])
        except: return 0.0
    if sf(3) <= 0:
        return None
    return {
        'name': items[1], 'code': items[2],
        'cur': sf(3), 'pre': sf(4), 'open': sf(5),
        'high': sf(33), 'low': sf(34), 'pct': sf(32),
        'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
        'outer': sf(7), 'inner': sf(8),
    }

def fetch_batch(syms):
    results = {}
    batch_size = 40
    for i in range(0, len(syms), batch_size):
        batch = syms[i:i+batch_size]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            resp = requests.get(url, timeout=10)
            resp.encoding = "gbk"
            for line in resp.text.strip().split(";"):
                line = line.strip()
                if not line or '="' not in line:
                    continue
                var_part = line.split("=")[0]
                sym_key = var_part.replace("v_", "").strip()
                idx = line.index('="') + 2
                payload = line[idx:].rstrip('"')
                items = payload.split("~")
                if len(items) < 35:
                    continue
                def sf(i):
                    try: return float(items[i])
                    except: return 0.0
                if sf(3) <= 0:
                    continue
                results[sym_key] = {
                    'name': items[1], 'code': items[2],
                    'cur': sf(3), 'pre': sf(4), 'open': sf(5),
                    'high': sf(33), 'low': sf(34), 'pct': sf(32),
                    'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
                    'outer': sf(7), 'inner': sf(8),
                }
        except Exception as e:
            print(f"  批量获取异常: {e}")
        time.sleep(0.3)
    return results

def to_tencent(bs_code):
    if bs_code.startswith('sh.'):
        return 'sh' + bs_code[3:]
    elif bs_code.startswith('sz.'):
        return 'sz' + bs_code[3:]
    return None

print("=" * 65)
print("  早盘实时全市场扫描V2 | 纯盘面驱动")
print("=" * 65)

# ═══════════════════════════════════════
# Step 1: S2候选 - 近7日有大阳(>4%放量)+后续不跌回
# 放宽：不强制要求post缩量(因为数据只到04-17, 很多大阳在04-14-04-16)
# 改用实时数据确认"今日涨"来代替后续缩量验证
# ═══════════════════════════════════════
print(f"\n{'─'*65}")
print("  Step 1: 找近7日大阳线候选(≥4%,收阳,放量)")
print(f"{'─'*65}")

sql = """
WITH vol20 AS (
    SELECT code, AVG(volume) as avg20
    FROM kline_daily
    WHERE date >= '2026-03-20' AND date <= '2026-04-16'
    GROUP BY code
),
big_yang AS (
    SELECT k.code, k.date as yang_date, k.open as yang_open, 
           k.close as yang_close, k.high as yang_high,
           k.volume as yang_vol, k.pctChg as yang_pct,
           v.avg20,
           (k.volume * 1.0 / v.avg20) as yang_vol_ratio
    FROM kline_daily k
    JOIN vol20 v ON v.code = k.code
    WHERE k.pctChg >= 4.0
      AND k.close > k.open
      AND k.volume >= v.avg20 * 1.2
      AND k.date >= '2026-04-08'
      AND k.close > 3
),
-- 取每只票最近的大阳线
latest_yang AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY yang_date DESC) as rn
    FROM big_yang
),
-- 大阳线后的收盘检查(如有后续数据)
post_check AS (
    SELECT ly.code, ly.yang_date, ly.yang_close, ly.yang_open, ly.yang_vol, 
           ly.yang_pct, ly.yang_vol_ratio,
           MIN(k2.close) as min_post_close,
           AVG(k2.volume) as avg_post_vol,
           COUNT(k2.date) as post_days
    FROM latest_yang ly
    LEFT JOIN kline_daily k2 ON k2.code = ly.code AND k2.date > ly.yang_date
    WHERE ly.rn = 1
    GROUP BY ly.code
),
-- 均线数据
ma_data AS (
    SELECT code,
           AVG(CASE WHEN rn <= 5 THEN close END) as ma5,
           AVG(CASE WHEN rn <= 10 THEN close END) as ma10,
           AVG(CASE WHEN rn <= 20 THEN close END) as ma20,
           AVG(CASE WHEN rn <= 60 THEN close END) as ma60
    FROM (
        SELECT code, close,
               ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as rn
        FROM kline_daily
    ) sub
    WHERE rn <= 60
    GROUP BY code
),
-- 最新收盘价
latest_price AS (
    SELECT code, close as last_close, volume as last_vol
    FROM kline_daily k
    WHERE date = (SELECT MAX(date) FROM kline_daily WHERE code = k.code)
)
SELECT p.code, p.yang_date, p.yang_close, p.yang_open, p.yang_vol, p.yang_pct,
       p.yang_vol_ratio, p.min_post_close, p.avg_post_vol, p.post_days,
       l.last_close,
       m.ma5, m.ma10, m.ma20, m.ma60,
       CASE WHEN p.post_days > 0 THEN (p.avg_post_vol / p.yang_vol) ELSE 0.5 END as shrink_ratio,
       CASE WHEN p.min_post_close IS NOT NULL THEN (p.min_post_close / p.yang_close) ELSE 1.0 END as hold_ratio
FROM post_check p
JOIN latest_price l ON l.code = p.code
JOIN ma_data m ON m.code = p.code
WHERE l.last_close >= p.yang_open
  AND l.last_close > COALESCE(m.ma20, 0)
  AND l.last_close > 5
  AND (p.min_post_close IS NULL OR p.min_post_close >= p.yang_close * 0.95)
ORDER BY p.yang_pct DESC
LIMIT 200
"""

df_s2 = pd.read_sql(sql, conn)
print(f"  数据库S2形态候选: {len(df_s2)}只")

# 加行业
if len(df_s2) > 0:
    codes = df_s2['code'].tolist()
    placeholders = ','.join(['?']*len(codes))
    industries = pd.read_sql(
        f"SELECT code, code_name, industry FROM stock_industry WHERE code IN ({placeholders})",
        conn, params=codes
    )
    df_s2 = df_s2.merge(industries, on='code', how='left')

# ═══════════════════════════════════════
# Step 2: 板块排名
# ═══════════════════════════════════════
sector_df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY date', conn)
dates = sorted(sector_df['date'].unique())[-5:]
recent_sector = sector_df[sector_df['date'].isin(dates)]
mom5 = recent_sector.groupby('industry')['avg_pct'].sum().reset_index()
mom5.columns = ['industry', 'mom5']
mom5 = mom5.sort_values('mom5', ascending=False).reset_index(drop=True)
mom5['rank'] = range(1, len(mom5)+1)
total_sectors = len(mom5)
cutoff_rank = int(total_sectors * 0.6)  # M3: 淘汰后40%

# ═══════════════════════════════════════
# Step 3: 批量拉实时行情
# ═══════════════════════════════════════
print(f"\n{'─'*65}")
print("  Step 2: 批量拉实时行情")
print(f"{'─'*65}")

passed = []
if len(df_s2) > 0:
    sym_map = {}
    for _, row in df_s2.iterrows():
        tc = to_tencent(row['code'])
        if tc:
            sym_map[tc] = row
    
    all_syms = list(sym_map.keys())
    print(f"  请求{len(all_syms)}只行情...")
    rt_data = fetch_batch(all_syms)
    print(f"  获取{len(rt_data)}只")
    
    for sym, rt in rt_data.items():
        pct = rt['pct']
        cur = rt['cur']
        
        # 今日涨0.5~5% = 放量启动但不过热
        if not (0.5 <= pct <= 5.0):
            continue
        
        if sym not in sym_map:
            continue
        row = sym_map[sym]
        
        # 现价站上MA5
        ma5 = row['ma5'] if pd.notna(row['ma5']) else 0
        ma60 = row['ma60'] if pd.notna(row['ma60']) else 0
        if ma5 > 0 and cur < ma5 * 0.99:
            continue
        # 不能太远离MA60(超跌弱势排除)
        if ma60 > 0 and cur < ma60 * 0.95:
            continue
        
        passed.append({'sym': sym, 'rt': rt, 'db': row})

print(f"  涨0.5~5% + 站MA5: {len(passed)}只")

# ═══════════════════════════════════════
# Step 4: 板块过滤 + 评分
# ═══════════════════════════════════════
print(f"\n{'─'*65}")
print("  Step 3: 板块过滤 + S2评分")
print(f"{'─'*65}")

final = []
for item in passed:
    db = item['db']
    rt = item['rt']
    
    industry = db.get('industry', '') if hasattr(db, 'get') else (db['industry'] if 'industry' in db.index else '')
    if not industry:
        continue
    
    sr = mom5[mom5['industry'] == industry]
    if len(sr) == 0:
        continue
    s_rank = int(sr['rank'].values[0])
    if s_rank > cutoff_rank:
        continue
    
    # S2评分
    score = 0
    
    # ① 缩量 (2分) - 用DB数据或默认1分
    shrink = db['shrink_ratio']
    if shrink <= 0.5:
        score += 2
    elif shrink <= 0.7:
        score += 1
    else:
        score += 1  # 如果没有post data默认给1分
    
    # ② 价格守住 (2分)
    hold = db['hold_ratio']
    if hold >= 0.99:
        score += 2
    elif hold >= 0.97:
        score += 1
    
    # ③ 板块 (2分)
    pct_rank = s_rank / total_sectors
    if pct_rank <= 0.3:
        score += 2
    elif pct_rank <= 0.5:
        score += 1
    
    # ④ 均线 (2分)
    ma5 = db['ma5'] if pd.notna(db['ma5']) else 0
    ma10 = db['ma10'] if pd.notna(db['ma10']) else 0
    ma20 = db['ma20'] if pd.notna(db['ma20']) else 0
    ma60 = db['ma60'] if pd.notna(db['ma60']) else 0
    if ma5 > ma10 > ma20 and rt['cur'] > ma60 * 0.99:
        score += 2
    elif ma5 > ma10:
        score += 1
    
    if score >= 6:
        grade = "A" if score >= 7 else "B"
        final.append({
            'sym': item['sym'],
            'code': db['code'],
            'name': db.get('code_name', rt['name']) if hasattr(db, 'get') else rt['name'],
            'industry': industry,
            'rt': rt,
            'score': score,
            'grade': grade,
            'shrink': shrink,
            'hold': hold,
            'sector_rank': s_rank,
            'yang_date': db['yang_date'],
            'yang_pct': db['yang_pct'],
            'yang_close': db['yang_close'],
            'yang_open': db['yang_open'],
            'yang_vol_ratio': db['yang_vol_ratio'],
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        })

final.sort(key=lambda x: (-x['score'], -x['rt']['pct']))
# 同行业只取最高分1只
seen_ind = set()
deduped = []
for c in final:
    if c['industry'] not in seen_ind:
        deduped.append(c)
        seen_ind.add(c['industry'])
final = deduped

print(f"  评分≥6 + 同行业去重: {len(final)}只")

# ═══════════════════════════════════════
# 输出
# ═══════════════════════════════════════
print(f"\n{'═'*65}")
print("  【最终候选清单】今日盘面实时S2选股")
print(f"{'═'*65}")

if not final:
    print("\n  暂无符合全部条件的候选。")
else:
    for i, c in enumerate(final[:8], 1):
        rt = c['rt']
        cur = rt['cur']
        buy_lo = cur * 0.98
        buy_hi = cur
        stop = c['yang_open']
        tp1 = cur * 1.03
        tp2 = cur * 1.05
        
        if rt['high'] > rt['low']:
            intra_pos = (cur - rt['low']) / (rt['high'] - rt['low'])
        else:
            intra_pos = 0.5
        
        oi_ratio = rt['outer'] / max(rt['inner'], 1) if rt['outer'] > 0 else 1.0
        
        print(f"\n  {'━'*60}")
        print(f"  #{i} {c['name']} ({c['code']})  S2评分: {c['score']}/8 {c['grade']}级")
        print(f"  {'━'*60}")
        print(f"  现价: {cur:.2f}  涨幅: {rt['pct']:+.2f}%  日内位置: {intra_pos:.0%}")
        print(f"  开{rt['open']:.2f}  高{rt['high']:.2f}  低{rt['low']:.2f}  外/内:{oi_ratio:.2f}")
        print(f"  板块: {c['industry']}  排名: {c['sector_rank']}/{total_sectors}")
        print(f"  大阳: {c['yang_date']} +{c['yang_pct']:.1f}% 量比{c['yang_vol_ratio']:.1f}x")
        print(f"  缩量比: {c['shrink']:.2f}  守住比: {c['hold']:.3f}")
        print(f"  MA: 5={c['ma5']:.2f} 10={c['ma10']:.2f} 20={c['ma20']:.2f} 60={c['ma60']:.2f}")
        print(f"  ─── 操作计划 ───")
        print(f"  买入区间: {buy_lo:.2f} ~ {buy_hi:.2f}")
        print(f"  硬止损: {stop:.2f} (大阳开盘价)")
        print(f"  止盈1(+3%): {tp1:.2f}  止盈2(+5%): {tp2:.2f}")

conn.close()

print(f"\n{'═'*65}")
print("  【纪律】")
print(f"{'═'*65}")
print("  · 等10:00-10:30回踩确认再买，不追!")
print("  · 回踩不破开盘价 → 首仓1/8  → 继续放量再补到1/4")
print("  · 涨>3%追不上 → 放弃")
print("  · 今天最多新开2只 | D1=今天, D3=后天必清")
print()
