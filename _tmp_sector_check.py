import sys
sys.stdout.reconfigure(encoding="utf-8")
import sqlite3
conn = sqlite3.connect('数据缓存/stock_cache.db')
cur = conn.cursor()

industry = 'C41其他制造业'

print(f"=== {industry} 最近10日板块表现 ===")
cur.execute("""SELECT date, avg_pct, up_count, down_count, stock_count, top_gainer, top_gainer_pct
               FROM sector_daily WHERE industry = ? ORDER BY date DESC LIMIT 10""", (industry,))
for r in cur.fetchall():
    print(f"  {r[0]} 日涨:{r[1]:+.2f}% 涨/跌={r[2]}/{r[3]} (共{r[4]}只) 领涨:{r[5]} +{r[6]:.1f}%")

print(f"\n=== {industry} 5日动量 ===")
cur.execute("""SELECT date, avg_pct FROM sector_daily WHERE industry = ? ORDER BY date DESC LIMIT 5""", (industry,))
rows = cur.fetchall()
momentum_5d = sum(r[1] for r in rows)
print(f"  5日累计涨幅: {momentum_5d:+.2f}%")

print(f"\n=== 全行业5日动量排名 ===")
cur.execute("SELECT DISTINCT industry FROM sector_daily")
all_sectors = [r[0] for r in cur.fetchall()]

sector_momentums = []
for sec in all_sectors:
    cur.execute("""SELECT avg_pct FROM sector_daily WHERE industry = ? ORDER BY date DESC LIMIT 5""", (sec,))
    pcts = [r[0] for r in cur.fetchall()]
    if pcts:
        m5 = sum(pcts)
        sector_momentums.append((sec, m5))

sector_momentums.sort(key=lambda x: x[1], reverse=True)
total = len(sector_momentums)
for i, (sec, m5) in enumerate(sector_momentums):
    marker = ' <<<' if sec == industry else ''
    if marker or i < 10 or i >= total - 5:
        print(f"  {i+1:>3}/{total} {sec:35s} 5日:{m5:+.2f}%{marker}")
    elif i == 10:
        print(f"  ...")

target_rank = next(i+1 for i, (s, _) in enumerate(sector_momentums) if s == industry)
pct_rank = target_rank / total * 100
print(f"\n  {industry} 排名: {target_rank}/{total} (前{pct_rank:.0f}%)")

print(f"\n=== 605318 近10日K线(DB) ===")
cur.execute("""SELECT date, open, high, low, close, volume, amount, turn, pctChg
               FROM kline_daily WHERE code = 'sh.605318' ORDER BY date DESC LIMIT 30""")
rows = cur.fetchall()
for r in rows[:10]:
    print(f"  {r[0]} O:{r[1]:.2f} H:{r[2]:.2f} L:{r[3]:.2f} C:{r[4]:.2f} V:{r[5]:,.0f} Turn:{r[7]:.2f}% Chg:{r[8]:+.2f}%")

# 60-day stats
all_rows = rows  # already got 30
cur.execute("""SELECT date, open, high, low, close, volume, amount, turn, pctChg
               FROM kline_daily WHERE code = 'sh.605318' ORDER BY date DESC LIMIT 60""")
all_60 = cur.fetchall()

# market cap estimate
last = rows[0]
if last[7] > 0:
    circ_shares = last[5] / (last[7] / 100)
    circ_mv = circ_shares * last[4] / 1e8
    print(f"\n  估算流通市值: {circ_mv:.1f} 亿")

# Check if had limit-up in 60 days
limit_ups = [r for r in all_60 if r[8] >= 9.5]
print(f"\n  近60日涨停次数: {len(limit_ups)}")
for r in limit_ups:
    print(f"    {r[0]} +{r[8]:.2f}%")

# 60d max/min
prices_60 = [r[4] for r in all_60]
max_60 = max(prices_60)
min_60 = min(prices_60)
gain_60 = (prices_60[0] - prices_60[-1]) / prices_60[-1] * 100
max_idx = prices_60.index(max_60)
drawdown = (max_60 - prices_60[0]) / max_60 * 100
print(f"\n  近60日: 最高={max_60:.2f} 最低={min_60:.2f}")
print(f"  近60日涨幅: {gain_60:+.2f}%")
print(f"  从高点回撤: {drawdown:.2f}%")

# MA60
cur.execute("""SELECT close FROM kline_daily WHERE code = 'sh.605318' ORDER BY date DESC LIMIT 60""")
closes_60 = [r[0] for r in cur.fetchall()]
ma60 = sum(closes_60) / len(closes_60)
ma20 = sum(closes_60[:20]) / 20
ma10 = sum(closes_60[:10]) / 10
ma5 = sum(closes_60[:5]) / 5
print(f"\n  MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60:.2f}")
print(f"  现价({prices_60[0]:.2f}) vs MA60({ma60:.2f}): {'>' if prices_60[0] > ma60 else '<'} MA60")

# sector stocks tier ranking
print(f"\n=== {industry} 板块内个股5日涨幅排名 ===")
cur.execute("SELECT code, code_name FROM stock_industry WHERE industry = ?", (industry,))
sector_stocks = cur.fetchall()

stock_chgs = []
for scode, sname in sector_stocks:
    cur.execute("""SELECT pctChg FROM kline_daily WHERE code = ? ORDER BY date DESC LIMIT 5""", (scode,))
    pcts = [r[0] for r in cur.fetchall()]
    if len(pcts) >= 3:
        chg_5d = sum(pcts)
        stock_chgs.append((scode, sname, chg_5d))

stock_chgs.sort(key=lambda x: x[2], reverse=True)
for i, (c, n, chg) in enumerate(stock_chgs):
    marker = ' <<<' if '605318' in c else ''
    print(f"  {i+1:>3}/{len(stock_chgs)} {c} {n:12s} 5日:{chg:+.2f}%{marker}")

# 5d volume info
vols_5 = [r[5] for r in rows[:5]]
vols_20 = [r[5] for r in all_rows[:20]]
avg_v5 = sum(vols_5) / len(vols_5)
avg_v20 = sum(vols_20) / len(vols_20) if vols_20 else 1
print(f"\n=== 量能分析 ===")
print(f"  5日均量: {avg_v5:,.0f}")
print(f"  20日均量: {avg_v20:,.0f}")
print(f"  量比(5/20): {avg_v5/avg_v20:.2f}")

conn.close()
