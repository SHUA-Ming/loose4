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
    name = items[1]; cur = sf(3); pre = sf(4); opn = sf(5)
    hi = sf(33); lo = sf(34); pct = sf(32); vol = sf(36); amt = sf(37)
    print(f"{name}: 当前{cur:.2f} 昨收{pre:.2f} 开{opn:.2f} 高{hi:.2f} 低{lo:.2f} 涨跌{pct:+.2f}% 量{vol:,.0f}手 额{amt:,.0f}万")

for s in ["sh000001","sz399001","sz399006","sh603659"]:
    fetch(s)
