import sys
sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection
conn = get_connection()
c = conn.cursor()

# Check what tables exist
print("--- Tables in DB ---")
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
for t in tables:
    print(t)

# Try to find the right table with date data
for t in tables:
    try:
        c.execute(f"SELECT * FROM {t} LIMIT 1")
        cols = [d[0] for d in c.description]
        if 'date' in cols or 'trade_date' in cols:
            date_col = 'date' if 'date' in cols else 'trade_date'
            c.execute(f"SELECT {date_col}, count(*) FROM {t} WHERE {date_col} >= '2026-04-14' GROUP BY {date_col} ORDER BY {date_col}")
            rows = c.fetchall()
            if rows:
                print(f"\n--- {t} (col={date_col}) ---")
                for r in rows:
                    print(f"  {r[0]}: {r[1]} rows")
    except:
        pass

conn.close()
