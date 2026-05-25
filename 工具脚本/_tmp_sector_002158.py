import sys
sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection
conn = get_connection()

# C34 sector recent data
print('=== C34通用设备制造业 近期表现 ===')
rows = conn.execute("SELECT * FROM sector_daily WHERE industry LIKE '%C34%' ORDER BY date DESC LIMIT 15").fetchall()
for r in rows:
    print(f'  {r[1]}  avg_pct:{r[2]:.2f}%  涨:{r[3]} 跌:{r[4]} 平:{r[5]}  成交额:{r[6]/1e8:.0f}亿  龙头:{r[8]}({r[9]:.1f}%)')

# Sector ranking (rank not stored, compute from all sectors same day)
print('\n=== 最近交易日板块排名 ===')
latest_date = conn.execute("SELECT MAX(date) FROM sector_daily").fetchone()[0]
print(f'Latest date: {latest_date}')
all_sectors = conn.execute("SELECT industry, avg_pct FROM sector_daily WHERE date=? ORDER BY avg_pct DESC", (latest_date,)).fetchall()
for i, s in enumerate(all_sectors):
    if 'C34' in s[0]:
        print(f'C34 rank: {i+1}/{len(all_sectors)}  avg_pct: {s[1]:.2f}%')
        break

# Get 5 recent days ranking
print('\n=== 近5日C34排名 ===')
recent_dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM sector_daily ORDER BY date DESC LIMIT 5").fetchall()]
for d in recent_dates:
    sectors = conn.execute("SELECT industry, avg_pct FROM sector_daily WHERE date=? ORDER BY avg_pct DESC", (d,)).fetchall()
    for i, s in enumerate(sectors):
        if 'C34' in s[0]:
            print(f'  {d}: rank {i+1}/{len(sectors)}  avg_pct: {s[1]:.2f}%')
            break

# Trade log for 002158
print('\n=== Trade log for 002158 ===')
trades = conn.execute("SELECT * FROM trade_log WHERE code LIKE '%002158%'").fetchall()
for t in trades:
    print(f'  {t}')
