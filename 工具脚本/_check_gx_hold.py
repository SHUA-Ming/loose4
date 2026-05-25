#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests, sys
sys.stdout.reconfigure(encoding='utf-8')

r = requests.get('https://qt.gtimg.cn/q=sh603599', timeout=10)
r.encoding = 'gbk'
items = r.text.strip().split('"')[1].split('~')

def f(i):
    try:
        return float(items[i])
    except:
        return 0

cur = f(3)
pre = f(4)
hi = f(33)
lo = f(34)
pct = f(32)
outer = f(7)
inner = f(8)
turn = f(38)
upd = items[30] if len(items) > 30 else ''

entry_price = 14.85
float_pct = (cur - entry_price) / entry_price * 100
float_amt = cur - entry_price

print('=== 广信股份 当前持仓状态 ===')
print(f'现价: {cur:.2f}  昨收: {pre:.2f}  涨跌: {pct:+.2f}%')
print(f'日高: {hi:.2f}  日低: {lo:.2f}  距高: {(hi-cur)/hi*100:.2f}%')
print(f'外盘: {outer:.0f}  内盘: {inner:.0f}  外/内比: {outer/max(inner,1):.2f}')
print(f'换手: {turn:.2f}%')
print()
print('【持仓评估】')
print(f'你的买入价: {entry_price:.2f}')
print(f'当前浮盈/亏: {float_amt:+.2f}元  ({float_pct:+.2f}%)')
print(f'明天冲到15.05需要: +{(15.05-cur):.2f}元 (+{(15.05-cur)/entry_price*100:.2f}%)')
print(f'距日高回落: {(hi-cur)/hi*100:.2f}%')
print()
print('【风险诊断】')
if float_pct < 0:
    print(f'❌ 当前微亏 {float_pct:.2f}%，心态可能扭曲')
if outer/max(inner,1) < 1.0:
    print(f'❌ 外/内比 {outer/max(inner,1):.2f} < 1.0，资金净流出，尾盘弱信号')
if (hi-cur)/hi*100 > 1.0:
    print(f'⚠️  距离日高{(hi-cur)/hi*100:.2f}%，已经放松，再冲15.05风险大')
print()
print('【明天冲高的三个前置条件】')
print(f'1. 隔夜不出现"跳空低开" → 取决于夜间突发风险（无法控制）')
print(f'2. 高开后能稳住 > {14.85:.2f} → 前提是资金还要进场（当前外/内{outer/max(inner,1):.2f}说明难度大）')
print(f'3. 冲破15.05且不被砸 → 需要真实量能支持（一个短线小票难度极大）')
