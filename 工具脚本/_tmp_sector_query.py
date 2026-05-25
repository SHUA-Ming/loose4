import db_cache
conn = db_cache.get_connection()
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print("Tables:", tables)

# Try to find 法狮龙 sector info
for t in tables:
    tname = t[0]
    try:
        cur.execute(f"SELECT * FROM {tname} WHERE code='sh.605318' LIMIT 1")
        row = cur.fetchone()
        if row:
            cur.execute(f"PRAGMA table_info({tname})")
            cols = [c[1] for c in cur.fetchall()]
            print(f"\nFound in {tname}:")
            for c, v in zip(cols, row):
                print(f"  {c}: {v}")
    except:
        pass

# Also check sector_daily for recent data
try:
    cur.execute("SELECT DISTINCT industry FROM sector_daily ORDER BY industry")
    industries = [r[0] for r in cur.fetchall()]
    print(f"\nAvailable industries ({len(industries)}):")
    # Check 建筑 or 装饰 related
    for ind in industries:
        if '建' in ind or '装' in ind or '非金属' in ind or '金属' in ind:
            print(f"  {ind}")
except Exception as e:
    print(f"sector_daily error: {e}")

conn.close()
