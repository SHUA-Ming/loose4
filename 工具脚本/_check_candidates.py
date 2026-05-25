#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests, numpy as np, time, sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except: pass

stocks = [
    ('sh603228', 'sh603228', '景旺电子'),
    ('sz000539', 'sz000539', '粤电力A'),
    ('sz000519', 'sz000519', '中兵红箭'),
    ('sh601688', 'sh601688', '华泰证券'),
    ('sh603599', 'sh603599', '广信股份'),
]

def fetch_kline(code, days=30):
    try:
        url = (f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
               f'CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={days}')
        r = requests.get(url, timeout=8)
        data = r.json()
        if not data: return None
        return [{'c': float(k['close']), 'h': float(k['high']), 'l': float(k['low']),
                 'o': float(k['open']), 'v': float(k.get('volume',0)), 'd': k['day']} for k in data]
    except: return None

def fetch_rt(sym):
    try:
        r = requests.get(f'https://qt.gtimg.cn/q={sym}', timeout=8)
        r.encoding = 'gbk'
        items = r.text.strip().split('"')[1].split('~')
        def sf(i):
            try: return float(items[i])
            except: return 0.0
        return {'name': items[1], 'cur': sf(3), 'pre': sf(4), 'hi': sf(33), 'lo': sf(34),
                'pct': sf(32), 'turn': sf(38), 'amt': sf(37), 'outer': sf(7), 'inner': sf(8),
                'upd': items[30] if len(items) > 30 else ''}
    except: return None

for sina_code, qq_code, name in stocks:
    rt = fetch_rt(qq_code)
    kd = fetch_kline(sina_code, 30)

    print(f'\n--- {qq_code} {name} ---')
    if rt:
        oi = rt['outer'] / max(rt['inner'], 1)
        print(f'  RT: {rt["cur"]:.2f} ({rt["pct"]:+.2f}%)  换手{rt["turn"]:.2f}%  成交{rt["amt"]/10000:.2f}亿')
        print(f'  外/内: {oi:.2f}  (外{rt["outer"]:.0f} 内{rt["inner"]:.0f})  更新:{rt["upd"]}')

    if kd and len(kd) >= 20:
        closes = np.array([d['c'] for d in kd])
        vols = np.array([d['v'] for d in kd])
        ma5 = float(np.mean(closes[-5:]))
        ma10 = float(np.mean(closes[-10:]))
        ma20 = float(np.mean(closes[-20:]))
        avg_v = float(np.mean(vols[-10:]))
        vr = vols[-1] / avg_v if avg_v > 0 else 0

        if ma5 > ma10 > ma20:
            trend = '✅多头'
        elif ma5 > ma20 > ma10:
            trend = '⚡初多(MA5>MA20>MA10)'
        elif ma5 > ma20:
            trend = '⚡弱多'
        else:
            trend = '⚠️偏弱'

        print(f'  MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  均线:{trend}')
        print(f'  量比={vr:.2f}')

        recent_hi = max(d['h'] for d in kd[-10:])
        cur_c = closes[-1]
        from_peak = (recent_hi - cur_c) / recent_hi * 100
        print(f'  10日内最高={recent_hi:.2f}  距峰值回撤={from_peak:.1f}%')
    time.sleep(0.3)
