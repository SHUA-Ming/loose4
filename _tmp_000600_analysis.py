import requests, json

# Get 120 days for proper MA computation
url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz000600&scale=240&ma=no&datalen=120'
r = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn'})
r.encoding = 'gbk'
data = json.loads(r.text)
print(f'Total records: {len(data)}')

closes = [float(d['close']) for d in data]
opens_  = [float(d['open']) for d in data]
highs  = [float(d['high']) for d in data]
lows   = [float(d['low']) for d in data]
vols   = [float(d['volume']) for d in data]
dates  = [d['day'] for d in data]

def ma(arr, n):
    if len(arr) < n:
        return None
    return sum(arr[-n:]) / n

print(f'=== MAs as of {dates[-1]} ===')
print(f'MA5  = {ma(closes,5):.3f}')
print(f'MA10 = {ma(closes,10):.3f}')
print(f'MA20 = {ma(closes,20):.3f}')
if len(closes)>=60:
    print(f'MA60 = {ma(closes,60):.3f}')
else:
    print(f'MA60 = N/A (only {len(closes)} days)')
if len(closes)>=120:
    print(f'MA120= {ma(closes,120):.3f}')
else:
    print(f'MA120= N/A (only {len(closes)} days)')

# Identify the big candle in last 5-7 days
print('\n=== Recent 15 days candles ===')
for i in range(-15, 0):
    d_date = dates[i]
    o = opens_[i]
    h = highs[i]
    l = lows[i]
    c = closes[i]
    v = vols[i]
    avg20v = sum(vols[max(0,i-20):i])/20 if i < -20 else sum(vols[:i])/max(1,len(vols[:i]))
    vol_ratio = v/avg20v if avg20v > 0 else 0
    chg_pct = (c - o)/o*100
    close_chg = (c - closes[i-1])/closes[i-1]*100 if i > -len(closes) else 0
    body_pct = abs(c - o)/o*100
    print(f'{d_date}: O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f} | 涨跌={close_chg:+.2f}% 实体={body_pct:.1f}% 量={v/1e6:.1f}M 量比={vol_ratio:.2f}x')

# Volume stats
print('\n=== Volume stats ===')
print(f'20日均量: {sum(vols[-20:])/20/1e6:.1f}M')
print(f'60日均量: {sum(vols[-60:])/60/1e6:.1f}M' if len(vols)>=60 else 'NA')
print(f'近期各日量能(最近10天):')
for i in range(-10, 0):
    print(f'  {dates[i]}: {vols[i]/1e6:.1f}M')

# Check S2 setup: big candle detection
print('\n=== S2 大阳检测 ===')
# Check 4/29 specifically
idx_429 = dates.index('2026-04-29') if '2026-04-29' in dates else -1
if idx_429 >= 0:
    o = opens_[idx_429]; c = closes[idx_429]; v = vols[idx_429]
    avg_before = sum(vols[idx_429-20:idx_429])/20
    chg = (c-o)/o*100
    close_chg_vs_prev = (c - closes[idx_429-1])/closes[idx_429-1]*100
    vol_ratio = v/avg_before
    print(f'4/29 大阳: 开={o:.2f} 收={c:.2f} 涨幅(实体)={chg:.1f}% 日涨={close_chg_vs_prev:+.1f}% 量={v/1e6:.1f}M 量比={vol_ratio:.2f}x')
    print(f'  大阳开盘价(止损参考): {o:.2f}')
    # Post-candle days
    print('  大阳后各日量能:')
    for j in range(idx_429+1, len(dates)):
        ratio = vols[j]/v
        print(f'  {dates[j]}: close={closes[j]:.2f} vol={vols[j]/1e6:.1f}M ({ratio:.1%} of 大阳量)')

# Price position vs cost
cost = 9.94
print(f'\n=== 成本分析 ===')
curr = closes[-1]
print(f'成本: {cost:.2f}, 现价(5/8收): {curr:.2f}, 浮盈: {(curr-cost)/cost*100:+.2f}%')
print(f'MA5={ma(closes,5):.3f} (现价/MA5 = {curr/ma(closes,5)*100:.1f}%)')
print(f'MA10={ma(closes,10):.3f} (现价/MA10 = {curr/ma(closes,10)*100:.1f}%)')
print(f'MA20={ma(closes,20):.3f} (现价/MA20 = {curr/ma(closes,20)*100:.1f}%)')
if len(closes)>=60:
    print(f'MA60={ma(closes,60):.3f} (现价/MA60 = {curr/ma(closes,60)*100:.1f}%)')

# Support/resistance levels
print('\n=== 支撑/阻力 ===')
# 20-day high/low
print(f'20日高点: {max(highs[-20:]):.2f}')
print(f'20日低点: {min(lows[-20:]):.2f}')
print(f'60日高点: {max(highs[-60:]):.2f}' if len(highs)>=60 else 'NA')
print(f'近期低点(4/28): {lows[dates.index("2026-04-28")]:.2f}' if '2026-04-28' in dates else '')
print(f'今日最低(5/8): {lows[-1]:.2f}')
print(f'今日最高(5/8): {highs[-1]:.2f}')
