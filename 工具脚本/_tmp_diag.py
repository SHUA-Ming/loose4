import requests
resp = requests.get('https://qt.gtimg.cn/q=sz002906', timeout=8)
resp.encoding = 'gbk'
text = resp.text.strip()
idx = text.index('="') + 2
payload = text[idx:].rstrip('";')
items = payload.split('~')
sf = lambda i: float(items[i]) if items[i] else 0
cur=sf(3);pre=sf(4);op=sf(5);hi=sf(33);lo=sf(34);pct=sf(32)
vol=sf(36);outer=sf(7);inner=sf(8);turn=sf(38)
cost=31.74
pnl=(cur/cost-1)*100
pos=(cur-lo)/(hi-lo)*100 if hi>lo else 50
oi=outer/max(inner,1)
print(f'{items[1]} sz.002906')
print(f'现价:{cur:.2f} 涨幅:{pct:+.2f}% 开:{op:.2f} 高:{hi:.2f} 低:{lo:.2f}')
print(f'量:{vol:.0f}万 换手:{turn:.2f}% 外/内:{oi:.2f}')
print(f'成本:{cost} 浮盈:{pnl:+.2f}%')
print(f'止盈1(+3%):{cost*1.03:.2f} 止盈2(+5%):{cost*1.05:.2f}')
print(f'日内位置:{pos:.0f}%')
