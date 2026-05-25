#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
from datetime import datetime, timedelta
import requests
import numpy as np
import pandas as pd
import baostock as bs

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def qq_rt(sym):
    r = requests.get(f'https://qt.gtimg.cn/q={sym}', timeout=10)
    r.encoding = 'gbk'
    items = r.text.strip().split('"')[1].split('~')

    def f(i):
        try:
            return float(items[i])
        except Exception:
            return 0.0

    return {
        'name': items[1], 'code': items[2],
        'cur': f(3), 'pre': f(4), 'open': f(5),
        'hi': f(33), 'lo': f(34),
        'chg': f(31), 'pct': f(32),
        'vol': f(36), 'amt_wan': f(37), 'turn': f(38),
        'outer': f(7), 'inner': f(8),
        'upd': items[30] if len(items) > 30 else ''
    }

# 大盘
idx = {s: qq_rt(s) for s in ['sh000001', 'sz399001', 'sz399006', 'sh000300']}

# 中兵红箭实时
rt = qq_rt('sz000519')
oi = rt['outer'] / max(rt['inner'], 1)
amp = (rt['hi'] - rt['lo']) / max(rt['pre'], 1e-9) * 100
from_hi = (rt['hi'] - rt['cur']) / max(rt['hi'], 1e-9) * 100

# 军工板块代理
peers = [
    ('sz000519', '中兵红箭'),
    ('sh600677', '航新科技'),
    ('sz002288', '超华科技'),
    ('sz000065', '北方国际'),
    ('sh601989', '中国核电'),
    ('sh601106', '中国一重'),
    ('sh600760', '中航沙河'),
    ('sz002013', '中航机电'),
    ('sh601900', '中国铁路'),
]
peer_rt = []
for sym, default_name in peers:
    try:
        d = qq_rt(sym)
        peer_rt.append((sym, d['name'] or default_name, d['pct'], d['turn'], d['amt_wan'] / 10000))
    except Exception:
        continue

avg_peer_pct = float(np.mean([x[2] for x in peer_rt])) if peer_rt else 0.0
med_peer_pct = float(np.median([x[2] for x in peer_rt])) if peer_rt else 0.0

# 120日
lg = bs.login()
end_date = datetime.now().strftime('%Y-%m-%d')
start_date = (datetime.now() - timedelta(days=240)).strftime('%Y-%m-%d')
rs = bs.query_history_k_data_plus(
    'sz.000519',
    'date,open,high,low,close,volume,amount,pctChg',
    start_date=start_date,
    end_date=end_date,
    frequency='d',
    adjustflag='2'
)
rows = []
while rs.error_code == '0' and rs.next():
    rows.append(rs.get_row_data())
bs.logout()

cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pctChg']
df = pd.DataFrame(rows, columns=cols)
for c in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pctChg']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df.dropna().tail(120).reset_index(drop=True)

close = df['close'].values
high = df['high'].values
low = df['low'].values
vol = df['volume'].values

ma5 = float(np.mean(close[-5:]))
ma10 = float(np.mean(close[-10:]))
ma20 = float(np.mean(close[-20:]))
ma60 = float(np.mean(close[-60:]))
ma120 = float(np.mean(close[-120:]))

hh20 = float(np.max(high[-20:]))
ll20 = float(np.min(low[-20:]))
hh60 = float(np.max(high[-60:]))
ll60 = float(np.min(low[-60:]))

ret5 = (close[-1] / close[-6] - 1) * 100 if len(close) >= 6 else 0
ret10 = (close[-1] / close[-11] - 1) * 100 if len(close) >= 11 else 0
ret20 = (close[-1] / close[-21] - 1) * 100 if len(close) >= 21 else 0

tr = []
for i in range(1, len(df)):
    tr_i = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    tr.append(tr_i)
atr14 = float(np.mean(tr[-14:])) if len(tr) >= 14 else 0

print('=== 实时大盘 ===')
for s, name in [('sh000001', '上证'), ('sz399001', '深证'), ('sz399006', '创业板'), ('sh000300', '沪深300')]:
    d = idx[s]
    print(f"{name}: {d['cur']:.2f} ({d['pct']:+.2f}%)")

print('\n=== 军工/国防板块代理 ===')
for sym, name, pct, turn, amt_yi in sorted(peer_rt, key=lambda x: x[2], reverse=True):
    print(f"{sym} {name:<10} 涨跌{pct:+.2f}% 换手{turn:.2f}% 成交{amt_yi:.2f}亿")
print(f"板块样本平均: {avg_peer_pct:+.2f}%  中位数: {med_peer_pct:+.2f}%")

print('\n=== 中兵红箭 实时 ===')
print(f"价:{rt['cur']:.2f} 涨跌:{rt['pct']:+.2f}% 高/低:{rt['hi']:.2f}/{rt['lo']:.2f} 振幅:{amp:.2f}%")
print(f"换手:{rt['turn']:.2f}% 成交:{rt['amt_wan']/10000:.2f}亿 外内比:{oi:.2f} 距日高回落:{from_hi:.2f}% 更新:{rt['upd']}")

print('\n=== 中兵红箭 120日结构 ===')
print(f"MA5:{ma5:.2f} MA10:{ma10:.2f} MA20:{ma20:.2f} MA60:{ma60:.2f} MA120:{ma120:.2f}")
print(f"20日高低:{hh20:.2f}/{ll20:.2f} 60日高低:{hh60:.2f}/{ll60:.2f}")
print(f"5/10/20日收益:{ret5:+.2f}%/{ret10:+.2f}%/{ret20:+.2f}% ATR14:{atr14:.2f}")

print('\n=== 关键位 ===')
print(f"压力位: {rt['hi']:.2f} / {hh20:.2f} / {hh60:.2f}")
print(f"支撑位: {ma5:.2f} / {ma10:.2f} / {ma20:.2f}")
