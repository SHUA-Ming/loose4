import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from sina_kline import fetch_kline
import pandas as pd

code = 'sh.603599'
start_date = '2026-04-01'
end_date   = '2026-04-13'

df = fetch_kline(code, days=30)
df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]

print("【最近K线数据】")
print(df.to_string(index=False))
print(f"\n最新收盘（{df.iloc[-1]['date']}）: {df.iloc[-1]['close']:.2f}")
