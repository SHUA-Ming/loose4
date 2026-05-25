import requests

def sf(items, i):
    try: return float(items[i])
    except: return 0.0

code = 'sz002348'
url = f"https://qt.gtimg.cn/q={code}"
r = requests.get(url, timeout=10)
r.encoding = 'gbk'
line = r.text.strip()
if '="' not in line:
    print("no data")
    exit()
payload = line.split('="', 1)[1]
payload = payload.rstrip(';').rstrip('"')
items = payload.split('~')

name = items[1]; cur = sf(items,3); pre = sf(items,4); opn = sf(items,5)
hi = sf(items,33); lo = sf(items,34); pct = sf(items,32); turn = sf(items,38)
amt = sf(items,37); outer = sf(items,7); inner = sf(items,8); upd = items[30] if len(items)>30 else ''

print(f"{name} 当前={cur:.2f} 涨幅={pct:+.2f}% 开={opn:.2f} 昨收={pre:.2f}")
print(f"最高={hi:.2f} 最低={lo:.2f} 换手={turn:.2f}%  成交额={amt/10000:.2f}亿  更新={upd}")
print(f"外盘={outer:.0f}手  内盘={inner:.0f}手  外/内={outer/max(inner,1):.2f}")

sell_prices=[]; sell_vols=[]
buy_prices=[]; buy_vols=[]
for i in range(5,0,-1):
    sell_prices.append(sf(items,19+i*2)); sell_vols.append(sf(items,18+i*2))
for i in range(1,6):
    buy_prices.append(sf(items,9+i*2)); buy_vols.append(sf(items,8+i*2))

print("--- 五档盘口 ---")
for i in range(5):
    print(f"  卖{5-i}: {sell_prices[i]:.2f} x {sell_vols[i]:,.0f}手")
print("  " + "─"*30)
for i in range(5):
    print(f"  买{i+1}: {buy_prices[i]:.2f} x {buy_vols[i]:,.0f}手")
