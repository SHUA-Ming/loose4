import sys
sys.stdout.reconfigure(encoding='utf-8')
import requests
from db_cache import get_connection, init_db
init_db()
conn = get_connection()

def rt_batch(syms):
    url = "https://qt.gtimg.cn/q=" + ",".join(syms)
    resp = requests.get(url, timeout=10)
    resp.encoding = "gbk"
    results = []
    for line in resp.text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        idx = line.index('="') + 2
        items = line[idx:].rstrip('"').split("~")
        if len(items) < 40:
            continue
        def sf(i):
            try: return float(items[i])
            except: return 0.0
        results.append({"name": items[1], "pct": sf(32)})
    return results

for sector in ["C14食品制造业", "C20木材加工和木、竹、藤、棕、草制品业"]:
    rows = conn.execute("SELECT code FROM stock_industry WHERE industry=? LIMIT 10", [sector]).fetchall()
    syms = []
    for r in rows:
        c = r[0]
        if c.startswith("sh."):
            syms.append("sh" + c[3:])
        elif c.startswith("sz."):
            syms.append("sz" + c[3:])
    data = rt_batch(syms[:8])
    up = sum(1 for d in data if d["pct"] > 0)
    dn = sum(1 for d in data if d["pct"] < 0)
    avg = sum(d["pct"] for d in data) / max(len(data), 1)
    sync = "✅同步" if up >= 2 else "❌不同步"
    print(f"{sector[:20]:20s} 涨{up}/跌{dn} 均涨{avg:+.2f}% {sync}")
    for d in sorted(data, key=lambda x: x["pct"], reverse=True)[:4]:
        print(f"  {d['name']:8s} {d['pct']:+.2f}%")

conn.close()
