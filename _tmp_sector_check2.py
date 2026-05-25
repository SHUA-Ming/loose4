import sys, sqlite3, os
sys.path.insert(0, '工具脚本')

db_path = '数据缓存/stock_cache.db'
if not os.path.exists(db_path):
    print('DB not found:', db_path)
else:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # List tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tbls = [r[0] for r in cur.fetchall()]
    print('Tables:', tbls)
    
    # Find 000600 sector
    for tbl in ['stock_industry', 'stock_info', 'stocks']:
        if tbl in tbls:
            try:
                cur.execute(f"SELECT * FROM {tbl} WHERE code LIKE '%000600%' LIMIT 3")
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
                print(f'\n{tbl} columns:', cols)
                for r in rows: print(r)
            except Exception as e:
                print(f'{tbl} error: {e}')
    
    # Check sector_daily
    if 'sector_daily' in tbls:
        cur.execute("SELECT * FROM sector_daily ORDER BY trade_date DESC LIMIT 5")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        print('\nsector_daily sample:', cols)
        for r in rows: print(r)
    
    conn.close()
