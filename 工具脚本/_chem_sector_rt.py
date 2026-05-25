#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests, sys
import numpy as np
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

peers = [
    ('sh600309','万华化学'),
    ('sh600426','华鲁恒升'),
    ('sh600352','浙江龙盛'),
    ('sz002601','龙佰集团'),
    ('sz000683','远兴能源'),
    ('sh600596','新安股份'),
    ('sh601678','滨化股份'),
    ('sh600989','宝丰能源'),
    ('sh600141','兴发集团'),
]

vals = []
for sym, n in peers:
    try:
        r = requests.get(f'https://qt.gtimg.cn/q={sym}', timeout=8)
        r.encoding='gbk'
        items = r.text.strip().split('"')[1].split('~')
        f = lambda i: float(items[i]) if items[i] else 0.0
        pct, turn, amt = f(32), f(38), f(37)/10000
        name = items[1] if items[1] else n
        vals.append((sym, name, pct, turn, amt))
    except Exception:
        pass

vals = sorted(vals, key=lambda x: x[2], reverse=True)
for v in vals:
    print(f'{v[0]} {v[1]:<8} {v[2]:+6.2f}% 换手{v[3]:.2f}% 成交{v[4]:.2f}亿')

if vals:
    arr = np.array([x[2] for x in vals], dtype=float)
    print(f'\n样本平均涨跌: {arr.mean():+.2f}%  中位数: {np.median(arr):+.2f}%  正收益占比: {(arr>0).mean()*100:.0f}%')
