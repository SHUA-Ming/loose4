#!/usr/bin/env python3
import sys; sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
import pandas as pd

init_db()
conn = get_connection()
df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY date', conn)
dates = sorted(df['date'].unique())[-5:]
recent = df[df['date'].isin(dates)]
mom5 = recent.groupby('industry')['avg_pct'].sum().reset_index()
mom5.columns = ['industry','mom5']
mom5 = mom5.sort_values('mom5', ascending=False).reset_index(drop=True)
mom5['rank'] = range(1, len(mom5)+1)
total = len(mom5)

c34 = mom5[mom5['industry']=='C34通用设备制造业'].iloc[0]
rank = c34['rank']
pct = rank / total * 100
tier = "TOP30%" if pct<=30 else "TOP50%" if pct<=50 else "后50%" if pct<=70 else "后30%"
print(f"C34通用设备 5日动量: {c34['mom5']:+.2f}%  排名: {int(rank)}/{total} ({tier})")

print(f"\n板块TOP10:")
for _, r in mom5.head(10).iterrows():
    print(f"  {int(r['rank']):2d}. {r['industry']}  {r['mom5']:+.2f}%")

print(f"\nC34附近:")
for _, r in mom5[(mom5['rank']>=rank-2) & (mom5['rank']<=rank+2)].iterrows():
    tag = " ★" if r['industry']=='C34通用设备制造业' else ""
    print(f"  {int(r['rank']):2d}. {r['industry']}  {r['mom5']:+.2f}%{tag}")

conn.close()
