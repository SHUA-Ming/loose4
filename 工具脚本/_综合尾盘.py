#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests, sys, time
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

def qq_rt(sym):
    r = requests.get(f'https://qt.gtimg.cn/q={sym}', timeout=10)
    r.encoding = 'gbk'
    items = r.text.strip().split('"')[1].split('~')

    def f(i):
        try:
            return float(items[i])
        except Exception:
            return 0.0

    return {
        'name': items[1], 'code': items[2],
        'cur': f(3), 'pre': f(4), 'open': f(5),
        'hi': f(33), 'lo': f(34),
        'chg': f(31), 'pct': f(32),
        'vol': f(36), 'amt_wan': f(37), 'turn': f(38),
        'outer': f(7), 'inner': f(8),
        'upd': items[30] if len(items) > 30 else ''
    }

stocks = [
    ('sh600309', '万华化学', '✅首选'),
    ('sh600226', '亨通股份', '🥈次选'),
    ('sh603599', '广信股份', '🆘持仓'),
    ('sz000519', '中兵红箭', '🥉第三'),
]

print('=== 当前时刻 ===')
print(time.strftime('%Y-%m-%d %H:%M:%S'))
print()

# 大盘
idx_syms = ['sh000001', 'sz399001', 'sz399006']
print('【大盘快照】')
for s in idx_syms:
    d = qq_rt(s)
    names = {'sh000001': '上证', 'sz399001': '深证', 'sz399006': '创业板'}
    print(f"  {names[s]:4s}: {d['cur']:>8.2f}  {d['pct']:+7.2f}%")

print('\n【四只候选股 - 最新实时】')
print(f"{'代码':<12} {'名称':<8} {'标记':8s} {'现价':>8s} {'涨跌':>8s} {'高':>7s} {'低':>7s} {'距高':>6s} {'外/内':>6s} {'更新时间':>12s}")
print('-' * 95)

results = []
for sym, name, label in stocks:
    d = qq_rt(sym)
    oi = d['outer'] / max(d['inner'], 1)
    from_hi = (d['hi'] - d['cur']) / max(d['hi'], 1e-9) * 100
    txt_hi_lo = f"{d['hi']:.2f}/{d['lo']:.2f}"
    
    results.append({
        'sym': sym, 'name': name, 'label': label, 'data': d,
        'oi': oi, 'from_hi': from_hi
    })
    
    print(f"{sym:<12} {name:<8} {label:8s} {d['cur']:>8.2f} {d['pct']:>+7.2f}% "
          f"{d['hi']:>7.2f} {d['lo']:>7.2f} {from_hi:>5.1f}% {oi:>5.2f}  {d['upd']:>12s}")

print('\n【综合分析 + 尾盘 14:30~15:00 操作建议】\n')

for i, r in enumerate(results):
    print(f"{i+1}. {r['label']} {r['sym']} {r['name']}")
    d = r['data']
    print(f"   现价: {d['cur']:.2f}  涨跌: {d['pct']:+.2f}%  外内比: {r['oi']:.2f}  距日高: {r['from_hi']:.2f}%")
    
    # 尾盘建议（简版，基于当前价位）
    if r['sym'] == 'sh600309':
        print(f"   ⏱ 尾盘窗口：14:30-14:55")
        print(f"   📌 条件A：回踩91.8-92.2企稳，且分时不破91.6 → 分批买入（1/3仓试）")
        print(f"   📌 条件B：放量站稳93.2+5分钟 → 追随买入")
        print(f"   🚫 不在条件下不追")
        print(f"   止盈: 94.5-95.2(卖1/2) / 96.5-97.0(清)")
        print(f"   止损: 盘中破90.8直出 / 收盘破90.9次日早开盘出")
    
    elif r['sym'] == 'sh600226':
        print(f"   ⏱ 尾盘窗口：14:30-14:55")
        print(f"   📌 条件A：回踩5.20-5.23企稳 → 分批试单（1/3仓）")
        print(f"   📌 条件B：放量站稳5.28+几分钟 → 追随")
        print(f"   ⚠️  当前{d['pct']:+.2f}%已有涨幅，不追高")
        print(f"   止盈: 5.36-5.40(卖1/2) / 5.48-5.55(清)")
        print(f"   止损: 收盘5.16下方次日早出 / 盘中5.10破直出")
    
    elif r['sym'] == 'sh603599':
        print(f"   🔴 持仓&卖出计划优先")
        if d['pct'] < -0.5:
            print(f"   📍 当前 {d['pct']:+.2f}% 微亏，按预案执行")
        else:
            print(f"   📍 当前 {d['pct']:+.2f}% 略弱")
        print(f"   ⏱ 执行时间表：")
        print(f"     • 14:30前若到15.05+ → 全出落袋")
        print(f"     • 14:30前若在14.90-15.00 → 先卖1/2锁保本")
        print(f"     • 14:45尾盘 → 不管什么价剩余全出（不留隔夜）")
        print(f"     • 若跌破14.70 → 全部市价出（不等反弹）")
    
    elif r['sym'] == 'sz000519':
        print(f"   ⏱ 尾盘窗口：14:30-14:55")
        print(f"   📌 条件A：回踩17.50-17.60企稳+外内比>1.3 → 极小仓试（1/5仓）")
        print(f"   📌 条件B：放量穿破17.73+5分钟 → 追随极小仓")
        print(f"   🚫 不建议现在追，已离日高仅{r['from_hi']:.2f}%")
        print(f"   止盈: 18.00-18.30(卖1/2) / 18.80+(清)")
        print(f"   止损: 破17.50立即出 / 2交易日强制出")
    
    print()

print('=' * 95)
print('【优先级建议】')
print('1️⃣  广信股份：执行既定卖出计划，优先不亏或小利出局（持仓压力释放）')
print('2️⃣  万华化学：条件充分再进，看回踩或突破确认（最强操作）')
print('3️⃣  亨通股份：条件确认再进，板块偏弱不追（中等机会）') 
print('4️⃣  中兵红箭：观望为主，等明天高开再考虑（最低优先级）')
print()
print('【时间提醒】')
print('尾盘入场黄金窗口： 14:30 ~ 14:55')
print('最后清场时间：     14:45 ~ 15:00（不留隔夜隐患🚩）')
