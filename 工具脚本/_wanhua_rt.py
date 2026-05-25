#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests, sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

syms = 'sh000001,sz399001,sz399006,sh000300,sh600309'
r = requests.get(f'https://qt.gtimg.cn/q={syms}', timeout=10)
r.encoding = 'gbk'

for line in r.text.strip().split('\n'):
    if '="' not in line or '~' not in line:
        continue
    items = line.split('="')[1].rstrip('";').split('~')

    def f(i):
        try:
            return float(items[i])
        except Exception:
            return 0.0

    name = items[1]
    code = items[2]
    cur = f(3)
    pct = f(32)
    hi = f(33)
    lo = f(34)
    turn = f(38)
    amt = f(37)
    upd = items[30] if len(items) > 30 else ''
    outer = f(7)
    inner = f(8)
    print(f'{name}({code}) 价:{cur:.2f} 涨跌:{pct:+.2f}% 高/低:{hi:.2f}/{lo:.2f} 换手:{turn:.2f}% 成交额:{amt/10000:.2f}亿 外内比:{outer/max(inner,1):.2f} 更新:{upd}')
