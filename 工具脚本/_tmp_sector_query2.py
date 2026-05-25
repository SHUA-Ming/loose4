import sqlite3
import pandas as pd
from datetime import datetime

conn = sqlite3.connect('../数据缓存/stock_cache.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('Tables:', [t[0] for t in tables])

# Try to find sector-related data
for t in [t[0] for t in tables]:
    cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
    print(f"\n{t}: {[c[1] for c in cols]}")

conn.close()
