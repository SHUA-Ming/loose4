#!/usr/bin/env python3
import sys; sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
init_db()
conn = get_connection()

r = conn.execute('SELECT MAX(date), COUNT(*) FROM kline_daily').fetchone()
print(f'数据库最新日期: {r[0]}  总记录: {r[1]}')

r2 = conn.execute("SELECT COUNT(*) FROM kline_daily WHERE date >= '2026-04-14'").fetchone()
print(f'04-14以后记录: {r2[0]}')

r3 = conn.execute("SELECT COUNT(*) FROM kline_daily WHERE pctChg>=4 AND close>open AND date>='2026-04-08'").fetchone()
print(f'04-08以后大阳线(>4%): {r3[0]}')

rows = conn.execute("SELECT date, COUNT(*) FROM kline_daily WHERE pctChg>=4 AND close>open AND date>='2026-04-08' GROUP BY date ORDER BY date").fetchall()
for row in rows:
    print(f'  {row[0]}: {row[1]}只大阳')

# 看看具体的S2形态SQL哪里有问题 - 测试子查询
print("\n--- 测试big_yang ---")
rows2 = conn.execute("""
    SELECT k.code, k.date, k.pctChg, k.volume, k.close, k.open
    FROM kline_daily k
    WHERE k.pctChg >= 4.0
      AND k.close > k.open
      AND k.date >= '2026-04-08'
    ORDER BY k.date DESC
    LIMIT 20
""").fetchall()
for r in rows2:
    print(f'  {r[0]} {r[1]} +{r[2]:.1f}% vol={r[3]} c={r[4]} o={r[5]}')

# 检查volume和avg_vol_20关系
print("\n--- 量比检查(sample) ---")
sample = conn.execute("""
    WITH vol20 AS (
        SELECT code, AVG(volume) as avg20
        FROM kline_daily WHERE date >= '2026-03-20'
        GROUP BY code
    )
    SELECT k.code, k.date, k.pctChg, k.volume, v.avg20, 
           CAST(k.volume AS REAL)/v.avg20 as vol_ratio
    FROM kline_daily k
    JOIN vol20 v ON v.code = k.code
    WHERE k.pctChg >= 4.0 AND k.close > k.open AND k.date >= '2026-04-08'
    ORDER BY k.date DESC
    LIMIT 10
""").fetchall()
for r in sample:
    print(f'  {r[0]} {r[1]} +{r[2]:.1f}% vol={r[3]:.0f} avg20={r[4]:.0f} ratio={r[5]:.2f}')

# 检查后续缩量
print("\n--- post缩量检查 ---")
test_code = sample[0][0] if sample else None
test_date = sample[0][1] if sample else None
if test_code:
    post = conn.execute("""
        SELECT date, volume, close FROM kline_daily 
        WHERE code=? AND date>? ORDER BY date
    """, (test_code, test_date)).fetchall()
    yang_vol = sample[0][3]
    print(f'  大阳: {test_code} {test_date} vol={yang_vol}')
    for p in post:
        ratio = p[1] / yang_vol if yang_vol > 0 else 0
        print(f'    {p[0]} vol={p[1]:.0f} ratio={ratio:.2f} close={p[2]}')

conn.close()
