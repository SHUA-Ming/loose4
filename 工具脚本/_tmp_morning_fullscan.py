#!/usr/bin/env python3
"""早盘实时全市场扫描 - 不依赖历史候选，纯盘面驱动"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import requests
import pandas as pd
import numpy as np
from db_cache import get_connection, init_db
import time

init_db()
conn = get_connection()

def fetch_rt(sym):
    """腾讯实时行情"""
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
    def sf(i):
        try: return float(items[i])
        except: return 0.0
    if len(items) < 35:
        return None
    return {
        'name': items[1], 'code': items[2],
        'cur': sf(3), 'pre': sf(4), 'open': sf(5),
        'high': sf(33), 'low': sf(34), 'pct': sf(32),
        'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
        'outer': sf(7), 'inner': sf(8),
    }

def fetch_batch(syms):
    """批量获取(最多50个一批)"""
    results = {}
    batch_size = 40
    for i in range(0, len(syms), batch_size):
        batch = syms[i:i+batch_size]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            resp = requests.get(url, timeout=10)
            resp.encoding = "gbk"
            lines = resp.text.strip().split(";")
            for line in lines:
                line = line.strip()
                if not line or '="' not in line:
                    continue
                # 提取sym from v_xxxx
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

print("=" * 65)
print("  早盘实时全市场扫描 | 纯盘面驱动 | 不依赖历史候选")
print("=" * 65)

# ═══════════════════════════════════════
# Step 1: 从数据库找S2形态候选(大阳后缩量)
# ═══════════════════════════════════════
print(f"\n{'─'*65}")
print("  Step 1: 数据库筛选S2形态（近5日有大阳+后续缩量）")
print(f"{'─'*65}")

# S2：近5个交易日内有大阳线(≥4%,收阳,放量1.5x) + 后续缩量(<70%)
sql_s2 = """
WITH vol20 AS (
    SELECT code, AVG(volume) as avg_vol_20
    FROM kline_daily
    WHERE date >= date('2026-03-20')
    GROUP BY code
),
big_yang AS (
    SELECT k.code, k.date, k.open, k.close, k.high, k.low, 
           k.volume, k.pctChg, k.turn,
           v.avg_vol_20
    FROM kline_daily k
    JOIN vol20 v ON k.code = v.code
    WHERE k.pctChg >= 4.0
      AND k.close > k.open
      AND k.volume >= v.avg_vol_20 * 1.3
      AND k.date >= date('2026-04-10')
),
post_yang AS (
    SELECT b.code, b.date as yang_date, b.close as yang_close, 
           b.open as yang_open, b.volume as yang_vol, b.pctChg as yang_pct,
           AVG(k2.volume) as post_vol,
           MIN(k2.close) as min_post_close,
           MAX(k2.close) as max_post_close,
           COUNT(k2.date) as post_days
    FROM big_yang b
    JOIN kline_daily k2 ON k2.code = b.code AND k2.date > b.date
    GROUP BY b.code, b.date
    HAVING post_days >= 1
),
latest_price AS (
    SELECT code, close as last_close, date as last_date,
           volume as last_vol
    FROM kline_daily
    WHERE date = (SELECT MAX(date) FROM kline_daily)
),
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
)
SELECT p.code, p.yang_date, p.yang_close, p.yang_open, p.yang_vol, p.yang_pct,
       p.post_vol, p.min_post_close, p.post_days,
       l.last_close, l.last_date,
       m.ma5, m.ma10, m.ma20, m.ma60,
       (p.post_vol * 1.0 / p.yang_vol) as shrink_ratio,
       (p.min_post_close / p.yang_close) as hold_ratio
FROM post_yang p
JOIN latest_price l ON l.code = p.code
JOIN ma_data m ON m.code = p.code
WHERE (p.post_vol * 1.0 / p.yang_vol) <= 0.7
  AND (p.min_post_close / p.yang_close) >= 0.97
  AND l.last_close > m.ma20
  AND l.last_close > 3
  AND p.post_days <= 6
ORDER BY p.yang_pct DESC, shrink_ratio ASC
"""

df_s2 = pd.read_sql(sql_s2, conn)
print(f"  S2形态候选: {len(df_s2)}只")

# 加行业信息和流通市值
if len(df_s2) > 0:
    codes = df_s2['code'].tolist()
    placeholders = ','.join(['?']*len(codes))
    industries = pd.read_sql(
        f"SELECT code, code_name, industry FROM stock_industry WHERE code IN ({placeholders})",
        conn, params=codes
    )
    df_s2 = df_s2.merge(industries, on='code', how='left')

# ═══════════════════════════════════════
# Step 2: 批量拉实时行情
# ═══════════════════════════════════════
print(f"\n{'─'*65}")
print("  Step 2: 批量拉取实时行情，筛选今日放量启动(+1%~+4%)")
print(f"{'─'*65}")

# 转换为腾讯代码
def to_tencent(bs_code):
    if bs_code.startswith('sh.'):
        return 'sh' + bs_code[3:]
    elif bs_code.startswith('sz.'):
        return 'sz' + bs_code[3:]
    return None

passed = []
if len(df_s2) > 0:
    sym_map = {}
    for _, row in df_s2.iterrows():
        tc = to_tencent(row['code'])
        if tc:
            sym_map[tc] = row
    
    # 批量获取
    all_syms = list(sym_map.keys())
    print(f"  批量请求{len(all_syms)}只...")
    rt_data = fetch_batch(all_syms)
    print(f"  获取到{len(rt_data)}只实时数据")
    
    # 筛选
    for sym, rt in rt_data.items():
        pct = rt['pct']
        cur = rt['cur']
        
        # 核心条件：涨幅+0.5%~+5%
        if not (0.5 <= pct <= 5.0):
            continue
        
        # 现价>MA5
        if sym in sym_map:
            row = sym_map[sym]
            ma5 = row['ma5'] if pd.notna(row['ma5']) else 0
            ma60 = row['ma60'] if pd.notna(row['ma60']) else 0
            if ma5 > 0 and cur < ma5:
                continue
            # 现价 > MA60 (中期趋势不坏)
            if ma60 > 0 and cur < ma60 * 0.97:
                continue
        
        passed.append({'sym': sym, 'rt': rt, 'db': sym_map.get(sym)})
    
    print(f"  符合涨幅+站上MA5: {len(passed)}只")

# ═══════════════════════════════════════
# Step 3: 板块过滤 + S2评分
# ═══════════════════════════════════════
print(f"\n{'─'*65}")
print("  Step 3: 板块过滤 + S2评分")
print(f"{'─'*65}")

# 获取板块5日动量排名
sector_df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY date', conn)
dates = sorted(sector_df['date'].unique())[-5:]
recent_sector = sector_df[sector_df['date'].isin(dates)]
mom5 = recent_sector.groupby('industry')['avg_pct'].sum().reset_index()
mom5.columns = ['industry', 'mom5']
mom5 = mom5.sort_values('mom5', ascending=False).reset_index(drop=True)
mom5['rank'] = range(1, len(mom5)+1)
total_sectors = len(mom5)
# M3模式: 淘汰后40%
cutoff_rank = int(total_sectors * 0.6)  # 前60%通过

final_candidates = []
for item in passed:
    db = item['db']
    rt = item['rt']
    
    if db is None:
        continue
    
    industry = db.get('industry', '')
    if not industry:
        continue
    
    # 板块排名检查
    sector_row = mom5[mom5['industry'] == industry]
    if len(sector_row) == 0:
        continue
    s_rank = int(sector_row['rank'].values[0])
    if s_rank > cutoff_rank:
        continue  # 淘汰弱势板块
    
    # S2评分 (8分制)
    score = 0
    
    # ① 缩量程度 (2分)
    shrink = db['shrink_ratio']
    if shrink <= 0.5:
        score += 2
    elif shrink <= 0.7:
        score += 1
    
    # ② 价格守住 (2分)
    hold = db['hold_ratio']
    if hold >= 0.99:
        score += 2
    elif hold >= 0.97:
        score += 1
    
    # ③ 板块强度 (2分)
    pct_rank = s_rank / total_sectors
    if pct_rank <= 0.3:
        score += 2
    elif pct_rank <= 0.5:
        score += 1
    
    # ④ 均线配合 (2分)
    ma5 = db['ma5'] if pd.notna(db['ma5']) else 0
    ma10 = db['ma10'] if pd.notna(db['ma10']) else 0
    ma20 = db['ma20'] if pd.notna(db['ma20']) else 0
    ma60 = db['ma60'] if pd.notna(db['ma60']) else 0
    if ma5 > ma10 > ma20 and rt['cur'] > ma60:
        score += 2
    elif ma5 > ma10:
        score += 1
    
    # 只要≥6分(B级以上)
    if score >= 6:
        grade = "A" if score >= 7 else "B"
        final_candidates.append({
            'sym': item['sym'],
            'code': db['code'],
            'name': db.get('code_name', rt['name']),
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
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        })

# 按评分排序
final_candidates.sort(key=lambda x: (-x['score'], x['shrink']))

print(f"  通过板块过滤+S2≥6分: {len(final_candidates)}只")

# ═══════════════════════════════════════
# Step 4: 输出
# ═══════════════════════════════════════
print(f"\n{'═'*65}")
print("  【最终候选清单】S2大阳横盘·今日放量启动")
print(f"{'═'*65}")

if not final_candidates:
    print("\n  暂无符合全部条件的候选。")
else:
    for i, c in enumerate(final_candidates[:10], 1):
        rt = c['rt']
        cur = rt['cur']
        # 买入区间: 回踩到现价-1%~-2%
        buy_hi = cur
        buy_lo = cur * 0.98
        # 止损: 大阳开盘价
        stop = c['yang_open']
        # 止盈
        tp1 = cur * 1.03
        tp2 = cur * 1.05
        
        # 日内位置
        if rt['high'] > rt['low']:
            intra_pos = (cur - rt['low']) / (rt['high'] - rt['low'])
        else:
            intra_pos = 0.5
        
        print(f"\n  {'━'*60}")
        print(f"  #{i} {c['name']} ({c['code']})  评分: {c['score']}/8 {c['grade']}级")
        print(f"  {'━'*60}")
        print(f"  现价: {cur:.2f}  涨幅: {rt['pct']:+.2f}%  开{rt['open']:.2f}  高{rt['high']:.2f} 低{rt['low']:.2f}")
        print(f"  行业: {c['industry']}  板块排名: {c['sector_rank']}/{total_sectors}")
        print(f"  大阳线: {c['yang_date']} +{c['yang_pct']:.1f}%  缩量比: {c['shrink']:.2f}  价格守住: {c['hold']:.3f}")
        print(f"  均线: MA5={c['ma5']:.2f} MA10={c['ma10']:.2f} MA20={c['ma20']:.2f} MA60={c['ma60']:.2f}")
        print(f"  日内位置: {intra_pos:.0%}  外/内盘: {rt['outer']:.0f}/{rt['inner']:.0f}")
        print(f"  ────────────────────────────────────")
        print(f"  买入区间: {buy_lo:.2f} ~ {buy_hi:.2f} (回踩-1%~-2%接)")
        print(f"  硬止损: {stop:.2f} (大阳开盘价)")
        print(f"  M3止盈1(+3%): {tp1:.2f}")
        print(f"  M3止盈2(+5%): {tp2:.2f}")
        print(f"  高开放弃线: >{cur*1.03:.2f}")

# 补充S3扫描（近20日新高+放量）
print(f"\n{'═'*65}")
print("  【补充】S3放量突破新高候选")
print(f"{'═'*65}")

sql_s3 = """
WITH hi20 AS (
    SELECT code, MAX(high) as high_20d
    FROM kline_daily
    WHERE date >= date('2026-03-24')
    GROUP BY code
),
latest AS (
    SELECT code, close, high, volume, date
    FROM kline_daily
    WHERE date = (SELECT MAX(date) FROM kline_daily)
),
vol20 AS (
    SELECT code, AVG(volume) as avg_vol
    FROM kline_daily
    WHERE date >= date('2026-03-20')
    GROUP BY code
)
SELECT l.code, l.close, l.high as today_high, h.high_20d, 
       l.volume as today_vol, v.avg_vol,
       (l.volume * 1.0 / v.avg_vol) as vol_ratio
FROM latest l
JOIN hi20 h ON h.code = l.code
JOIN vol20 v ON v.code = l.code
WHERE l.high >= h.high_20d * 0.99
  AND l.volume >= v.avg_vol * 1.5
  AND l.close > 3
ORDER BY vol_ratio DESC
LIMIT 20
"""
try:
    df_s3 = pd.read_sql(sql_s3, conn)
    if len(df_s3) > 0:
        codes_s3 = df_s3['code'].tolist()
        placeholders = ','.join(['?']*len(codes_s3))
        ind_s3 = pd.read_sql(
            f"SELECT code, code_name, industry FROM stock_industry WHERE code IN ({placeholders})",
            conn, params=codes_s3
        )
        df_s3 = df_s3.merge(ind_s3, on='code', how='left')
        
        # 批量拉实时
        s3_syms = [to_tencent(c) for c in codes_s3 if to_tencent(c)]
        s3_rt = fetch_batch(s3_syms)
        
        s3_passed = []
        for _, row in df_s3.iterrows():
            tc = to_tencent(row['code'])
            if tc not in s3_rt:
                continue
            rt = s3_rt[tc]
            if 1.0 <= rt['pct'] <= 5.0:
                # 板块检查
                ind = row.get('industry', '')
                sr = mom5[mom5['industry'] == ind]
                if len(sr) > 0 and int(sr['rank'].values[0]) <= cutoff_rank:
                    s3_passed.append({
                        'name': row.get('code_name', rt['name']),
                        'code': row['code'],
                        'rt': rt,
                        'vol_ratio': row['vol_ratio'],
                        'industry': ind,
                        'rank': int(sr['rank'].values[0]),
                    })
        
        if s3_passed:
            s3_passed.sort(key=lambda x: -x['vol_ratio'])
            for item in s3_passed[:5]:
                rt = item['rt']
                print(f"  {item['name']:6s}({item['code']}) {rt['cur']:.2f} {rt['pct']:+.2f}%  "
                      f"量比{item['vol_ratio']:.1f}x  {item['industry']} rank{item['rank']}")
        else:
            print("  今日暂无S3突破候选(涨幅1-5%区间)")
    else:
        print("  数据库无S3候选")
except Exception as e:
    print(f"  S3扫描异常: {e}")

conn.close()

print(f"\n{'═'*65}")
print("  【操作纪律】")
print(f"{'═'*65}")
print("  · 不要现在追! 等10:00-10:30回踩确认再进")
print("  · 回踩不破今日开盘价 → 买入首仓一半(1/8仓)")
print("  · 10:30后继续放量站上 → 补齐到1/4仓")
print("  · 涨超3%追不上 → 放弃")
print("  · 今天最多新开2只")
print("  · D计数从今天起: D1=今天, D3=后天必清")
print()
