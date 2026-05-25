#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时脚本：检查04-17数据量和真实情绪周期"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
init_db()
conn = get_connection()

# 检查04-17个股数据量
print("=== 各日个股数据量 ===")
r = conn.execute(
    "SELECT date, count(*) as cnt FROM kline_daily "
    "WHERE date >= '2026-04-09' AND code NOT LIKE 'sh.00%' AND code NOT LIKE 'sz.3990%' "
    "GROUP BY date ORDER BY date"
).fetchall()
for row in r:
    print(f'  {row[0]}: {row[1]} stocks')

print("\n=== 04-09到04-16涨停生态(排除04-17) ===")
dates = [row[0] for row in conn.execute(
    "SELECT DISTINCT date FROM kline_daily "
    "WHERE date >= '2026-04-09' AND date <= '2026-04-16' ORDER BY date"
).fetchall()]

for d in dates:
    lu = conn.execute(
        "SELECT count(*) FROM kline_daily WHERE date=? "
        "AND code NOT LIKE 'sh.00%' AND code NOT LIKE 'sz.3990%' "
        "AND CAST(pctChg AS REAL) >= 9.5", [d]
    ).fetchone()[0]
    ld = conn.execute(
        "SELECT count(*) FROM kline_daily WHERE date=? "
        "AND code NOT LIKE 'sh.00%' AND code NOT LIKE 'sz.3990%' "
        "AND CAST(pctChg AS REAL) <= -9.5", [d]
    ).fetchone()[0]
    up = conn.execute(
        "SELECT count(*) FROM kline_daily WHERE date=? "
        "AND code NOT LIKE 'sh.00%' AND code NOT LIKE 'sz.3990%' "
        "AND CAST(pctChg AS REAL) > 0", [d]
    ).fetchone()[0]
    dn = conn.execute(
        "SELECT count(*) FROM kline_daily WHERE date=? "
        "AND code NOT LIKE 'sh.00%' AND code NOT LIKE 'sz.3990%' "
        "AND CAST(pctChg AS REAL) < 0", [d]
    ).fetchone()[0]
    ratio = up / dn if dn > 0 else 99
    print(f'  {d}: 涨停{lu} 跌停{ld} 涨{up}/跌{dn} 涨跌比{ratio:.2f}')

# 看板块排名（04-16最新）
print("\n=== 04-16板块排名TOP20(5日涨幅) ===")
sector_rows = conn.execute(
    "SELECT industry, avg_pct, up_count, down_count FROM sector_daily "
    "WHERE date='2026-04-16' ORDER BY avg_pct DESC LIMIT 20"
).fetchall()
for i, row in enumerate(sector_rows, 1):
    print(f'  {i:2d}. {row[0][:20]:20s} 均涨{float(row[1]):+.2f}% 涨{row[2]}/跌{row[3]}')

print("\n=== 04-16板块排名后20(5日涨幅) ===")
sector_rows2 = conn.execute(
    "SELECT industry, avg_pct, up_count, down_count FROM sector_daily "
    "WHERE date='2026-04-16' ORDER BY avg_pct ASC LIMIT 20"
).fetchall()
for i, row in enumerate(sector_rows2, 1):
    print(f'  {i:2d}. {row[0][:20]:20s} 均涨{float(row[1]):+.2f}% 涨{row[2]}/跌{row[3]}')

# 板块总数
total_sectors = conn.execute(
    "SELECT count(DISTINCT industry) FROM sector_daily WHERE date='2026-04-16'"
).fetchone()[0]
print(f"\n  板块总数: {total_sectors}")
print(f"  后30%淘汰线: 排名>{int(total_sectors*0.7)}")
print(f"  后40%淘汰线(M3): 排名>{int(total_sectors*0.6)}")

conn.close()
print("\n完成")
