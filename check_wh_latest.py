import sys
sys.stdout.reconfigure(encoding="utf-8")
import requests

def fetch_realtime(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split("~")
    
    def sf(i):
        try:
            return float(items[i])
        except:
            return 0.0

    name = items[1]
    code = items[2]
    cur = sf(3)
    pre = sf(4)
    opn = sf(5)
    hi = sf(33)
    lo = sf(34)
    amp = sf(43)
    chg = sf(31)
    pct = sf(32)
    vol = sf(36)
    amt = sf(37)
    turn = sf(38)
    outer = sf(7)
    inner = sf(8)
    upd = items[30] if len(items) > 30 else ""

    print(f"{'='*60}")
    print(f"  {name} ({code})")
    print(f"  更新时间: {upd}")
    print(f"{'='*60}")
    print(f"  当前价: {cur:.2f}   昨收: {pre:.2f}   开盘: {opn:.2f}")
    print(f"  最高: {hi:.2f}   最低: {lo:.2f}   振幅: {amp:.2f}%")
    print(f"  涨跌额: {chg:+.2f}   涨跌幅: {pct:+.2f}%")
    print(f"  成交量: {vol:,.0f}手   成交额: {amt:,.0f}万")
    print(f"  换手率: {turn:.2f}%")
    print(f"  外盘: {outer:,.0f}手  内盘: {inner:,.0f}手  外/内比: {outer/max(inner,1):.2f}")
    print(f"\n  --- 五档盘口 ---")
    for i in range(5, 0, -1):
        sp = sf(19 + i*2)
        sv = sf(18 + i*2)
        print(f"  卖{i}: {sp:.2f} x {sv:,.0f}")
    for i in range(1, 6):
        bp = sf(9 + i*2)
        bv = sf(8 + i*2)
        print(f"  买{i}: {bp:.2f} x {bv:,.0f}")
    print()

fetch_realtime("sh600309")  # 万华化学

# 也拉最近K线
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '工具脚本'))
from sina_kline import fetch_kline
import pandas as pd

code = 'sh.600309'
start_date = '2026-04-01'
end_date = '2026-04-13'

df = fetch_kline(code, days=30)
df = df[(df['date'] >= start_date) & (df['date'] <= end_date)].reset_index(drop=True)
for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'pctChg']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

print("【最近K线数据】")
print(df.to_string(index=False))
if len(df) > 0:
    print(f"\n最新收盘（{df.iloc[-1]['date']}）: {df.iloc[-1]['close']:.2f}")
