import sqlite3
import pandas as pd

conn = sqlite3.connect('../数据缓存/stock_cache.db')

# Get all available dates
dates = conn.execute("SELECT DISTINCT date FROM sector_daily ORDER BY date DESC LIMIT 10").fetchall()
print("Available dates:", [d[0] for d in dates])
recent_dates = [d[0] for d in dates][:5]

# Get sector data for recent days
df = pd.read_sql(f"""
    SELECT industry, date, avg_pct, up_count, down_count, total_amount, top_gainer, top_gainer_pct, stock_count
    FROM sector_daily 
    WHERE date >= '{recent_dates[-1]}'
    ORDER BY date, avg_pct DESC
""", conn)

# Pivot to see trend
pivot = df.pivot(index='industry', columns='date', values='avg_pct').fillna(0)
# Add 5-day sum
all_cols = sorted(pivot.columns)
pivot['5d_sum'] = pivot[all_cols].sum(axis=1)
pivot = pivot.sort_values('5d_sum', ascending=False)

print("\n====== 板块近5日涨跌排名 ======")
print(f"{'板块':<30} {'5d合计':>8}", "  ".join([f"{d[-5:]:>8}" for d in all_cols]))
for idx, (industry, row) in enumerate(pivot.iterrows(), 1):
    vals = "  ".join([f"{row[d]:>+7.2f}%" for d in all_cols])
    print(f"{idx:3}. {industry:<26} {row['5d_sum']:>+7.2f}%  {vals}")

print("\n====== 今日(最新)板块top20 ======")
latest = all_cols[-1]
top20 = pivot.sort_values(latest, ascending=False).head(20)
for idx, (industry, row) in enumerate(top20.iterrows(), 1):
    print(f"{idx:3}. {industry:<28} 今日:{row[latest]:>+6.2f}%  5日:{row['5d_sum']:>+6.2f}%")

print("\n====== 今日跌幅最大板块 ======")
bot10 = pivot.sort_values(latest, ascending=True).head(10)
for idx, (industry, row) in enumerate(bot10.iterrows(), 1):
    print(f"{idx:3}. {industry:<28} 今日:{row[latest]:>+6.2f}%  5日:{row['5d_sum']:>+6.2f}%")

conn.close()
