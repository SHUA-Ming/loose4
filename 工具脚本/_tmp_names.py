import sys
sys.stdout.reconfigure(encoding="utf-8")
from db_cache import get_connection, init_db
init_db()
conn = get_connection()

codes = [
    'sz.000537','sh.600084','sh.600773','sh.603613','sh.603660','sh.603991',
    'sz.002046','sz.002906','sh.603477','sh.603989','sz.000049','sz.002066',
    'sh.600106','sh.600232','sz.002047','sh.600289','sh.600707','sz.002559',
    'sz.000766','sh.600351','sh.605222','sh.603958','sh.603139','sh.605098',
]

for code in codes:
    r = conn.execute("SELECT code_name, industry FROM stock_industry WHERE code=?", [code]).fetchone()
    name = r[0] if r else '?'
    ind = r[1] if r else '?'
    r2 = conn.execute("SELECT close FROM kline_daily WHERE code=? ORDER BY date DESC LIMIT 1", [code]).fetchone()
    close = float(r2[0]) if r2 else 0
    print(f"  {code}: {name:10s} | {ind[:20]:20s} | {close:.2f}")
conn.close()
