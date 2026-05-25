#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""尾盘实时批量拉取 - 临时脚本"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except:
    pass

import requests

def rt_batch(syms):
    url = f"https://qt.gtimg.cn/q={','.join(syms)}"
    resp = requests.get(url, timeout=10)
    resp.encoding = "gbk"
    results = []
    for line in resp.text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        idx = line.index('="') + 2
        payload = line[idx:].rstrip('"')
        items = payload.split("~")
        def sf(i):
            try: return float(items[i])
            except: return 0.0
        if len(items) < 40:
            continue
        name = items[1]
        code = items[2]
        cur = sf(3); pre = sf(4); opn = sf(5); hi = sf(33); lo = sf(34)
        pct = sf(32); vol = sf(36); amt = sf(37); turn = sf(38)
        outer = sf(7); inner = sf(8)
        results.append({
            'name': name, 'code': code, 'cur': cur, 'pre': pre, 'opn': opn,
            'hi': hi, 'lo': lo, 'pct': pct, 'vol': vol, 'amt': amt, 'turn': turn,
            'outer': outer, 'inner': inner
        })
    return results

# S2候选 + S1观察池 + 大盘指数
candidates = [
    'sh000001', 'sz399001', 'sz399006',  # 3大指数
    'sh605318', 'sh603698', 'sz000049', 'sz002906',  # S2 TOP4
    'sh603203', 'sh605005', 'sz002046', 'sz002047',  # S2 5-8
    'sh603226', 'sh603317', 'sz002066', 'sh600232',  # S2 9-12
    'sh600382', 'sh600810', 'sh603989', 'sz002559', 'sz002631',  # S2 其余
    'sz000899', 'sz002948',  # S1观察池
]

data = rt_batch(candidates)
print(f"拉取到 {len(data)} 只股票实时数据\n")

# 先找昨日成交量用于量比对比 (从DB)
sys.path.insert(0, "f:\\loose3\\工具脚本")
from db_cache import get_connection, init_db
init_db()
conn = get_connection()

# 取最近2天的成交量
yesterday_vol = {}
for d in data:
    code_bs = f"sh.{d['code']}" if d['code'].startswith(('6', '5')) else f"sz.{d['code']}"
    rows = conn.execute(
        "SELECT date, volume FROM kline_daily WHERE code=? ORDER BY date DESC LIMIT 2",
        [code_bs]
    ).fetchall()
    if rows:
        yesterday_vol[d['code']] = float(rows[0][1]) if rows[0][1] else 0
conn.close()

# 打印
print(f"{'名称':8s} {'代码':8s} {'现价':>8s} {'涨跌%':>7s} {'今量(手)':>12s} {'昨量(手)':>12s} {'量比':>6s} {'换手%':>6s} {'收盘位置':>8s} {'区段':4s} {'外/内':>5s}")
print("-" * 110)

for d in data:
    code = d['code']
    is_idx = code.startswith('000') or code.startswith('399')
    
    rng = d['hi'] - d['lo']
    if rng > 0:
        pos = (d['cur'] - d['lo']) / rng * 100
    else:
        pos = 50.0
    
    upper = "上半" if pos >= 50 else "下半"
    oi_ratio = d['outer'] / max(d['inner'], 1)
    
    yvol = yesterday_vol.get(code, 0)
    if yvol > 0:
        vratio = d['vol'] / (yvol / 100)  # DB中volume单位和API单位可能不同
    else:
        vratio = 0
    
    # 直接用手数对比
    vratio_str = f"{d['vol']/max(yvol/100,1):.2f}" if yvol > 0 else "N/A"
    
    tag = "指数" if is_idx else ""
    print(f"{d['name']:8s} {d['code']:8s} {d['cur']:8.2f} {d['pct']:+7.2f}% {d['vol']:>12,.0f} {yvol/100:>12,.0f} {vratio_str:>6s} {d['turn']:6.2f}% {pos:7.1f}% {upper} {oi_ratio:5.2f} {tag}")

# 额外：拉同板块个股做板块同步确认
# C41其他制造业、C35专用设备、C38电气机械、C39计算机通信
print("\n\n===== 板块同步确认 =====")

# 从DB获取同板块主要个股
conn = get_connection()
sectors_to_check = {
    'C41其他制造业': 'sh.605318',
    'C35专用设备制造业': 'sh.603698',
    'C38电气机械和器材制造业': 'sz.000049',
    'C39计算机、通信和其他电子设备制造业': 'sz.002906',
    'C36汽车制造业': 'sh.605005',
    'C34通用设备制造业': 'sz.002046',
    'E50建筑装饰、装修和其他建筑业': 'sz.002047',
}

for sector, main_code in sectors_to_check.items():
    # 取该板块5日涨幅TOP10
    rows = conn.execute("""
        SELECT si.code, si.code_name FROM stock_industry si
        WHERE si.industry = ? AND si.code != ?
        LIMIT 10
    """, [sector, main_code]).fetchall()
    
    if rows:
        syms = []
        for r in rows:
            c = r[0]
            if c.startswith('sh.'):
                syms.append(f"sh{c[3:]}")
            elif c.startswith('sz.'):
                syms.append(f"sz{c[3:]}")
        
        sector_data = rt_batch(syms[:8])  # 最多拉8只
        up_count = sum(1 for sd in sector_data if sd['pct'] > 0)
        down_count = sum(1 for sd in sector_data if sd['pct'] < 0)
        avg_pct = sum(sd['pct'] for sd in sector_data) / max(len(sector_data), 1)
        
        sync = "✅同步" if up_count >= 2 else "❌不同步"
        print(f"\n{sector[:15]:16s} 拉取{len(sector_data)}只 涨{up_count}/跌{down_count} 均涨{avg_pct:+.2f}% {sync}")
        # 显示前5
        for sd in sorted(sector_data, key=lambda x: x['pct'], reverse=True)[:5]:
            print(f"  {sd['name']:8s} {sd['pct']:+6.2f}%")

conn.close()
print("\n===== 尾盘确认完成 =====")
