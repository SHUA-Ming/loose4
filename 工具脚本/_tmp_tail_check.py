#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时尾盘确认脚本 - 拉取候选股实时数据"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except:
    pass

import requests

def quick_rt(sym):
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
    name = items[1]; code = items[2]; cur = sf(3); pre = sf(4); opn = sf(5)
    hi = sf(33); lo = sf(34); pct = sf(32); vol = sf(36); amt = sf(37)
    turn = sf(38); outer = sf(7); inner = sf(8)
    pos = (cur - lo) / (hi - lo) if hi > lo else 0.5
    oi = outer / max(inner, 1)
    return name, code, cur, pre, opn, hi, lo, pct, vol, amt, turn, pos, oi

# S2 candidates
s2_list = ['sz002158','sz002528','sh603698','sh605005','sh605318',
           'sz002023','sz002283','sz002838','sh603203','sz002576','sz000049']
# S3 candidates
s3_list = ['sz002290','sh603958','sz002132','sz002176','sz002650']
# 大盘
idx_list = ['sh000001','sz399001','sz399006']

print("="*80)
print("  大盘实时")
print("="*80)
for sym in idx_list:
    n,c,cur,pre,opn,hi,lo,pct,vol,amt,turn,pos,oi = quick_rt(sym)
    print(f"  {n}: {cur:.2f}  {pct:+.2f}%  振幅:{((hi-lo)/pre*100):.2f}%  日内位置:{pos:.0%}")

print()
print("="*80)
print("  S2 候选实时数据 (尾盘确认)")
print("="*80)
for sym in s2_list:
    n,c,cur,pre,opn,hi,lo,pct,vol,amt,turn,pos,oi = quick_rt(sym)
    print(f"  {n}({c}): 现价{cur:.2f} {pct:+.2f}% 开{opn:.2f} 高{hi:.2f} 低{lo:.2f} 量{vol:.0f}万手 额{amt:.0f}万 换手{turn:.2f}% 日内位置:{pos:.0%} 外内比:{oi:.2f}")

print()
print("="*80)
print("  S3 候选实时数据 (尾盘确认)")
print("="*80)
for sym in s3_list:
    n,c,cur,pre,opn,hi,lo,pct,vol,amt,turn,pos,oi = quick_rt(sym)
    print(f"  {n}({c}): 现价{cur:.2f} {pct:+.2f}% 开{opn:.2f} 高{hi:.2f} 低{lo:.2f} 量{vol:.0f}万手 额{amt:.0f}万 换手{turn:.2f}% 日内位置:{pos:.0%} 外内比:{oi:.2f}")
