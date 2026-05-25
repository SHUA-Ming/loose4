#!/usr/bin/env python3
"""获取 002158 和 002906 的04-17收盘数据"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import requests

def fetch_close(sym, label):
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
    cur = sf(3)
    pre = sf(4)
    opn = sf(5)
    hi = sf(33)
    lo = sf(34)
    pct = sf(32)
    vol = sf(36)
    amt = sf(37)
    turn = sf(38)
    outer = sf(7)
    inner = sf(8)
    upd = items[30] if len(items) > 30 else ""

    print(f"{'='*60}")
    print(f"  {label}: {name} ({sym})")
    print(f"  更新时间: {upd}")
    print(f"  当前/收盘: {cur:.2f}   昨收: {pre:.2f}   开盘: {opn:.2f}")
    print(f"  最高: {hi:.2f}   最低: {lo:.2f}")
    print(f"  涨跌幅: {pct:+.2f}%")
    print(f"  成交量: {vol:,.0f}手   成交额: {amt:,.0f}万")
    print(f"  换手率: {turn:.2f}%")
    print(f"  外盘: {outer:,.0f}手  内盘: {inner:,.0f}手")
    if hi > lo > 0:
        pos = (cur - lo) / (hi - lo)
        print(f"  日内位置: {pos:.0%}")
    print()
    return cur, pre, opn, hi, lo, pct, vol, turn

print("=== 04-17 收盘数据 ===\n")
c1, pre1, o1, h1, l1, pct1, vol1, t1 = fetch_close("sz002158", "汉钟精机")
c2, pre2, o2, h2, l2, pct2, vol2, t2 = fetch_close("sz002906", "华阳集团")

# 持仓盈亏
print("=" * 60)
print("  持仓盈亏速算")
print("=" * 60)
buy_158 = 24.74
buy_906 = 31.74
print(f"  汉钟精机: 买入{buy_158} → 收盘{c1:.2f} → 浮盈{(c1/buy_158-1)*100:+.2f}%")
print(f"  华阳集团: 买入{buy_906} → 收盘{c2:.2f} → 浮盈{(c2/buy_906-1)*100:+.2f}%")

# 关键止损线检查
print(f"\n  === 汉钟精机 止损线检查 ===")
print(f"  D1软止损线(-1.5%): {buy_158*0.985:.2f}  收盘{c1:.2f} {'⚠️已触发' if c1 < buy_158*0.985 else '✅未触发'}")
print(f"  硬止损(-3%): {buy_158*0.97:.2f}  收盘{c1:.2f} {'⚠️已触发' if c1 < buy_158*0.97 else '✅未触发'}")

print(f"\n  === 华阳集团 止损线检查 ===")
print(f"  D1软止损线(-1.5%): {buy_906*0.985:.2f}  收盘{c2:.2f} {'⚠️已触发' if c2 < buy_906*0.985 else '✅未触发'}")
print(f"  硬止损(-3%): {buy_906*0.97:.2f}  收盘{c2:.2f} {'⚠️已触发' if c2 < buy_906*0.97 else '✅未触发'}")

# 大盘
print(f"\n=== 大盘指数 ===\n")
fetch_close("sh000001", "上证指数")
fetch_close("sz399001", "深证成指")
fetch_close("sz399006", "创业板指")
