import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
init_db()
conn = get_connection()
r = conn.execute("SELECT MIN(date),MAX(date),COUNT(DISTINCT date),COUNT(DISTINCT code) FROM kline_daily WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'").fetchone()
print(f'日期范围: {r[0]} ~ {r[1]}, 交易日数: {r[2]}, 股票数: {r[3]}')
dates = conn.execute('SELECT DISTINCT date FROM kline_daily ORDER BY date DESC LIMIT 15').fetchall()
print('最近15个交易日:', [d[0] for d in dates])
try:
    sec_r = conn.execute('SELECT COUNT(DISTINCT industry),MIN(date),MAX(date) FROM sector_daily').fetchone()
    print(f'板块数据: {sec_r[0]}个行业, {sec_r[1]} ~ {sec_r[2]}')
except:
    print('无板块数据')
conn.close()
