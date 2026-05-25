import sqlite3, pandas as pd, numpy as np, urllib.request, json, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect(r'数据缓存/stock_cache.db')

targets = {
    'sh.603599': '广信股份',
    'sh.600309': '万华化学',
    'sh.600226': '亨通股份',
    'sz.001280': '中国铀业',
    'sz.001308': '康冠科技',
}

# ============ 1. 历史K线分析 ============
print("=" * 90)
print("【一】历史K线技术面分析（基于数据库最近30日K线）")
print("=" * 90)

for code, name in targets.items():
    df = pd.read_sql(
        'SELECT date, open, high, low, close, volume, amount FROM kline_daily WHERE code=? ORDER BY date',
        conn, params=(code,))
    if len(df) < 20:
        print(f"\n{name}({code}): 数据不足，跳过")
        continue
    
    df['date'] = pd.to_datetime(df['date'])
    df30 = df.tail(30).copy().reset_index(drop=True)
    df20 = df.tail(20).copy().reset_index(drop=True)
    df10 = df.tail(10).copy().reset_index(drop=True)
    df5 = df.tail(5).copy().reset_index(drop=True)
    last = df30.iloc[-1]
    prev = df30.iloc[-2]
    
    close = last['close']
    
    # 均线
    ma5 = df5['close'].mean()
    ma10 = df10['close'].mean()
    ma20 = df20['close'].mean()
    ma60 = df.tail(60)['close'].mean() if len(df) >= 60 else None
    
    # 均线排列
    if ma5 > ma10 > ma20:
        ma_status = "多头排列(强)"
    elif ma5 > ma10:
        ma_status = "短多(MA5>MA10)"
    elif ma10 > ma20:
        ma_status = "中多(MA10>MA20)"
    elif ma5 < ma10 < ma20:
        ma_status = "空头排列(弱)"
    else:
        ma_status = "交叉/震荡"
    
    # 近5/10/20日涨跌幅
    chg5 = (close / df5.iloc[0]['close'] - 1) * 100
    chg10 = (close / df10.iloc[0]['close'] - 1) * 100
    chg20 = (close / df20.iloc[0]['close'] - 1) * 100
    
    # 近20日高低点和回撤
    high20 = df20['high'].max()
    low20 = df20['low'].min()
    drawback20 = (high20 - close) / high20 * 100
    bounce20 = (close - low20) / low20 * 100
    
    # 量能分析
    vol5 = df5['volume'].mean()
    vol20 = df20['volume'].mean()
    vol_ratio = vol5 / vol20 if vol20 > 0 else 0
    
    # 近5日振幅
    amp5 = (df5['high'].max() - df5['low'].min()) / df5['low'].min() * 100
    
    # V3+评分
    score = 0
    # 缩量
    if vol_ratio < 0.8: score += 3
    elif vol_ratio < 1.0: score += 1
    # 横盘
    if amp5 < 5: score += 4
    elif amp5 < 8: score += 2
    # 均线
    if ma5 > ma10 > ma20: score += 4
    elif ma5 > ma10 or ma10 > ma20: score += 2
    # 实体
    body_pct = abs(last['close'] - last['open']) / last['open'] * 100 if last['open'] > 0 else 99
    if body_pct < 1.0: score += 3
    elif body_pct < 2.0: score += 2
    # 下影
    lower_shadow = min(last['open'], last['close']) - last['low']
    body = abs(last['close'] - last['open'])
    if body > 0 and lower_shadow > body * 0.5: score += 2
    elif last['low'] < min(last['open'], last['close']): score += 1
    # 十字星
    if body_pct < 0.5 and (last['high'] - last['low']) > 0: score += 2
    # 量价交替
    if len(df5) >= 3:
        alt = 0
        for i in range(1, len(df5)):
            chg_d = df5.iloc[i]['close'] - df5.iloc[i-1]['close']
            vol_d = df5.iloc[i]['volume'] - df5.iloc[i-1]['volume']
            if (chg_d > 0 and vol_d > 0) or (chg_d < 0 and vol_d < 0):
                alt += 1
        if alt >= 2: score += 2
        elif alt >= 1: score += 1
    
    grade = 'A' if score >= 16 else 'B' if score >= 15 else 'C' if score >= 12 else 'D'
    
    # 近5日K线形态
    print(f"\n{'='*50}")
    print(f"  {name}({code})  V3+评分: {score}/20 ({grade}级)")
    print(f"{'='*50}")
    print(f"  最新收盘: {close:.2f}")
    print(f"  均线: MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}" + (f" MA60={ma60:.2f}" if ma60 else ""))
    print(f"  均线状态: {ma_status}")
    print(f"  涨跌幅: 5日{chg5:+.1f}% | 10日{chg10:+.1f}% | 20日{chg20:+.1f}%")
    print(f"  20日高点: {high20:.2f} | 低点: {low20:.2f}")
    print(f"  回撤(距高): {drawback20:.1f}% | 反弹(距低): {bounce20:.1f}%")
    print(f"  5日振幅: {amp5:.1f}% | 量比(5/20): {vol_ratio:.2f}")
    print(f"  最新K线: 开{last['open']:.2f} 高{last['high']:.2f} 低{last['low']:.2f} 收{last['close']:.2f} 实体{body_pct:.2f}%")
    
    # 近5日K线明细
    print(f"  --- 近5日K线 ---")
    for idx, row in df5.iterrows():
        d = str(row['date'])[:10]
        chg_d = (row['close'] / row['open'] - 1) * 100
        bar = "+" * int(abs(chg_d) * 5) if chg_d >= 0 else "-" * int(abs(chg_d) * 5)
        color = "阳" if chg_d >= 0 else "阴"
        vol_m = row['volume'] / 10000
        print(f"    {d} {color} {chg_d:+.2f}% O{row['open']:.2f} H{row['high']:.2f} L{row['low']:.2f} C{row['close']:.2f} V{vol_m:.0f}万 {bar}")

    # 关键支撑/压力
    print(f"  --- 关键位 ---")
    print(f"    支撑: MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f}, 20日低={low20:.2f}")
    print(f"    压力: 20日高={high20:.2f}" + (f", MA60={ma60:.2f}" if ma60 and close < ma60 else ""))

conn.close()

# ============ 2. 实时盘口 ============
print(f"\n\n{'='*90}")
print("【二】实时盘口数据")
print("=" * 90)

rt_codes = 'sh603599,sh600309,sh600226,sz001280,sz001308'
url = f'http://qt.gtimg.cn/q={rt_codes}'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, timeout=10).read().decode('gbk')

code_map = {'sh603599':'广信股份','sh600309':'万华化学','sh600226':'亨通股份',
            'sz001280':'中国铀业','sz001308':'康冠科技'}
hold_cost = {'sh603599': 14.89, 'sh600309': 92.18, 'sh600226': 5.25}

for line in resp.strip().split('\n'):
    if '=' not in line: continue
    code_part = line.split('=')[0].split('_')[-1]
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data) < 45: continue
    
    name = code_map.get(code_part, data[1])
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
    
    print(f"\n  {name}({code_part})")
    print(f"    现价: {price:.2f} ({chg_pct:+.2f}%)")
    print(f"    今开: {open_p:.2f} | 最高: {high:.2f} | 最低: {low:.2f}")
    print(f"    成交额: {amount:.2f}亿 | 换手: {turnover:.2f}% | 量比: {vol_ratio:.2f}")
    print(f"    外盘/内盘: {oi:.2f} (外{outer:.0f}手/内{inner:.0f}手) {'外强' if oi > 1.1 else '内压' if oi < 0.85 else '均衡'}")
    
    # 持仓盈亏
    if code_part in hold_cost:
        cost = hold_cost[code_part]
        pnl = (price - cost) / cost * 100
        print(f"    [持仓] 成本: {cost:.2f} | 盈亏: {pnl:+.2f}%")
    
    # 日内位置
    if high > low:
        pos = (price - low) / (high - low) * 100
        print(f"    日内位置: {pos:.0f}% (0=最低, 100=最高)")

# 指数
print(f"\n  --- 大盘指数 ---")
idx_url = 'http://qt.gtimg.cn/q=sh000001,sz399001,sz399006'
req2 = urllib.request.Request(idx_url, headers={'User-Agent': 'Mozilla/5.0'})
resp2 = urllib.request.urlopen(req2, timeout=10).read().decode('gbk')
for line in resp2.strip().split('\n'):
    if '=' not in line: continue
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data) > 32:
        print(f"    {data[1]}: {data[3]} ({data[32]}%)")

# ============ 3. 行业对比 ============
print(f"\n\n{'='*90}")
print("【三】所属行业近期表现")
print("=" * 90)

conn2 = sqlite3.connect(r'数据缓存/stock_cache.db')
for code, name in targets.items():
    ind = pd.read_sql('SELECT industry FROM stock_industry WHERE code=?', conn2, params=(code,))
    if not ind.empty:
        industry = ind.iloc[0]['industry']
        # 查行业近10日表现
        sec = pd.read_sql(
            "SELECT date, close FROM sector_daily WHERE industry=? ORDER BY date DESC LIMIT 10",
            conn2, params=(industry,))
        if not sec.empty and len(sec) >= 2:
            latest = sec.iloc[0]['close']
            oldest = sec.iloc[-1]['close']
            sec_chg = (latest - oldest) / oldest * 100 if oldest > 0 else 0
            print(f"  {name}: {industry} | 近10日行业涨幅: {sec_chg:+.1f}%")
        else:
            print(f"  {name}: {industry} | 行业数据不足")
    else:
        print(f"  {name}({code}): 未找到行业信息")
conn2.close()
