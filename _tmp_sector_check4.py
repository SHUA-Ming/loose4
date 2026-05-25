import sys, sqlite3, os
sys.path.insert(0, '工具脚本')

db_path = '数据缓存/stock_cache.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Find 000600 sector info
cur.execute("SELECT * FROM stock_industry WHERE code LIKE '%000600%'")
rows = cur.fetchall()
print('000600 info:')
for r in rows: print(r)

if rows:
    industry = rows[0][2]  # industry column
    print(f'\nIndustry: {industry}')
    
    # Get latest sector stats
    cur.execute("SELECT * FROM sector_daily WHERE industry=? ORDER BY date DESC LIMIT 7", (industry,))
    srows = cur.fetchall()
    print(f'\nSector [{industry}] recent stats:')
    cols = ['industry', 'date', 'avg_pct', 'up_count', 'down_count', 'flat_count', 'total_amount', 'avg_turn', 'top_gainer', 'top_gainer_pct', 'stock_count']
    for r in srows:
        print(f"  {r[1]}: avg={r[2]:.2f}% up={r[3]} down={r[4]} top={r[8]}({r[9]:.1f}%)")
    
    # Get 5-day cumulative return for sector
    cur.execute("SELECT date, avg_pct FROM sector_daily WHERE industry=? ORDER BY date DESC LIMIT 10", (industry,))
    rows5 = cur.fetchall()
    print(f'\n近10日板块累计涨幅:')
    total_5d = sum(r[1] for r in rows5[:5])
    total_10d = sum(r[1] for r in rows5[:10])
    print(f'  近5日: {total_5d:.2f}%')
    print(f'  近10日: {total_10d:.2f}%')
    for r in rows5: print(f'  {r[0]}: {r[1]:+.2f}%')
    
    # Sector rank vs all sectors
    cur.execute("SELECT date FROM sector_daily ORDER BY date DESC LIMIT 1")
    latest_date = cur.fetchone()[0]
    print(f'\n最新日期: {latest_date}')
    cur.execute("SELECT industry, avg_pct FROM sector_daily WHERE date=? ORDER BY avg_pct DESC", (latest_date,))
    all_sectors = cur.fetchall()
    rank = next((i+1 for i, s in enumerate(all_sectors) if s[0]==industry), None)
    print(f'板块当日排名: {rank}/{len(all_sectors)} (avg={next((s[1] for s in all_sectors if s[0]==industry), None):.2f}%)')
    print('\n前5板块:')
    for s in all_sectors[:5]: print(f'  {s[0]}: {s[1]:+.2f}%')

conn.close()
