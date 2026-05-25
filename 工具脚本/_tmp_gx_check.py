import sys; sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
import pandas as pd, numpy as np
init_db()
conn = get_connection()
df = pd.read_sql("SELECT * FROM kline_daily WHERE code='sh.603599' ORDER BY date", conn)
if df.empty:
    print('NO DATA for sh.603599')
    sys.exit()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df.dropna(subset=['close','volume'])
print(f'广信股份 sh.603599  共{len(df)}条数据')
print(f'数据区间: {df.date.iloc[0]} ~ {df.date.iloc[-1]}')
print()
d20 = df.tail(20)
print('近20日K线:')
print(f'{"日期":>12s} {"开盘":>7s} {"收盘":>7s} {"最高":>7s} {"最低":>7s} {"涨跌%":>7s} {"成交量":>12s} {"换手":>6s}')
for _, r in d20.iterrows():
    tag = '阳' if r.close >= r.open else '阴'
    print(f'{r.date:>12s} {r.open:>7.2f} {r.close:>7.2f} {r.high:>7.2f} {r.low:>7.2f} {r.pctChg:>+6.2f}% {r.volume:>12,.0f} {r.turn:>5.2f}% {tag}')

cls = df.close.values; ops = df.open.values; his = df.high.values; los = df.low.values
vols = df.volume.values; pcts = df.pctChg.values; turns = df.turn.values
n = len(df)
print()
ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
print(f'MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}')
vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:])
print(f'5日均量={vol5:,.0f}  20日均量={vol20:,.0f}  量比5/20={vol5/vol20:.2f}')
print(f'5日换手均={np.mean(turns[-5:]):.2f}%')
c60 = cls[-60:]
pct60 = (cls[-1]-c60[0])/c60[0]*100
max60 = np.max(c60); dd60 = (cls[-1]-max60)/max60*100
print(f'60日涨幅={pct60:+.1f}%  60日高点={max60:.2f}  回撤={dd60:+.1f}%')
print(f'近5日最低={np.min(los[-5:]):.2f}  近10日最低={np.min(los[-10:]):.2f}')
print(f'近5日最高={np.max(his[-5:]):.2f}')

# 板块
ind_df = pd.read_sql("SELECT industry FROM stock_industry WHERE code='sh.603599'", conn)
if not ind_df.empty:
    ind = ind_df.industry.iloc[0]
    print(f'所属板块: {ind}')
    # 板块动量
    sec_df = pd.read_sql("SELECT * FROM sector_daily WHERE industry=? ORDER BY date", conn, params=[ind])
    if not sec_df.empty:
        sec_df['avg_pct'] = pd.to_numeric(sec_df['avg_pct'], errors='coerce')
        m5 = sec_df['avg_pct'].values[-5:].mean() if len(sec_df) >= 5 else 0
        print(f'板块5日动量: {m5:.2f}%')
        # 排名
        all_sec = pd.read_sql("SELECT * FROM sector_daily ORDER BY industry, date", conn)
        all_sec['avg_pct'] = pd.to_numeric(all_sec['avg_pct'], errors='coerce')
        mom = {}
        for i, g in all_sec.groupby('industry'):
            g = g.sort_values('date')
            if len(g) >= 5:
                mom[i] = g['avg_pct'].values[-5:].mean()
        ranked = sorted(mom.items(), key=lambda x: x[1])
        pos = [i for i, (k, _) in enumerate(ranked) if k == ind]
        if pos:
            pctile = pos[0] / max(len(ranked)-1, 1)
            print(f'板块排名百分位: {pctile:.2%} (共{len(ranked)}个板块)')

# 近5日量价特征
print()
print('=== 量价特征分析 ===')
for i in range(-5, 0):
    d = df.iloc[i]
    body = abs(d.close - d.open)
    amp = (d.high - d.low) / d.close * 100
    vol_r = d.volume / vol20
    tag = '阳' if d.close >= d.open else '阴'
    print(f'  {d.date} {tag} 涨跌{d.pctChg:+.2f}% 振幅{amp:.1f}% 量比{vol_r:.2f} 换手{d.turn:.2f}%')
