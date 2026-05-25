import sys
sys.stdout.reconfigure(encoding='utf-8')
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
    name = items[1]
    code = items[2]
    cur = sf(3); pre = sf(4); opn = sf(5); hi = sf(33); lo = sf(34)
    pct = sf(32); vol = sf(36); amt = sf(37); turn = sf(38)
    outer = sf(7); inner = sf(8)
    upd = items[30] if len(items) > 30 else ""
    print(f"{'='*50}")
    print(f"  {name} ({code})")
    print(f"  更新: {upd}")
    print(f"  当前价: {cur:.2f}  昨收: {pre:.2f}  开盘: {opn:.2f}")
    print(f"  最高: {hi:.2f}  最低: {lo:.2f}")
    print(f"  涨跌幅: {pct:+.2f}%  成交量: {vol:,.0f}手  成交额: {amt:,.0f}万")
    print(f"  换手率: {turn:.2f}%  外/内比: {outer/max(inner,1):.2f}")
    print()

fetch("sh603271")
fetch("sz000899")
fetch("sz002158")
fetch("sh600232")
fetch("sh600382")
fetch("sh603203")
fetch("sh603701")
fetch("sh605005")
fetch("sz002066")
fetch("sz002283")
fetch("sz002290")
