#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from sina_kline import fetch_kline
import pandas as pd
import numpy as np
from datetime import datetime

# ========== PART 1: Real-time quote ==========
def fetch_rt(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split("~")
    sf = lambda i: float(items[i]) if items[i] else 0.0
    print(f"=== RT: {items[1]} ({items[2]}) ===")
    print(f"Time: {items[30]}")
    print(f"Cur:{sf(3):.2f} Pre:{sf(4):.2f} Open:{sf(5):.2f}")
    print(f"Hi:{sf(33):.2f} Lo:{sf(34):.2f} Amp:{sf(43):.2f}%")
    print(f"Chg:{sf(31):+.2f} Pct:{sf(32):+.2f}%")
    print(f"Vol:{sf(36):,.0f}shou Amt:{sf(37):,.0f}wan")
    print(f"Turn:{sf(38):.2f}%")
    print(f"Outer:{sf(7):,.0f} Inner:{sf(8):,.0f} O/I:{sf(7)/max(sf(8),1):.2f}")
    for i in range(5, 0, -1):
        print(f"  S{i}: {sf(19+i*2):.2f} x {sf(18+i*2):,.0f}")
    for i in range(1, 6):
        print(f"  B{i}: {sf(9+i*2):.2f} x {sf(8+i*2):,.0f}")
    print()

fetch_rt("sh600084")

# ========== PART 2: 120-day history ==========
code = 'sh.600084'
name = 'ZXNY'
days = 120
end_date = datetime.now().strftime('%Y-%m-%d')
start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

df = fetch_kline(code, days=days)

for col in ['open','high','low','close','volume','amount','turn','pctChg']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

print(f'=== {name} ({code}) 120d Kline ===')
print(f'Range: {df["date"].iloc[0]} ~ {df["date"].iloc[-1]}, Total: {len(df)} days')
print()

for _, row in df.iterrows():
    vol_s = int(row['volume']) if pd.notna(row['volume']) else 0
    amt_w = row['amount'] / 10000
    print(f'{row["date"]} O:{row["open"]:.2f} C:{row["close"]:.2f} H:{row["high"]:.2f} L:{row["low"]:.2f} Chg:{row["pctChg"]:+.2f}% Vol:{vol_s:,} Amt:{amt_w:.0f}w Turn:{row["turn"]:.2f}%')

closes = df['close'].values
opens = df['open'].values
highs = df['high'].values
lows = df['low'].values
vols = df['volume'].values
turns = df['turn'].values
pcts = df['pctChg'].values

# MA
ma5 = np.mean(closes[-5:])
ma10 = np.mean(closes[-10:])
ma20 = np.mean(closes[-20:])
ma60 = np.mean(closes[-60:]) if len(closes)>=60 else np.mean(closes)
ma120 = np.mean(closes) if len(closes)>=120 else np.mean(closes)

avg_vol_5 = np.mean(vols[-5:])
avg_vol_10 = np.mean(vols[-10:])
avg_vol_20 = np.mean(vols[-20:])
vol_ratio = vols[-1]/avg_vol_5 if avg_vol_5 > 0 else 0

recent_high = max(closes[-20:])
recent_low = min(closes[-20:])
high_120 = max(closes)
low_120 = min(closes)

print()
print('='*80)
print('TECH SUMMARY')
print('='*80)
print(f'Latest close: {closes[-1]:.2f}')
print(f'MA5={ma5:.3f} MA10={ma10:.3f} MA20={ma20:.3f} MA60={ma60:.3f} MA120={ma120:.3f}')

if closes[-1]>ma5>ma10>ma20:
    ma_status = 'BULLISH'
elif closes[-1]<ma5<ma10<ma20:
    ma_status = 'BEARISH'
else:
    ma_status = 'MIXED'
print(f'MA align: {ma_status}')
print(f'vs MA5: {(closes[-1]/ma5-1)*100:+.2f}%')
print(f'vs MA10: {(closes[-1]/ma10-1)*100:+.2f}%')
print(f'vs MA20: {(closes[-1]/ma20-1)*100:+.2f}%')
print(f'vs MA60: {(closes[-1]/ma60-1)*100:+.2f}%')

print(f'AvgVol 5d:{avg_vol_5:,.0f} 10d:{avg_vol_10:,.0f} 20d:{avg_vol_20:,.0f}')
print(f'VolRatio(vs5d): {vol_ratio:.2f}')
print(f'20d H:{recent_high:.2f} L:{recent_low:.2f}')
print(f'120d H:{high_120:.2f} L:{low_120:.2f}')
print(f'Sum5d: {sum(pcts[-5:]):.2f}%')
print(f'Sum10d: {sum(pcts[-10:]):.2f}%')
print(f'Sum20d: {sum(pcts[-20:]):.2f}%')

# MACD
ema12 = pd.Series(closes).ewm(span=12).mean().values
ema26 = pd.Series(closes).ewm(span=26).mean().values
dif = ema12 - ema26
dea = pd.Series(dif).ewm(span=9).mean().values
macd = 2*(dif - dea)
print(f'MACD: DIF={dif[-1]:.4f} DEA={dea[-1]:.4f} MACD={macd[-1]:.4f}')
if dif[-1]>dea[-1] and dif[-2]<=dea[-2]:
    print('*** GOLDEN CROSS! ***')
elif dif[-1]<dea[-1] and dif[-2]>=dea[-2]:
    print('*** DEATH CROSS! ***')

# RSI
delta = pd.Series(pcts)
gain = delta.where(delta>0, 0).rolling(14).mean()
loss = (-delta.where(delta<0, 0)).rolling(14).mean()
rs_val = gain / loss.replace(0, np.nan)
rsi14 = 100 - (100/(1+rs_val))
print(f'RSI14: {rsi14.iloc[-1]:.1f}')

# KDJ RSV
low_9 = pd.Series(lows).rolling(9).min()
high_9 = pd.Series(highs).rolling(9).max()
rsv = (closes[-1] - low_9.iloc[-1]) / (high_9.iloc[-1] - low_9.iloc[-1]) * 100
print(f'RSV9: {rsv:.1f}')

# Streak
streak = 0
if pcts[-1] > 0:
    for i in range(len(pcts)-1, -1, -1):
        if pcts[i] > 0: streak += 1
        else: break
    print(f'Up streak: {streak}')
else:
    for i in range(len(pcts)-1, -1, -1):
        if pcts[i] <= 0: streak += 1
        else: break
    print(f'Down streak: {streak}')

# Recent 15 days detail
print()
print('=== RECENT 15 DAYS ===')
for i in range(-15, 0):
    idx = len(df)+i
    if idx < 0: continue
    row = df.iloc[idx]
    body = row['close'] - row['open']
    upper = row['high'] - max(row['close'], row['open'])
    lower = min(row['close'], row['open']) - row['low']
    real_body = abs(body)
    if real_body < 0.02:
        bar_type = 'DOJI'
    elif body > 0:
        bar_type = 'BULL'
    else:
        bar_type = 'BEAR'
    print(f'{row["date"]} C:{row["close"]:.2f} Chg:{row["pctChg"]:+.2f}% Vol:{int(row["volume"]):,} Turn:{row["turn"]:.2f}% {bar_type} Body:{real_body:.3f} Up:{upper:.3f} Dn:{lower:.3f}')

# Key levels
print()
print('=== KEY LEVELS ===')
for level in [6.0, 6.5, 7.0, 7.5, 8.0, 5.5, 5.0]:
    dist = (level - closes[-1])/closes[-1]*100
    print(f'  {level:.1f} ({dist:+.1f}%)')
print(f'  20d H: {recent_high:.2f} ({(recent_high-closes[-1])/closes[-1]*100:+.1f}%)')
print(f'  20d L: {recent_low:.2f} ({(recent_low-closes[-1])/closes[-1]*100:+.1f}%)')
print(f'  120d H: {high_120:.2f} ({(high_120-closes[-1])/closes[-1]*100:+.1f}%)')
print(f'  120d L: {low_120:.2f} ({(low_120-closes[-1])/closes[-1]*100:+.1f}%)')
