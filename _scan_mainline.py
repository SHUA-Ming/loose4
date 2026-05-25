import sqlite3, pandas as pd, numpy as np, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
conn = sqlite3.connect(r'数据缓存/stock_cache.db')

# 目标行业: 有色金属矿采选+冶炼, 电子通信设备
target_inds = ['B09有色金属矿采选业','C32有色金属冶炼和压延加工业','C39计算机、通信和其他电子设备制造业']

stocks = pd.read_sql(
    'SELECT code, code_name, industry FROM stock_industry WHERE industry IN ({})'.format(
        ','.join(['?']*len(target_inds))), conn, params=target_inds)
print(f'目标行业共 {len(stocks)} 只股票')

codes = stocks['code'].tolist()
all_klines = pd.read_sql(
    'SELECT code, date, open, high, low, close, volume, amount FROM kline_daily WHERE code IN ({}) ORDER BY code, date'.format(
        ','.join(['?']*len(codes))), conn, params=codes)
all_klines['date'] = pd.to_datetime(all_klines['date'])

results = []
for code in codes:
    df = all_klines[all_klines['code']==code].tail(30).copy()
    if len(df) < 20:
        continue
    df = df.reset_index(drop=True)
    last = df.iloc[-1]
    name = stocks[stocks['code']==code]['code_name'].values[0]
    industry = stocks[stocks['code']==code]['industry'].values[0]
    
    close = last['close']
    if close <= 0 or close > 200:
        continue
    if 'ST' in str(name).upper():
        continue
    
    score = 0
    recent5 = df.tail(5)
    
    # 1. 缩量(3分)
    vol5 = recent5['volume'].mean()
    vol20 = df.tail(20)['volume'].mean()
    if vol20 > 0 and vol5 < vol20 * 0.8:
        score += 3
    elif vol20 > 0 and vol5 < vol20 * 1.0:
        score += 1
    
    # 2. 横盘(4分)
    high5 = recent5['high'].max()
    low5 = recent5['low'].min()
    amp5 = (high5 - low5) / low5 * 100 if low5 > 0 else 99
    if amp5 < 5:
        score += 4
    elif amp5 < 8:
        score += 2
    
    # 3. 均线(4分)
    ma5 = df.tail(5)['close'].mean()
    ma10 = df.tail(10)['close'].mean()
    ma20 = df.tail(20)['close'].mean()
    if ma5 > ma10 > ma20:
        score += 4
    elif ma5 > ma10 or ma10 > ma20:
        score += 2
    
    # 4. 实体小(3分)
    body_pct = abs(last['close'] - last['open']) / last['open'] * 100 if last['open'] > 0 else 99
    if body_pct < 1.0:
        score += 3
    elif body_pct < 2.0:
        score += 2
    
    # 5. 下影线(2分)
    lower_shadow = min(last['open'], last['close']) - last['low']
    body = abs(last['close'] - last['open'])
    if body > 0 and lower_shadow > body * 0.5:
        score += 2
    elif last['low'] < min(last['open'], last['close']):
        score += 1
    
    # 6. 十字星(2分)
    if body_pct < 0.5 and (last['high'] - last['low']) > 0:
        score += 2
    
    # 7. 量价交替(2分)
    if len(recent5) >= 3:
        alt_score = 0
        for i in range(1, len(recent5)):
            chg = recent5.iloc[i]['close'] - recent5.iloc[i-1]['close']
            vol_chg = recent5.iloc[i]['volume'] - recent5.iloc[i-1]['volume']
            if (chg > 0 and vol_chg > 0) or (chg < 0 and vol_chg < 0):
                alt_score += 1
        if alt_score >= 2:
            score += 2
        elif alt_score >= 1:
            score += 1
    
    high20 = df.tail(20)['high'].max()
    drawback = (high20 - close) / high20 * 100
    
    if score >= 12:
        results.append({
            'code': code, 'name': name, 'industry': industry,
            'score': score, 'close': close, 'amp5': round(amp5,1),
            'ma_trend': 'bull' if ma5>ma10>ma20 else 'partial',
            'vol_ratio': round(vol5/vol20,2) if vol20>0 else 0,
            'drawback': round(drawback,1),
            'body_pct': round(body_pct,2)
        })

results.sort(key=lambda x: x['score'], reverse=True)
print(f'\n=== 评分>=12 的候选 ({len(results)}只) ===')
for r in results[:20]:
    ind_tag = '有色' if '有色' in r['industry'] else '电子'
    line = "{} {:8s} [{}] {:2d}分 | 价{:.2f} 振{:.1f}% 量比{:.2f} 回撤{:.1f}% {} 体{:.2f}%".format(
        r['code'], r['name'], ind_tag, r['score'], r['close'], r['amp5'],
        r['vol_ratio'], r['drawback'], r['ma_trend'], r['body_pct'])
    print(line)

# 重点看>=14分的
top = [r for r in results if r['score'] >= 14]
print(f'\n=== A/B级 (>=14分) 共{len(top)}只 ===')
for r in top:
    ind_tag = '有色' if '有色' in r['industry'] else '电子'
    line = "{} {:8s} [{}] {:2d}分 | 价{:.2f} 振{:.1f}% 量比{:.2f} 回撤{:.1f}% {} 体{:.2f}%".format(
        r['code'], r['name'], ind_tag, r['score'], r['close'], r['amp5'],
        r['vol_ratio'], r['drawback'], r['ma_trend'], r['body_pct'])
    print(line)

conn.close()
