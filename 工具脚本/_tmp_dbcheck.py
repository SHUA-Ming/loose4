import sys; sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
init_db(); conn = get_connection()
cols = conn.execute('PRAGMA table_info(kline_daily)').fetchall()
print('kline_daily columns:', [c[1] for c in cols])
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('Tables:', [t[0] for t in tables])
for t in tables:
    if 'stock' in t[0].lower() or 'name' in t[0].lower() or 'info' in t[0].lower():
        cols2 = conn.execute(f'PRAGMA table_info({t[0]})').fetchall()
        print(f'  {t[0]} columns:', [c[1] for c in cols2])
conn.close()
