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
        try:
            return float(items[i])
        except:
            return 0.0

    name = items[1]
    code = items[2]
    cur = sf(3)
    pre = sf(4)
    opn = sf(5)
    hi = sf(33)
    lo = sf(34)
    amp = sf(43)
    chg = sf(31)
    pct = sf(32)
    vol = sf(36)
    amt = sf(37)
    turn = sf(38)
    outer = sf(7)
    inner = sf(8)
    upd = items[30] if len(items) > 30 else ""

    print(f"{'='*50}")
    print(f"  {name} ({code})")
    print(f"  更新时间: {upd}")
    print(f"{'='*50}")
    print(f"  当前价: {cur:.2f}   昨收: {pre:.2f}   开盘: {opn:.2f}")
    print(f"  最高: {hi:.2f}   最低: {lo:.2f}   振幅: {amp:.2f}%")
    print(f"  涨跌额: {chg:+.2f}   涨跌幅: {pct:+.2f}%")
    print(f"  成交量: {vol:,.0f}手   成交额: {amt:,.0f}万")
    print(f"  换手率: {turn:.2f}%")
    print(f"  外盘: {outer:,.0f}手  内盘: {inner:,.0f}手  外/内比: {outer/max(inner,1):.2f}")
    print(f"  --- 五档盘口 ---")
    for i in range(5, 0, -1):
        sp = sf(19 + i*2)
        sv = sf(18 + i*2)
        print(f"  卖{i}: {sp:.2f} x {sv:,.0f}")
    for i in range(1, 6):
        bp = sf(9 + i*2)
        bv = sf(8 + i*2)
        print(f"  买{i}: {bp:.2f} x {bv:,.0f}")
    print()

fetch("sh600233")
