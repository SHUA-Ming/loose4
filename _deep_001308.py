import sqlite3, pandas as pd, numpy as np, urllib.request, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect(r'数据缓存/stock_cache.db')

# ============ 1. 康冠科技 120日K线 ============
print("=" * 95)
print("【一】康冠科技(sz.001308) 近120日K线技术面深度分析")
print("=" * 95)

df = pd.read_sql(
    'SELECT date, open, high, low, close, volume, amount FROM kline_daily WHERE code=? ORDER BY date',
    conn, params=('sz.001308',))
df['date'] = pd.to_datetime(df['date'])

total_days = len(df)
print(f"  数据库总共有 {total_days} 个交易日数据")

df120 = df.tail(120).copy().reset_index(drop=True)
print(f"  取近120日: {df120.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df120.iloc[-1]['date'].strftime('%Y-%m-%d')}")

last = df120.iloc[-1]
close = last['close']

# -- 均线体系 --
for n in [5, 10, 20, 30, 60, 120]:
    tail = df.tail(n)
    if len(tail) >= n:
        ma = tail['close'].mean()
        dist = (close - ma) / ma * 100
        print(f"  MA{n}={ma:.2f} (距离{dist:+.1f}%)")

# -- 分阶段趋势 --
print(f"\n  --- 分阶段趋势 ---")
for label, n in [('近5日',5), ('近10日',10), ('近20日',20), ('近30日',30), ('近60日',60), ('近120日',120)]:
    seg = df120.tail(n) if n <= len(df120) else df120
    chg = (seg.iloc[-1]['close'] / seg.iloc[0]['close'] - 1) * 100
    high_n = seg['high'].max()
    low_n = seg['low'].min()
    amp = (high_n - low_n) / low_n * 100
    avg_vol = seg['volume'].mean()
    print(f"  {label:6s}: 涨跌{chg:+.1f}% | 区间高{high_n:.2f}-低{low_n:.2f} | 振幅{amp:.1f}% | 均量{avg_vol/10000:.0f}万")

# -- 量能分析 --
print(f"\n  --- 量能分析 ---")
vol5 = df120.tail(5)['volume'].mean()
vol10 = df120.tail(10)['volume'].mean()
vol20 = df120.tail(20)['volume'].mean()
vol60 = df120.tail(60)['volume'].mean()
print(f"  5日均量: {vol5/10000:.0f}万 | 10日: {vol10/10000:.0f}万 | 20日: {vol20/10000:.0f}万 | 60日: {vol60/10000:.0f}万")
print(f"  量比(5/20): {vol5/vol20:.2f} | 量比(5/60): {vol5/vol60:.2f}")

# -- 近20日详细K线 --
print(f"\n  --- 近20日K线明细 ---")
df20 = df120.tail(20).copy().reset_index(drop=True)
for _, row in df20.iterrows():
    d = str(row['date'])[:10]
    chg_d = (row['close'] - row['open']) / row['open'] * 100
    body = abs(row['close'] - row['open'])
    upper = row['high'] - max(row['open'], row['close'])
    lower = min(row['open'], row['close']) - row['low']
    bar_type = "阳" if row['close'] >= row['open'] else "阴"
    vol_m = row['volume'] / 10000
    amt_w = row['amount'] / 10000  # 万元
    
    # K线形态判断
    total_range = row['high'] - row['low']
    shape = ""
    if total_range > 0:
        body_ratio = body / total_range
        if body_ratio < 0.1:
            shape = "十字星"
        elif body_ratio < 0.3 and lower > body * 1.5:
            shape = "锤子线" if row['close'] > row['open'] else "上吊线"
        elif body_ratio < 0.3 and upper > body * 1.5:
            shape = "射击星" if row['close'] < row['open'] else "倒锤"
        elif abs(chg_d) > 5:
            shape = "大阳线" if chg_d > 0 else "大阴线"
    
    print(f"    {d} {bar_type} {chg_d:+5.2f}% O{row['open']:6.2f} H{row['high']:6.2f} L{row['low']:6.2f} C{row['close']:6.2f} V{vol_m:6.0f}万 {shape}")

# -- 关键价位 --
print(f"\n  --- 关键价位分析 ---")
high120 = df120['high'].max()
low120 = df120['low'].min()
high60 = df120.tail(60)['high'].max()
low60 = df120.tail(60)['low'].min()
high20 = df120.tail(20)['high'].max()
low20 = df120.tail(20)['low'].min()

ma5 = df.tail(5)['close'].mean()
ma10 = df.tail(10)['close'].mean()
ma20 = df.tail(20)['close'].mean()
ma60 = df.tail(60)['close'].mean()

print(f"  120日: 高{high120:.2f} 低{low120:.2f} | 当前位置: {(close-low120)/(high120-low120)*100:.0f}%")
print(f"  60日:  高{high60:.2f} 低{low60:.2f} | 当前位置: {(close-low60)/(high60-low60)*100:.0f}%")
print(f"  20日:  高{high20:.2f} 低{low20:.2f} | 当前位置: {(close-low20)/(high20-low20)*100:.0f}%")

# -- 筹码分布 (简化:近60日量价分布) --
print(f"\n  --- 近60日量价密集区 ---")
df60 = df120.tail(60).copy()
# 按价格区间统计成交量集中度
price_bins = np.linspace(df60['low'].min(), df60['high'].max(), 11)
for i in range(len(price_bins)-1):
    lo, hi = price_bins[i], price_bins[i+1]
    mask = (df60['close'] >= lo) & (df60['close'] <= hi)
    vol_in = df60[mask]['volume'].sum()
    pct = vol_in / df60['volume'].sum() * 100 if df60['volume'].sum() > 0 else 0
    bar = "#" * int(pct)
    marker = " <<< 当前" if lo <= close <= hi else ""
    print(f"    {lo:6.2f}-{hi:6.2f}: {pct:5.1f}% {bar}{marker}")

# -- MACD/KDJ等指标 --
print(f"\n  --- 技术指标 ---")
closes = df.tail(120)['close'].values
# MACD
ema12 = pd.Series(closes).ewm(span=12).mean().values
ema26 = pd.Series(closes).ewm(span=26).mean().values
dif = ema12 - ema26
dea = pd.Series(dif).ewm(span=9).mean().values
macd_bar = (dif - dea) * 2
print(f"  MACD: DIF={dif[-1]:.3f} DEA={dea[-1]:.3f} 柱={macd_bar[-1]:.3f}")
if dif[-1] > dea[-1]:
    print(f"    DIF在DEA上方 -> 多头")
else:
    print(f"    DIF在DEA下方 -> 空头")
if macd_bar[-1] > 0 and macd_bar[-2] > 0:
    if macd_bar[-1] > macd_bar[-2]:
        print(f"    红柱放大 -> 多头加速")
    else:
        print(f"    红柱缩小 -> 多头衰减")
elif macd_bar[-1] < 0:
    if macd_bar[-1] > macd_bar[-2]:
        print(f"    绿柱缩小 -> 空头衰减")
    else:
        print(f"    绿柱放大 -> 空头加速")

# KDJ
df_kdj = df.tail(130).copy().reset_index(drop=True)
low_9 = df_kdj['low'].rolling(9).min()
high_9 = df_kdj['high'].rolling(9).max()
rsv = (df_kdj['close'] - low_9) / (high_9 - low_9) * 100
k = rsv.ewm(com=2).mean()
d = k.ewm(com=2).mean()
j = 3 * k - 2 * d
print(f"  KDJ: K={k.iloc[-1]:.1f} D={d.iloc[-1]:.1f} J={j.iloc[-1]:.1f}")
if k.iloc[-1] < 20:
    print(f"    超卖区域 -> 可能反弹")
elif k.iloc[-1] > 80:
    print(f"    超买区域 -> 注意回调")
elif k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2]:
    print(f"    金叉! -> 买入信号")
elif k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2]:
    print(f"    死叉! -> 卖出信号")

# RSI
delta = pd.Series(closes).diff()
gain = delta.where(delta > 0, 0)
loss = -delta.where(delta < 0, 0)
avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()
rs = avg_gain / avg_loss
rsi14 = 100 - (100 / (1 + rs))
print(f"  RSI14: {rsi14.iloc[-1]:.1f}")

# 布林带
ma20_val = pd.Series(closes).rolling(20).mean().iloc[-1]
std20 = pd.Series(closes).rolling(20).std().iloc[-1]
upper_band = ma20_val + 2 * std20
lower_band = ma20_val - 2 * std20
boll_pos = (close - lower_band) / (upper_band - lower_band) * 100
print(f"  BOLL: 上轨{upper_band:.2f} 中轨{ma20_val:.2f} 下轨{lower_band:.2f} | 位置{boll_pos:.0f}%")

conn.close()

# ============ 2. 指数趋势 ============
print(f"\n\n{'='*95}")
print("【二】大盘指数趋势")
print("=" * 95)

conn2 = sqlite3.connect(r'数据缓存/stock_cache.db')
for idx_code, idx_name in [('sh.000001','上证指数'), ('sz.399001','深证成指')]:
    idf = pd.read_sql('SELECT date,open,high,low,close,volume FROM kline_daily WHERE code=? ORDER BY date', conn2, params=(idx_code,))
    if len(idf) >= 20:
        idf['date'] = pd.to_datetime(idf['date'])
        last_i = idf.iloc[-1]
        ma5_i = idf.tail(5)['close'].mean()
        ma10_i = idf.tail(10)['close'].mean()
        ma20_i = idf.tail(20)['close'].mean()
        chg5_i = (last_i['close'] / idf.iloc[-6]['close'] - 1) * 100 if len(idf) > 5 else 0
        chg10_i = (last_i['close'] / idf.iloc[-11]['close'] - 1) * 100 if len(idf) > 10 else 0
        
        if ma5_i > ma10_i > ma20_i:
            trend = "多头排列(强)"
        elif ma5_i > ma10_i:
            trend = "短多"
        elif ma5_i < ma10_i < ma20_i:
            trend = "空头排列(弱)"
        else:
            trend = "震荡"
        
        print(f"\n  {idx_name}:")
        print(f"    最新: {last_i['close']:.2f} | MA5={ma5_i:.2f} MA10={ma10_i:.2f} MA20={ma20_i:.2f}")
        print(f"    5日涨跌: {chg5_i:+.1f}% | 10日: {chg10_i:+.1f}%")
        print(f"    均线判断: {trend}")
        
        # 近5日K线
        for _, row in idf.tail(5).iterrows():
            d = str(row['date'])[:10]
            chg = (row['close'] - row['open']) / row['open'] * 100
            bar_type = "阳" if row['close'] >= row['open'] else "阴"
            print(f"    {d} {bar_type} {chg:+.2f}% 收{row['close']:.2f}")
conn2.close()

# 指数实时
print(f"\n  --- 实时指数 ---")
idx_url = 'http://qt.gtimg.cn/q=sh000001,sz399001,sz399006'
req = urllib.request.Request(idx_url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
for line in resp.strip().split('\n'):
    if '=' not in line: continue
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data) > 38:
        print(f"    {data[1]}: {data[3]} ({data[32]}%) 成交额{float(data[37])/100000000:.0f}亿")

# ============ 3. 康冠实时盘口 + 分时 ============
print(f"\n\n{'='*95}")
print("【三】康冠科技 实时盘口")
print("=" * 95)

rt_url = 'http://qt.gtimg.cn/q=sz001308'
req2 = urllib.request.Request(rt_url, headers={'User-Agent': 'Mozilla/5.0'})
resp2 = urllib.request.urlopen(req2, timeout=10).read().decode('gbk')
for line in resp2.strip().split('\n'):
    if '=' not in line: continue
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data) < 45: continue
    
    price = float(data[3])
    chg_pct = float(data[32]) if data[32] else 0
    amount = float(data[37]) / 10000 if data[37] else 0
    vol_ratio = float(data[49]) if len(data) > 49 and data[49] else 0
    turnover = float(data[38]) if data[38] else 0
    outer = float(data[7]) if data[7] else 0
    inner = float(data[8]) if data[8] else 0
    oi = round(outer / inner, 2) if inner > 0 else 0
    high = float(data[33]) if data[33] else 0
    low = float(data[34]) if data[34] else 0
    open_p = float(data[5]) if data[5] else 0
    prev_close = float(data[4]) if data[4] else price
    
    # 五档盘口
    print(f"  现价: {price:.2f} ({chg_pct:+.2f}%) 昨收: {prev_close:.2f}")
    print(f"  今开: {open_p:.2f} | 最高: {high:.2f} | 最低: {low:.2f}")
    print(f"  成交额: {amount:.2f}亿 | 换手: {turnover:.2f}% | 量比: {vol_ratio:.2f}")
    print(f"  外盘: {outer:.0f}手 | 内盘: {inner:.0f}手 | 外/内={oi:.2f}")
    
    # 五档买卖
    print(f"\n  --- 五档盘口 ---")
    for i in range(5):
        sell_p = data[25 - i*2] if len(data) > 25-i*2 else ''
        sell_v = data[26 - i*2] if len(data) > 26-i*2 else ''
        label = f"卖{5-i}"
        print(f"    {label}: {sell_p:>8s}  {sell_v:>6s}手")
    print(f"    ----当前价 {price}----")
    for i in range(5):
        buy_p = data[9 + i*2] if len(data) > 9+i*2 else ''
        buy_v = data[10 + i*2] if len(data) > 10+i*2 else ''
        label = f"买{i+1}"
        print(f"    {label}: {buy_p:>8s}  {buy_v:>6s}手")
    
    # 日内位置和幅度
    if high > low:
        pos = (price - low) / (high - low) * 100
        intraday_amp = (high - low) / prev_close * 100
        print(f"\n  日内位置: {pos:.0f}% | 日内振幅: {intraday_amp:.2f}%")
        if pos > 70:
            print(f"  -> 日内偏高位，尾盘追入风险偏大")
        elif pos < 30:
            print(f"  -> 日内偏低位，可能存在尾盘拉升机会")
        else:
            print(f"  -> 日内中间位置")

# 分时K线 (新浪)
print(f"\n  --- 今日分时走势(5分钟K线) ---")
try:
    sina_code = 'sz001308'
    sina_url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=5&ma=no&datalen=48'
    req3 = urllib.request.Request(sina_url, headers={'User-Agent': 'Mozilla/5.0'})
    resp3 = urllib.request.urlopen(req3, timeout=10).read().decode('utf-8')
    import re
    # 解析简单的json-like格式
    items = re.findall(r'\{([^}]+)\}', resp3)
    today_bars = []
    for item in items:
        fields = {}
        for pair in item.split(','):
            if ':' in pair:
                k, v = pair.split(':', 1)
                fields[k.strip().strip('"')] = v.strip().strip('"')
        if 'day' in fields:
            day_str = fields['day']
            if '2026-04-14' in day_str:
                today_bars.append(fields)
    
    if today_bars:
        print(f"  今日共 {len(today_bars)} 根5分钟K线:")
        for bar in today_bars[-12:]:  # 最近12根
            t = bar.get('day','')
            o = float(bar.get('open',0))
            h = float(bar.get('high',0))
            l = float(bar.get('low',0))
            c = float(bar.get('close',0))
            v = float(bar.get('volume',0))
            chg = (c - o) / o * 100 if o > 0 else 0
            print(f"    {t[-5:]} {chg:+.2f}% H{h:.2f} L{l:.2f} C{c:.2f} V{v:.0f}")
        
        # 分时量价特征
        if len(today_bars) >= 3:
            recent_bars = today_bars[-6:] if len(today_bars) >= 6 else today_bars
            vol_trend = [float(b.get('volume',0)) for b in recent_bars]
            price_trend = [float(b.get('close',0)) for b in recent_bars]
            if vol_trend[-1] > vol_trend[0]:
                print(f"\n  分时量能: 近期放量 ({vol_trend[0]:.0f} -> {vol_trend[-1]:.0f})")
            else:
                print(f"\n  分时量能: 近期缩量 ({vol_trend[0]:.0f} -> {vol_trend[-1]:.0f})")
            if price_trend[-1] > price_trend[0]:
                print(f"  分时价格: 近期走高 ({price_trend[0]:.2f} -> {price_trend[-1]:.2f})")
            else:
                print(f"  分时价格: 近期走低 ({price_trend[0]:.2f} -> {price_trend[-1]:.2f})")
    else:
        print(f"  未获取到今日分时数据")
except Exception as e:
    print(f"  分时数据获取异常: {e}")

# 行业实时
print(f"\n  --- 所属行业(C39电子通信) 今日表现 ---")
conn3 = sqlite3.connect(r'数据缓存/stock_cache.db')
sec_latest = pd.read_sql("SELECT * FROM sector_daily WHERE industry='C39计算机、通信和其他电子设备制造业' ORDER BY date DESC LIMIT 5", conn3)
if not sec_latest.empty:
    for _, row in sec_latest.iterrows():
        print(f"    {row['date']} 均涨{row['avg_pct']:+.2f}% 涨{row['up_count']} 跌{row['down_count']} 平{row['flat_count']} 额{row['total_amount']/100000000:.0f}亿")
conn3.close()
