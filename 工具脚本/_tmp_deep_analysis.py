"""分析近期市场真正领涨板块 vs 我们选股系统的选择"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
import pandas as pd, numpy as np
init_db()
conn = get_connection()

print("=" * 80)
print("  Part 1: 近5日/10日板块涨幅排名（谁在真正涨？）")
print("=" * 80)

sec_df = pd.read_sql("SELECT * FROM sector_daily ORDER BY industry, date", conn)
for c in sec_df.columns:
    if c not in ['industry', 'date']:
        sec_df[c] = pd.to_numeric(sec_df[c], errors='coerce')

# 计算各板块5日/10日累计涨幅
results = []
for ind, grp in sec_df.groupby('industry'):
    grp = grp.sort_values('date').reset_index(drop=True)
    if len(grp) < 10:
        continue
    m5 = grp['avg_pct'].values[-5:].sum()
    m10 = grp['avg_pct'].values[-10:].sum()
    m1 = grp['avg_pct'].values[-1]
    results.append({'industry': ind, 'pct_1d': m1, 'pct_5d': m5, 'pct_10d': m10})

rdf = pd.DataFrame(results).sort_values('pct_5d', ascending=False)

print("\n  近5日涨幅TOP20板块:")
print(f"  {'排名':>4s} {'板块':30s} {'今日':>7s} {'近5日':>7s} {'近10日':>8s}")
print("  " + "-" * 65)
for i, r in rdf.head(20).iterrows():
    rank = list(rdf.index).index(i) + 1
    print(f"  {rank:>4d} {r.industry:30s} {r.pct_1d:>+6.2f}% {r.pct_5d:>+6.2f}% {r.pct_10d:>+7.2f}%")

print(f"\n  近5日跌幅TOP10板块:")
for i, r in rdf.tail(10).iterrows():
    rank = len(rdf) - list(rdf.index[::-1]).index(i)
    print(f"  {rank:>4d} {r.industry:30s} {r.pct_1d:>+6.2f}% {r.pct_5d:>+6.2f}% {r.pct_10d:>+7.2f}%")

print("\n" + "=" * 80)
print("  Part 2: 各板块内近5日涨幅最大个股（哪些票在暴涨？）")
print("=" * 80)

# 查各板块个股近5日涨幅
ind_map = pd.read_sql("SELECT code, industry, code_name FROM stock_industry", conn)
ind_dict = dict(zip(ind_map['code'], ind_map['industry']))
name_dict = dict(zip(ind_map['code'], ind_map['code_name']))

# 查所有股票近5日涨幅
codes = [r[0] for r in conn.execute('SELECT DISTINCT code FROM kline_daily').fetchall()]
stock_chgs = []
for code in codes:
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = pd.read_sql("SELECT date, close, pctChg, volume FROM kline_daily WHERE code=? ORDER BY date", conn, params=[code])
    if len(df) < 10:
        continue
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['pctChg'] = pd.to_numeric(df['pctChg'], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    cls = df['close'].values
    pcts = df['pctChg'].values
    if len(cls) < 6:
        continue
    chg5 = (cls[-1] / cls[-6] - 1) * 100
    chg1 = pcts[-1]
    stock_chgs.append({
        'code': code,
        'name': name_dict.get(code, ''),
        'industry': ind_dict.get(code, ''),
        'price': cls[-1],
        'chg_1d': chg1,
        'chg_5d': chg5,
    })

sdf = pd.DataFrame(stock_chgs)

# 找医药、电缆相关板块
for keyword in ['医药', '电缆', '电气机械', '电子设备', '计算机', '有色金属', '汽车', '房地产', '化学原料', '农副食品']:
    subset = sdf[sdf['industry'].str.contains(keyword, na=False)].sort_values('chg_5d', ascending=False)
    if subset.empty:
        continue
    print(f"\n  [{keyword}] 板块内近5日涨幅TOP5:")
    for _, r in subset.head(5).iterrows():
        print(f"    {r.code:12s} {r['name']:8s} 现价{r.price:>7.2f}  5日{r.chg_5d:>+6.1f}%  今日{r.chg_1d:>+5.1f}%")

# 全市场近5日涨幅TOP30
print(f"\n  全市场近5日涨幅TOP30:")
top30 = sdf.sort_values('chg_5d', ascending=False).head(30)
print(f"  {'排名':>4s} {'代码':12s} {'名称':10s} {'板块':20s} {'现价':>7s} {'5日涨':>7s} {'今日':>6s}")
print("  " + "-" * 75)
for rank, (_, r) in enumerate(top30.iterrows(), 1):
    print(f"  {rank:>4d} {r.code:12s} {r['name']:10s} {r.industry[:18]:20s} {r.price:>7.2f} {r.chg_5d:>+6.1f}% {r.chg_1d:>+5.1f}%")

print("\n" + "=" * 80)
print("  Part 3: 我们选股系统选出的票 vs 实际表现")
print("=" * 80)

# 我们关注/操作过的票
our_picks = {
    'sh.600309': ('万华化学', '92.2买入89卖出', '-3.47%'),
    'sh.603599': ('广信股份', '14.89买入14.22卖出', '-4.5%'),
    'sh.600251': ('冠农股份', '11.00建仓', '持仓中'),
}
for code, (name, action, pnl) in our_picks.items():
    df = pd.read_sql("SELECT date, close, pctChg FROM kline_daily WHERE code=? ORDER BY date", conn, params=[code])
    if df.empty:
        continue
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    ind = ind_dict.get(code, '')
    cls = df['close'].values
    chg5 = (cls[-1] / cls[-6] - 1) * 100 if len(cls) >= 6 else 0
    # 找板块排名
    sec_pct = rdf[rdf['industry'] == ind]['pct_5d'].values[0] if ind in rdf['industry'].values else 0
    sec_rank = (rdf['pct_5d'] > sec_pct).sum() + 1
    total = len(rdf)
    print(f"  {code} {name:6s}  操作:{action:20s}  盈亏:{pnl}  板块:{ind[:15]}  板块5日:{sec_pct:+.2f}%  板块排名:{sec_rank}/{total}")
