import requests

def fetch(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split("~")
    def sf(i):
        try: return float(items[i])
        except: return 0.0
    name=items[1]; cur=sf(3); pre=sf(4); opn=sf(5)
    hi=sf(33); lo=sf(34); pct=sf(32); vol=sf(36); amt=sf(37)
    turn=sf(38); outer=sf(7); inner=sf(8)
    upd=items[30] if len(items)>30 else ""
    print(f"== {name}({items[2]}) == {upd}")
    print(f"  现:{cur:.2f} 开:{opn:.2f} 高:{hi:.2f} 低:{lo:.2f} 昨收:{pre:.2f}")
    print(f"  涨跌:{pct:+.2f}% 量:{vol:,.0f}手 额:{amt:,.0f}万 换手:{turn:.2f}%")
    print(f"  外:{outer:,.0f} 内:{inner:,.0f} 比:{outer/max(inner,1):.2f}")
    for i in range(5, 0, -1):
        sp = sf(19+i*2); sv = sf(18+i*2)
        print(f"  卖{i}: {sp:.2f} x {sv:,.0f}")
    for i in range(1, 6):
        bp = sf(9+i*2); bv = sf(8+i*2)
        print(f"  买{i}: {bp:.2f} x {bv:,.0f}")
    print()

for s in ["sh603659","sh603220","sz000338","sz000988","sz002281","sh600498","sh601669","sz000933","sh600893","sh600673"]:
    fetch(s)
