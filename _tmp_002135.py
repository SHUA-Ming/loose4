import sqlite3, pandas as pd, requests, numpy as np

# 从数据库读K线
db_path = '数据缓存/stock_cache.db'
conn = sqlite3.connect(db_path)
df = pd.read_sql_query(
    "SELECT date,open,high,low,close,volume,amount,turn,pctChg FROM kline_daily WHERE code='sz.002135' ORDER BY date DESC LIMIT 80",
    conn
)
conn.close()
df = df.sort_values('date').reset_index(drop=True)
print(f'总K线条数: {len(df)}')

df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
df['ma20'] = df['close'].rolling(20).mean()
df['ma60'] = df['close'].rolling(60).mean()
df['vol_ma5'] = df['volume'].rolling(5).mean()
df['vol_ratio'] = df['volume'] / df['vol_ma5']

print('\n=== 最近30天K线 ===')
recent = df.tail(30).copy()
recent['volume_w'] = (recent['volume']/10000).round(1)
recent['amount_w'] = (recent['amount']/10000).round(0)
cols = ['date','open','high','low','close','pctChg','volume_w','amount_w','ma5','ma10','ma20','vol_ratio']
print(recent[cols].to_string(index=False, float_format='%.2f'))

last = df.iloc[-1]
print(f'\n=== 最新交易日 {last["date"]} ===')
print(f'收盘: {last["close"]:.2f}  MA5: {last["ma5"]:.2f}  MA10: {last["ma10"]:.2f}  MA20: {last["ma20"]:.2f}')
print(f'量比(今/5日均): {last["vol_ratio"]:.2f}')

print('\n=== 近10天是否有大阳线(涨幅>=4%) ===')
for _, row in df.tail(10).iterrows():
    if row['pctChg'] >= 4 and row['close'] > row['open']:
        print(f'  {row["date"]}  涨幅{row["pctChg"]:.2f}%  开:{row["open"]:.2f} 收:{row["close"]:.2f}  量比:{row["vol_ratio"]:.2f}')

hh = df.tail(20)['high'].max()
ll = df.tail(20)['low'].min()
print(f'\n近20日 高点:{hh:.2f}  低点:{ll:.2f}')
print(f'近60日 高点:{df.tail(60)["high"].max():.2f}  低点:{df.tail(60)["low"].min():.2f}')
print(f'\n支撑压力:')
print(f'  压力1(近20日高): {hh:.2f}')
print(f'  MA10(短期均): {last["ma10"]:.2f}')
print(f'  MA20(中期均): {last["ma20"]:.2f}')
print(f'  支撑2(近20日低): {ll:.2f}')
print(f'  收盘vs MA5: {(last["close"]/last["ma5"]-1)*100:+.2f}%')
print(f'  收盘vs MA10: {(last["close"]/last["ma10"]-1)*100:+.2f}%')
print(f'  收盘vs MA20: {(last["close"]/last["ma20"]-1)*100:+.2f}%')

# 实时行情
code = 'sz002135'
url = 'http://qt.gtimg.cn/q=' + code
r = requests.get(url, timeout=10)
r.encoding = 'gbk'
raw = r.text.split('~')
name = raw[1]
now_price = float(raw[3])
yest_close = float(raw[4])
open_p = float(raw[5])
vol = int(raw[6])
high = float(raw[33])
low = float(raw[34])
amount = float(raw[37])
pct = float(raw[32])
outer = float(raw[7])  # 外盘(买盘)
inner = float(raw[8])  # 内盘(卖盘)

print(f'=== {name} ({code}) 实时行情 (2026-05-08) ===')
print(f'最新价: {now_price}  昨收: {yest_close}  今开: {open_p}')
print(f'最高: {high}  最低: {low}  振幅: {high-low:.2f} ({(high-low)/yest_close*100:.2f}%)')
print(f'涨跌幅: {pct:.2f}%  成交量: {vol}手({vol/10:.0f}万股)  成交额: {amount:.0f}万')
print(f'外盘/内盘: {outer}/{inner}  外内比: {outer/(inner+0.001):.2f}')
print()

# 从数据库读K线
db_path = '数据缓存/stock_cache.db'
conn = sqlite3.connect(db_path)
# 查表名
tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('DB表:', tables)

# 找K线表
kline_table = None
for t in tables:
    if 'kline' in t.lower() or 'daily' in t.lower() or 'hist' in t.lower():
        kline_table = t
        print('使用K线表:', kline_table)
        # 看字段
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        print('字段:', cols)
        break

if kline_table:
    # 找code字段名
    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({kline_table})").fetchall()]
    # 试着找002135
    for code_col in ['code', 'ts_code', 'symbol']:
        if code_col in cols:
            rows = conn.execute(f"SELECT * FROM {kline_table} WHERE {code_col} LIKE '%002135%' LIMIT 3").fetchall()
            if rows:
                print(f'找到数据 (用{code_col}):', rows[0])
                break

conn.close()
