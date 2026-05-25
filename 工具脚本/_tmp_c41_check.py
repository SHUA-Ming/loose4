import db_cache
conn = db_cache.get_connection()
cur = conn.cursor()

# Get C41 sector recent performance
cur.execute("""
    SELECT date, avg_pct, up_count, down_count, total_amount
    FROM sector_daily 
    WHERE industry='C41其他制造业'
    ORDER BY date DESC LIMIT 10
""")
rows = cur.fetchall()
print("C41 其他制造业 近10日表现:")
print(f"{'日期':>12} {'平均涨跌%':>8} {'上涨':>4} {'下跌':>4} {'成交额(亿)':>10}")
for r in rows:
    print(f"{r[0]:>12} {r[1]:>8.2f}% {r[2]:>4} {r[3]:>4} {r[4]/1e8:>10.1f}")

# Get sector ranking for C41 in last 5 days
cur.execute("""
    SELECT industry, SUM(avg_pct) as cum_pct 
    FROM sector_daily 
    WHERE date >= (SELECT date FROM sector_daily ORDER BY date DESC LIMIT 1 OFFSET 4)
    GROUP BY industry 
    ORDER BY cum_pct DESC
""")
all_sectors = cur.fetchall()
total = len(all_sectors)
for i, (ind, pct) in enumerate(all_sectors):
    if 'C41' in ind:
        pct_rank = (i+1)/total*100
        print(f"\nC41排名: {i+1}/{total} (前{pct_rank:.0f}%), 5日累涨: {pct:.2f}%")
        break

# Also show top/bottom 5 for context
print("\n前5强板块:")
for ind, pct in all_sectors[:5]:
    print(f"  {ind}: {pct:.2f}%")
print("\n后5弱板块:")
for ind, pct in all_sectors[-5:]:
    print(f"  {ind}: {pct:.2f}%")

conn.close()
