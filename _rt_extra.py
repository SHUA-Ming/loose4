import urllib.request, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
extra = 'sz000975,sz000807,sz002532,sz000426,sz002935,sz002960,sh600776'
url = f'http://qt.gtimg.cn/q={extra}'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
names = {'sz000975':'山金国际','sz000807':'云铝股份','sz002532':'天山铝业',
         'sz000426':'兴业银锡','sz002935':'天奥电子','sz002960':'青鸟消防','sh600776':'东方通信'}
print("代码        名称        | 现价    涨幅%  量比  外/内  换手%")
print("-"*65)
for line in resp.strip().split('\n'):
    if '=' not in line: continue
    code_part = line.split('=')[0].split('_')[-1]
    data = line.split('=')[1].strip(';').strip('"').split('~')
    if len(data)<45: continue
    tag = names.get(code_part,'?')
    price = float(data[3])
    chg = float(data[32]) if data[32] else 0
    vr = float(data[49]) if len(data)>49 and data[49] else 0
    outer = float(data[7]) if data[7] else 0
    inner = float(data[8]) if data[8] else 0
    oi = round(outer/inner,2) if inner>0 else 0
    tr = float(data[38]) if data[38] else 0
    sector = '有色' if code_part in ['sz000975','sz000807','sz002532','sz000426'] else '电子'
    print(f"{code_part:10s} {data[1]:8s} [{sector}] | {price:7.2f} {chg:+6.2f} {vr:5.2f} {oi:5.2f} {tr:5.2f}")
