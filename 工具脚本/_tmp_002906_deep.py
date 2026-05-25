#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""华阳集团 sz.002906 深度分析"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from db_cache import get_connection, init_db
from chip_cost import analyze_chip_cost
import pandas as pd
import numpy as np

init_db()
conn = get_connection()
code = 'sz.002906'

# 拉全部K线
df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', conn, params=[code])
for col in ['open','high','low','close','volume','amount','turn','pctChg']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df = df.dropna(subset=['close','volume'])

n = len(df)
print(f"华阳集团(002906) K线数据: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]} ({n}条)")

# 最近20天
recent = df.tail(20)
print(f"\n{'='*80}")
print(f"  最近20天K线")
print(f"{'='*80}")
for _, r in recent.iterrows():
    d = r['date']
    o, h, l, c = r['open'], r['high'], r['low'], r['close']
    v = r['volume']
    pct = r['pctChg']
    t = r['turn']
    print(f"  {d}  开{o:7.2f}  收{c:7.2f}  高{h:7.2f}  低{l:7.2f}  涨跌{pct:+6.2f}%  量{v/1e4:8.0f}万  换手{t:.2f}%")

# 关键指标
cls = df['close'].values
vols = df['volume'].values

last = cls[-1]
ma5 = np.mean(cls[-5:])
ma10 = np.mean(cls[-10:])
ma20 = np.mean(cls[-20:])
ma60 = np.mean(cls[-60:]) if n >= 60 else np.nan

vol5 = np.mean(vols[-5:])
vol10 = np.mean(vols[-10:])
vol20 = np.mean(vols[-20:])
vol60 = np.mean(vols[-60:]) if n >= 60 else np.nan

print(f"\n{'='*80}")
print(f"  汇总统计")
print(f"{'='*80}")
print(f"  收盘价: {last:.2f}")
print(f"  MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}")
print(f"  均线间距(5/10/20): {(max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/min(ma5,ma10,ma20)*100:.1f}%")
print(f"  现价vs MA60: {'上方' if last > ma60 else '下方'} ({(last/ma60-1)*100:+.1f}%)")
print()

# 量能
print(f"  5日均量: {vol5/1e4:.0f}万手  10日: {vol10/1e4:.0f}万手  20日: {vol20/1e4:.0f}万手")
print(f"  量比(5/20): {vol5/vol20:.2f}  量比(5/60): {vol5/vol60:.2f}")
print(f"  5日均换手: {np.mean(df['turn'].values[-5:]):.2f}%")

# 近5天波动
r5 = df.tail(5)
high5 = r5['high'].max()
low5 = r5['low'].min()
avg5 = r5['close'].mean()
wave5 = (high5 - low5) / avg5 * 100
print(f"\n  近5日波动: 高{high5:.2f} 低{low5:.2f} 幅{wave5:.1f}%")
print(f"  近5日重心偏移: {(np.mean(cls[-5:])/np.mean(cls[-10:])-1)*100:+.2f}%")

# 60日最高/最低
if n >= 60:
    high60 = df.tail(60)['high'].max()
    low60 = df.tail(60)['low'].min()
    pct60 = (last / cls[-60] - 1) * 100
    drawdown = (last / high60 - 1) * 100
    print(f"  60日高点: {high60:.2f}  回撤: {drawdown:+.1f}%")
    print(f"  60日涨幅: {pct60:+.1f}%")

# 近10日涨跌序列
print(f"\n  近10日涨跌序列:")
pcts10 = df['pctChg'].values[-10:]
dates10 = df['date'].values[-10:]
for d, p in zip(dates10, pcts10):
    bar = '🟢' if p > 0 else '🔴' if p < 0 else '⚪'
    print(f"    {d}: {p:+6.2f}% {bar} {'█'*max(1,int(abs(p)*3))}")

# 大阳线识别(最近20天)
print(f"\n  近20天大阳线(>4%):")
for _, r in recent.iterrows():
    if r['pctChg'] >= 4:
        print(f"    {r['date']}: +{r['pctChg']:.2f}% 开{r['open']:.2f}→收{r['close']:.2f} 量{r['volume']/1e4:.0f}万")

# 连板/大涨统计
print(f"\n  近20天涨停(>9.5%):")
for _, r in recent.iterrows():
    if r['pctChg'] >= 9.5:
        print(f"    {r['date']}: +{r['pctChg']:.2f}%")

# 筹码分析
print(f"\n{'='*80}")
print(f"  筹码成本分析")
print(f"{'='*80}")
try:
    chip = analyze_chip_cost(df, current_price=last)
    if chip:
        print(f"  成本重心: {chip['cost_center']:.2f}  偏移: {(last/chip['cost_center']-1)*100:+.1f}%")
        print(f"  近20日成本: {chip.get('recent_cost',0):.2f}")
        print(f"  获利盘: {chip['profit_ratio']*100:.1f}%  套牢盘: {(1-chip['profit_ratio'])*100:.1f}%")
        print(f"  筹码集中度: {chip.get('concentration',0)*100:.1f}%")
        if chip.get('support'):
            print(f"  下方支撑: {chip['support'][0]:.2f} ~ {chip['support'][1]:.2f}")
        if chip.get('pressure'):
            print(f"  上方压力: {chip['pressure'][0]:.2f} ~ {chip['pressure'][1]:.2f}")
except Exception as e:
    print(f"  筹码分析异常: {e}")

# screener评分(S2大阳横盘)
print(f"\n{'='*80}")
print(f"  S2大阳横盘评分(手动)")
print(f"{'='*80}")

# 找最近的大阳线
big_yang = None
for i in range(len(df)-1, max(len(df)-20, 0), -1):
    if df['pctChg'].iloc[i] >= 4.0:
        big_yang = df.iloc[i]
        big_yang_idx = i
        break

if big_yang is not None:
    by_date = big_yang['date']
    by_pct = big_yang['pctChg']
    by_close = big_yang['close']
    by_open = big_yang['open']
    days_after = len(df) - 1 - big_yang_idx
    
    print(f"  大阳线: {by_date} +{by_pct:.1f}% (开{by_open:.2f}→收{by_close:.2f})")
    print(f"  大阳后天数: {days_after}天")
    
    # 缩量检查
    if days_after >= 1:
        post_vols = df['volume'].values[big_yang_idx+1:]
        by_vol = big_yang['volume']
        shrink = np.mean(post_vols) / by_vol
        print(f"  大阳后缩量比: {shrink:.2f} (大阳量{by_vol/1e4:.0f}万, 后均量{np.mean(post_vols)/1e4:.0f}万)")
    
    # 价格守住
    if days_after >= 1:
        post_closes = cls[big_yang_idx+1:]
        hold_ratio = np.mean(post_closes) / by_close
        print(f"  价格守住比: {hold_ratio:.3f} (>0.97=守住)")
        print(f"  大阳后最低收盘: {min(post_closes):.2f} vs 大阳收盘{by_close:.2f}")
    
    # 均线支撑
    print(f"\n  当前均线位置:")
    print(f"    MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60:.2f}")
    print(f"    收盘{last:.2f} 在MA5{'上' if last>=ma5 else '下'}方/MA10{'上' if last>=ma10 else '下'}方/MA20{'上' if last>=ma20 else '下'}方/MA60{'上' if last>=ma60 else '下'}方")

conn.close()
print(f"\n分析完成。")
