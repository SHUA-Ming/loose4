from db_cache import get_connection
conn = get_connection()
r = conn.execute('SELECT COUNT(DISTINCT code) FROM kline_daily').fetchone()
print(f'股票总数: {r[0]}')
r2 = conn.execute('SELECT MIN(date),MAX(date) FROM kline_daily').fetchone()
print(f'日期范围: {r2[0]} ~ {r2[1]}')
r3 = conn.execute("SELECT date,COUNT(DISTINCT code) FROM kline_daily WHERE date>='2026-04-07' GROUP BY date ORDER BY date").fetchall()
for d, c in r3:
    print(f'  {d}: {c}只')
# 检查涨停家数样本
r4 = conn.execute("SELECT date, COUNT(*) FROM kline_daily WHERE pctChg>=9.5 AND date>='2026-04-01' GROUP BY date ORDER BY date").fetchall()
print('\n涨停板家数(>=9.5%):')
for d, c in r4:
    print(f'  {d}: {c}家')
r5 = conn.execute("SELECT date, COUNT(*) FROM kline_daily WHERE pctChg<=-9.5 AND date>='2026-04-01' GROUP BY date ORDER BY date").fetchall()
print('\n跌停家数(<=-9.5%):')
for d, c in r5:
    print(f'  {d}: {c}家')
conn.close()
