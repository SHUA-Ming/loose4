import urllib.request, json, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 精选候选: 有色3只 + 电子4只
candidates = {
    # 有色金属
    'sh600489': ('中金黄金', '有色', 16),
    'sh601899': ('紫金矿业', '有色', 15),
    'sz001280': ('中国铀业', '有色', 16),
    # 电子通信
    'sh600060': ('海信视像', '电子', 16),
    'sz001308': ('康冠科技', '电子', 16),
    'sz002351': ('漫步者',   '电子', 15),
    'sz000725': ('京东方A',  '电子', 16),
    'sz002972': ('科安达',   '电子', 14),
}

codes_str = ','.join(candidates.keys())
url = f'http://qt.gtimg.cn/q={codes_str}'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, timeout=10).read().decode('gbk')

print("=" * 100)
print(f"{'代码':10s} {'名称':8s} {'板块':4s} {'评分':4s} | {'现价':>7s} {'涨幅%':>6s} {'成交额(亿)':>10s} | {'量比':>5s} {'外/内':>5s} {'换手%':>5s} | 判断")
print("-" * 100)

for line in resp.strip().split('\n'):
    if '=' not in line:
        continue
    code_part = line.split('=')[0].split('_')[-1]
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data) < 45:
        continue
    
    info = candidates.get(code_part)
    if not info:
        continue
    name, sector, base_score = info
    name_real = data[1]
    
    price = float(data[3])
    chg_pct = float(data[32]) if data[32] else 0
    amount = float(data[37]) / 10000 if data[37] else 0  # 万->亿
    vol_ratio = float(data[49]) if len(data) > 49 and data[49] else 0
    turnover = float(data[38]) if data[38] else 0
    
    outer = float(data[7]) if data[7] else 0
    inner = float(data[8]) if data[8] else 0
    oi_ratio = round(outer / inner, 2) if inner > 0 else 0
    
    high = float(data[33]) if data[33] else 0
    low = float(data[34]) if data[34] else 0
    open_p = float(data[5]) if data[5] else 0
    prev_close = float(data[4]) if data[4] else price
    
    # 判断逻辑
    verdict = ''
    reasons = []
    
    # 涨幅过大不追
    if chg_pct > 3:
        reasons.append('涨幅过大')
    elif chg_pct > 1.5:
        reasons.append('涨幅偏高')
    
    # 外盘占优
    if oi_ratio >= 1.1:
        reasons.append('外盘强')
    elif oi_ratio <= 0.8:
        reasons.append('内盘压')
    
    # 量比
    if vol_ratio > 2:
        reasons.append('放量')
    elif vol_ratio < 0.5:
        reasons.append('极缩量')
    
    # 换手率
    if turnover > 5:
        reasons.append('高换手')
    
    # 距离日内最高
    if high > 0 and price > 0:
        dist_high = (high - price) / price * 100
        if dist_high > 2:
            reasons.append(f'离高{dist_high:.1f}%')
    
    # 综合判断
    if chg_pct > 5:
        verdict = 'SKIP-追高'
    elif chg_pct > 3:
        verdict = 'WAIT-涨多'
    elif chg_pct < -2:
        verdict = 'WATCH-弱'
    elif oi_ratio >= 1.0 and chg_pct <= 2 and base_score >= 15:
        verdict = 'OK-盘口好'
    elif oi_ratio >= 1.0 and chg_pct <= 2:
        verdict = 'OK'
    elif oi_ratio < 0.85:
        verdict = 'WAIT-内盘压'
    else:
        verdict = 'MAYBE'
    
    detail = ','.join(reasons) if reasons else '正常'
    line_out = f"{code_part:10s} {name_real:8s} {sector:4s} {base_score:2d}分  | {price:7.2f} {chg_pct:+6.2f} {amount:10.2f} | {vol_ratio:5.2f} {oi_ratio:5.2f} {turnover:5.2f} | {verdict} ({detail})"
    print(line_out)

# 获取指数实时
print("\n--- 指数参考 ---")
idx_url = 'http://qt.gtimg.cn/q=sh000001,sz399001,sz399006'
req2 = urllib.request.Request(idx_url, headers={'User-Agent': 'Mozilla/5.0'})
resp2 = urllib.request.urlopen(req2, timeout=10).read().decode('gbk')
for line in resp2.strip().split('\n'):
    if '=' not in line:
        continue
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data) > 32:
        print(f"  {data[1]}: {data[3]} ({data[32]}%)")
