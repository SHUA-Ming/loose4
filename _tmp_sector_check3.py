import sys, sqlite3, os
sys.path.insert(0, '工具脚本')

db_path = '数据缓存/stock_cache.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tbls = [r[0] for r in cur.fetchall()]
print('Tables:', tbls)

# Find 000600 sector
for tbl in ['stock_industry', 'stock_info', 'stocks', 'kline']:
    if tbl in tbls:
        try:
            cur.execute(f"PRAGMA table_info({tbl})")
            cols_info = cur.fetchall()
            cols = [c[1] for c in cols_info]
            print(f'\n{tbl} columns: {cols}')
            if any('code' in c.lower() for c in cols):
                code_col = next(c for c in cols if 'code' in c.lower())
                cur.execute(f"SELECT * FROM {tbl} WHERE {code_col} LIKE '%000600%' LIMIT 3")
                rows = cur.fetchall()
                for r in rows: print(r)
        except Exception as e:
            print(f'{tbl} error: {e}')

# sector_daily structure
if 'sector_daily' in tbls:
    cur.execute("PRAGMA table_info(sector_daily)")
    cols_info = cur.fetchall()
    cols = [c[1] for c in cols_info]
    print('\nsector_daily columns:', cols)
    cur.execute("SELECT * FROM sector_daily ORDER BY rowid DESC LIMIT 10")
    rows = cur.fetchall()
    for r in rows: print(r)

conn.close()
