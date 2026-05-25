from db_cache import get_connection
conn = get_connection()
r = conn.execute("SELECT COUNT(DISTINCT code) FROM kline_daily WHERE date='2026-04-16' AND code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'").fetchone()
print(f"4/16已更新: {r[0]} 只个股")
r2 = conn.execute("SELECT MAX(date) FROM kline_daily WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'").fetchone()
print(f"个股最新日期: {r2[0]}")
