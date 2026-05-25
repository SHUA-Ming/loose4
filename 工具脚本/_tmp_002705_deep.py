#!/usr/bin/env python3
"""新宝股份 002705 深度分析 + S1/S2/S3三策略评分"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import requests
import pandas as pd
import numpy as np
from db_cache import get_connection, init_db

init_db()
conn = get_connection()

CODE = 'sz.002705'
TC = 'sz002705'

# ═══════════ 1. 拉实时行情 ═══════════
print("=" * 65)
print("  新宝股份 002705 深度分析")
print("=" * 65)

url = f"https://qt.gtimg.cn/q={TC}"
resp = requests.get(url, timeout=8)
resp.encoding = "gbk"
text = resp.text.strip()
idx = text.index('="') + 2
payload = text[idx:].rstrip('";')
items = payload.split("~")
def sf(i):
    try: return float(items[i])
    except: return 0.0

rt = {
    'name': items[1], 'code': items[2],
    'cur': sf(3), 'pre': sf(4), 'open': sf(5),
    'high': sf(33), 'low': sf(34), 'pct': sf(32),
    'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
    'outer': sf(7), 'inner': sf(8),
    'pe': sf(39), 'mktcap': sf(44), 'floatcap': sf(45),
}

print(f"\n  实时行情:")
print(f"  现价: {rt['cur']:.2f}  涨跌: {rt['pct']:+.2f}%")
print(f"  开{rt['open']:.2f} 高{rt['high']:.2f} 低{rt['low']:.2f} 昨收{rt['pre']:.2f}")
print(f"  成交量: {rt['vol']:.0f}万  成交额: {rt['amt']:.0f}万")
print(f"  换手率: {rt['turn']:.2f}%  外/内: {rt['outer']:.0f}/{rt['inner']:.0f}")
print(f"  流通市值: {rt['floatcap']:.0f}亿  PE: {rt['pe']:.1f}")

# ═══════════ 2. DB历史数据 ═══════════
df = pd.read_sql(f"""
    SELECT date, open, high, low, close, volume, amount, turn, pctChg
    FROM kline_daily WHERE code='{CODE}'
    ORDER BY date DESC LIMIT 120
""", conn)
df = df.sort_values('date').reset_index(drop=True)
print(f"\n  DB历史: {len(df)}条, {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

# 均线
for w in [5, 10, 20, 60]:
    df[f'ma{w}'] = df['close'].rolling(w).mean()

latest = df.iloc[-1]
ma5 = df['ma5'].iloc[-1]
ma10 = df['ma10'].iloc[-1]
ma20 = df['ma20'].iloc[-1]
ma60 = df['ma60'].iloc[-1]

print(f"\n  均线(DB最新日{latest['date']}):")
print(f"  MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}")
print(f"  均线排列: {'MA5>MA10>MA20 多头' if ma5>ma10>ma20 else 'MA5>MA10' if ma5>ma10 else '非多头'}")
print(f"  现价vs MA60: {rt['cur']:.2f} vs {ma60:.2f} → {'站上' if rt['cur']>ma60 else '破位'}")

# ═══════════ 3. 板块 ═══════════
industry_row = pd.read_sql(
    f"SELECT code_name, industry FROM stock_industry WHERE code='{CODE}'", conn
)
industry = industry_row['industry'].values[0] if len(industry_row) > 0 else '未知'
code_name = industry_row['code_name'].values[0] if len(industry_row) > 0 else rt['name']
print(f"\n  板块: {industry} ({code_name})")

# 板块5日动量排名
sector_df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY date', conn)
dates = sorted(sector_df['date'].unique())[-5:]
recent_sector = sector_df[sector_df['date'].isin(dates)]
mom5 = recent_sector.groupby('industry')['avg_pct'].sum().reset_index()
mom5.columns = ['industry', 'mom5']
mom5 = mom5.sort_values('mom5', ascending=False).reset_index(drop=True)
mom5['rank'] = range(1, len(mom5)+1)
total_sectors = len(mom5)

sr = mom5[mom5['industry'] == industry]
if len(sr) > 0:
    s_rank = int(sr['rank'].values[0])
    s_mom = float(sr['mom5'].values[0])
    s_pct = s_rank / total_sectors
    print(f"  板块5日动量: {s_mom:+.2f}%  排名: {s_rank}/{total_sectors} (前{s_pct:.0%})")
else:
    s_rank = total_sectors
    s_pct = 1.0
    print(f"  板块排名: 未找到")

# ═══════════ 4. 近期K线详情 ═══════════
print(f"\n{'─'*65}")
print("  近10日K线:")
print(f"{'─'*65}")
print(f"  {'日期':>12} {'开盘':>8} {'最高':>8} {'最低':>8} {'收盘':>8} {'涨跌%':>7} {'成交量':>12} {'换手%':>6}")
for _, r in df.tail(10).iterrows():
    print(f"  {r['date']:>12} {r['open']:>8.2f} {r['high']:>8.2f} {r['low']:>8.2f} {r['close']:>8.2f} {r['pctChg']:>+7.2f} {r['volume']:>12.0f} {r['turn']:>6.2f}")

# 今日实时(补充)
if rt['high'] > rt['low']:
    intra_pos = (rt['cur'] - rt['low']) / (rt['high'] - rt['low'])
else:
    intra_pos = 0.5
oi_ratio = rt['outer'] / max(rt['inner'], 1)
print(f"\n  今日实时: 开{rt['open']:.2f} 高{rt['high']:.2f} 低{rt['low']:.2f} 现{rt['cur']:.2f} {rt['pct']:+.2f}%")
print(f"  日内位置: {intra_pos:.0%}  外/内比: {oi_ratio:.2f}")

# ═══════════ 5. 关键量价数据 ═══════════
vol_5 = df['volume'].tail(5).mean()
vol_10 = df['volume'].tail(10).mean()
vol_20 = df['volume'].tail(20).mean()
vol_60 = df['volume'].tail(60).mean()

print(f"\n  量能: 5日均量={vol_5:.0f}  20日均量={vol_20:.0f}  60日均量={vol_60:.0f}")
print(f"  5/20量比={vol_5/vol_20:.2f}  5/60量比={vol_5/vol_60:.2f}")

# 近60日最高最低
high_60 = df['high'].tail(60).max()
low_60 = df['low'].tail(60).min()
high_20 = df['high'].tail(20).max()
pct_60 = (latest['close'] / df['close'].iloc[-60] - 1) * 100 if len(df) >= 60 else 0
drawdown = (latest['close'] / high_60 - 1) * 100

print(f"  60日高/低: {high_60:.2f}/{low_60:.2f}  60日涨幅: {pct_60:+.1f}%")
print(f"  从高点回撤: {drawdown:+.1f}%  20日最高: {high_20:.2f}")

# 近5日涨幅
pct_5 = (latest['close'] / df['close'].iloc[-6] - 1) * 100 if len(df) >= 6 else 0
print(f"  近5日涨幅: {pct_5:+.1f}%")

# 近60日有涨停？
limit_up = df.tail(60)
has_limit = (limit_up['pctChg'] >= 9.5).any()
print(f"  近60日涨停: {'有' if has_limit else '无'}")

# ═══════════ 6. 找大阳线 ═══════════
print(f"\n{'─'*65}")
print("  近10日大阳线(≥4%收阳):")
print(f"{'─'*65}")
recent10 = df.tail(10)
big_yangs = []
for i, r in recent10.iterrows():
    if r['pctChg'] >= 4.0 and r['close'] > r['open']:
        vol_ratio = r['volume'] / vol_20 if vol_20 > 0 else 0
        big_yangs.append({
            'date': r['date'], 'pct': r['pctChg'], 'open': r['open'], 
            'close': r['close'], 'high': r['high'], 'vol': r['volume'],
            'vol_ratio': vol_ratio
        })
        print(f"  {r['date']} +{r['pctChg']:.2f}%  开{r['open']:.2f} 收{r['close']:.2f} 量比{vol_ratio:.1f}x")

if not big_yangs:
    print("  近10日无大阳线")

# ═══════════ 7. 大阳线后续数据 ═══════════
if big_yangs:
    by = big_yangs[-1]  # 最近一根
    by_idx = df[df['date'] == by['date']].index[0]
    post = df.iloc[by_idx+1:]
    if len(post) > 0:
        print(f"\n  大阳({by['date']})后续:")
        for _, pr in post.iterrows():
            vr = pr['volume'] / by['vol']
            print(f"    {pr['date']} 收{pr['close']:.2f} {pr['pctChg']:+.2f}% 量缩比{vr:.2f}")
        avg_post_vol = post['volume'].mean()
        shrink_ratio = avg_post_vol / by['vol']
        min_post_close = post['close'].min()
        hold_ratio = min_post_close / by['close']
        print(f"  缩量比: {shrink_ratio:.2f}  守住比: {hold_ratio:.3f}")
    else:
        print(f"\n  大阳({by['date']})后无后续DB数据")
        shrink_ratio = None
        hold_ratio = None

# ═══════════════════════════════════════════════════
#  三策略评分
# ═══════════════════════════════════════════════════
print(f"\n{'═'*65}")
print("  【三策略评分】S1 / S2 / S3")
print(f"{'═'*65}")

# ──────── S1 评分 ────────
print(f"\n{'━'*65}")
print("  S1：洗盘蓄力策略 (20分制)")
print(f"{'━'*65}")

# S1前置
floatcap = rt['floatcap']
s1_f1 = 30 <= floatcap <= 300
s1_f2 = has_limit
s1_f3 = 10 <= pct_60 <= 60
s1_f4 = -20 <= drawdown <= -5
s1_f5 = rt['cur'] > ma60

print(f"  F1 流通市值{floatcap:.0f}亿(30~300): {'✅' if s1_f1 else '❌'}")
print(f"  F2 近60日涨停: {'✅' if s1_f2 else '❌'}")
print(f"  F3 近60日涨幅{pct_60:+.1f}%(10~60): {'✅' if s1_f3 else '❌'}")
print(f"  F4 回撤{drawdown:+.1f}%(-5~-20): {'✅' if s1_f4 else '❌'}")
print(f"  F5 现价>MA60({ma60:.2f}): {'✅' if s1_f5 else '❌'}")
s1_pre = s1_f1 and s1_f2 and s1_f3 and s1_f4 and s1_f5

if not s1_pre:
    fails = []
    if not s1_f1: fails.append(f"流通市值{floatcap:.0f}亿不在30-300")
    if not s1_f2: fails.append("近60日无涨停")
    if not s1_f3: fails.append(f"60日涨幅{pct_60:+.1f}%不在10-60%")
    if not s1_f4: fails.append(f"回撤{drawdown:+.1f}%不在-5~-20%")
    if not s1_f5: fails.append("现价<MA60")
    print(f"  → 前置过滤未通过: {'; '.join(fails)}")
    print(f"  → S1评级: ❌ 淘汰(前置不过)")
else:
    # S1核心评分
    last5 = df.tail(5)
    last20 = df.tail(20)
    
    # ① 缩量 (3分)
    ratio_5_20 = vol_5 / vol_20 if vol_20 > 0 else 1
    ratio_5_60 = vol_5 / vol_60 if vol_60 > 0 else 1
    avg_turn_5 = last5['turn'].mean()
    vol_shrink = last5['volume'].is_monotonic_decreasing
    s1_score1 = 0
    checks = [0.4 <= ratio_5_20 <= 0.8, ratio_5_60 <= 0.7, avg_turn_5 <= 2, vol_shrink]
    if sum(checks) >= 3: s1_score1 = 3
    elif sum(checks) >= 1: s1_score1 = 1
    print(f"\n  ① 缩量: 5/20={ratio_5_20:.2f} 5/60={ratio_5_60:.2f} 换手{avg_turn_5:.1f}% 逐日缩{'是' if vol_shrink else '否'} → {s1_score1}/3")
    
    # ② 横盘 (4分)
    h5_range = (last5['high'].max() - last5['low'].min()) / last5['close'].mean()
    avg5 = last5['close'].mean()
    avg10 = df.tail(10)['close'].mean()
    center_shift = abs(avg5 - avg10) / avg10
    s1_score2 = 0
    checks2 = [h5_range <= 0.05, center_shift <= 0.01]
    if all(checks2): s1_score2 = 4
    elif any(checks2): s1_score2 = 2
    print(f"  ② 横盘: 5日振幅{h5_range:.1%} 重心偏移{center_shift:.1%} → {s1_score2}/4")
    
    # ③ 均线粘合 (4分)
    ma_vals = [ma5, ma10, ma20]
    ma_spread = (max(ma_vals) - min(ma_vals)) / np.mean(ma_vals)
    ma5_gt_10 = ma5 > ma10
    s1_score3 = 0
    if ma_spread <= 0.03 and rt['cur'] > ma60 and ma5_gt_10: s1_score3 = 4
    elif ma_spread <= 0.05 and ma5_gt_10: s1_score3 = 2
    print(f"  ③ 均线粘合: 间距{ma_spread:.1%} MA5>10={'是' if ma5_gt_10 else '否'} → {s1_score3}/4")
    
    # ④ K线实体缩小 (3分)
    body5 = (last5['close'] - last5['open']).abs().mean()
    body20 = (last20['close'] - last20['open']).abs().mean()
    body_ratio = body5 / body20 if body20 > 0 else 1
    max_amp_3 = ((df.tail(3)['high'] - df.tail(3)['low']) / df.tail(3)['close']).max()
    avg_abs_pct_5 = last5['pctChg'].abs().mean()
    s1_score4 = 0
    checks4 = [body_ratio <= 0.5, max_amp_3 <= 0.03, avg_abs_pct_5 <= 1.5]
    if sum(checks4) >= 2: s1_score4 = 3
    elif sum(checks4) >= 1: s1_score4 = 1
    print(f"  ④ 实体缩小: 体比{body_ratio:.2f} 3日振幅{max_amp_3:.1%} 均|涨跌|{avg_abs_pct_5:.1f}% → {s1_score4}/3")
    
    # ⑤ 下影线阳线 (2分)
    s1_score5 = 0
    for _, r in last5.iterrows():
        if r['close'] > r['open']:
            body = r['close'] - r['open']
            lower = r['open'] - r['low']
            if body > 0 and lower >= 2 * body and 0 <= r['pctChg'] <= 2:
                s1_score5 = 2
                break
    print(f"  ⑤ 下影线阳线: {'有' if s1_score5 else '无'} → {s1_score5}/2")
    
    # ⑥ 十字星 (2分)
    cross_count = 0
    for _, r in last5.iterrows():
        body_pct = abs(r['close'] - r['open']) / r['open']
        if body_pct <= 0.005:
            cross_count += 1
    s1_score6 = 2 if cross_count >= 2 else (1 if cross_count >= 1 else 0)
    print(f"  ⑥ 十字星: {cross_count}根 → {s1_score6}/2")
    
    # ⑦ 红绿交替 (2分)
    colors = ['R' if r['close'] >= r['open'] else 'G' for _, r in last5.iterrows()]
    no_3_same = all(colors[i] != colors[i+1] or colors[i+1] != colors[i+2] for i in range(len(colors)-2))
    pct_range = all(-2 <= r['pctChg'] <= 2 for _, r in last5.iterrows())
    cum_pct = last5['pctChg'].sum()
    cum_ok = -2 <= cum_pct <= 3
    checks7 = [no_3_same, pct_range, cum_ok]
    s1_score7 = 2 if all(checks7) else (1 if sum(checks7) >= 1 else 0)
    print(f"  ⑦ 红绿交替: 颜色{''.join(colors)} 无3连={'是' if no_3_same else '否'} 幅度{'OK' if pct_range else 'X'} 累计{cum_pct:+.1f}% → {s1_score7}/2")
    
    s1_total = s1_score1 + s1_score2 + s1_score3 + s1_score4 + s1_score5 + s1_score6 + s1_score7
    s1_grade = "A级" if s1_total >= 16 else ("B级" if s1_total == 15 else "淘汰")
    print(f"\n  S1总分: {s1_total}/20 → {s1_grade}")

# ──────── S2 评分 ────────
print(f"\n{'━'*65}")
print("  S2：大阳后缩量横盘策略 (8分制)")
print(f"{'━'*65}")

if not big_yangs:
    print("  → 近10日无大阳线, S2直接淘汰")
    s2_total = 0
    s2_grade = "淘汰"
else:
    by = big_yangs[-1]
    s2_f1 = 30 <= floatcap <= 300
    s2_f2 = by['pct'] >= 4 and by['vol_ratio'] >= 1.5
    s2_f4 = rt['cur'] >= by['open']
    s2_f5 = rt['cur'] > ma60
    s2_f6 = s_pct <= 0.7  # 不在后30%
    
    print(f"  大阳线: {by['date']} +{by['pct']:.2f}% 量比{by['vol_ratio']:.1f}x")
    print(f"  F1 流通市值{floatcap:.0f}亿: {'✅' if s2_f1 else '❌'}")
    print(f"  F2 大阳≥4%+放量≥1.5x: {'✅' if s2_f2 else '❌'} (涨{by['pct']:.1f}%, 量比{by['vol_ratio']:.1f}x)")
    
    # F3 缩量检查
    by_idx_val = df[df['date'] == by['date']].index[0]
    post_data = df.iloc[by_idx_val+1:]
    if len(post_data) > 0:
        s2_f3 = all(post_data['volume'] < by['vol'] * 0.7)
        print(f"  F3 大阳后缩量<70%: {'✅' if s2_f3 else '❌'}")
    else:
        s2_f3 = True  # 无后续数据,用实时补充
        print(f"  F3 大阳后无DB数据(待实时验证)")
    
    print(f"  F4 现价≥大阳开盘({by['open']:.2f}): {'✅' if s2_f4 else '❌'}")
    print(f"  F5 现价>MA60({ma60:.2f}): {'✅' if s2_f5 else '❌'}")
    print(f"  F6 板块不在后30%({s_rank}/{total_sectors}): {'✅' if s2_f6 else '❌'}")
    
    s2_pre = s2_f1 and s2_f2 and s2_f4 and s2_f5 and s2_f6
    
    # X排除项
    if len(post_data) > 0:
        x1 = (post_data['pctChg'] < -3).any()
        x2 = ((post_data['volume'] > by['vol'] * 0.8) & (post_data['pctChg'] < 0)).any()
    else:
        x1 = False
        x2 = False
    x3 = rt['turn'] > 8
    print(f"  X1 大阳后跌>3%: {'❌排除' if x1 else '✅'}")
    print(f"  X2 放量砸盘: {'❌排除' if x2 else '✅'}")
    print(f"  X3 换手>8%: {'❌排除' if x3 else '✅'}")
    
    if not s2_pre or x1 or x2 or x3:
        print(f"  → S2前置/排除未过")
        s2_total = 0
        s2_grade = "淘汰"
    else:
        # 评分
        # ① 缩量 (2)
        if len(post_data) > 0:
            sr_val = post_data['volume'].mean() / by['vol']
        else:
            sr_val = 0.5  # 默认
        s2_s1 = 2 if sr_val <= 0.5 else (1 if sr_val <= 0.7 else 0)
        
        # ② 价格守住 (2)
        hr_val = rt['cur'] / by['close']
        s2_s2 = 2 if hr_val >= 0.99 else (1 if hr_val >= 0.97 else 0)
        
        # ③ 板块 (2)
        s2_s3 = 2 if s_pct <= 0.3 else (1 if s_pct <= 0.5 else 0)
        
        # ④ 均线 (2)
        s2_s4 = 2 if ma5 > ma10 > ma20 else (1 if ma5 > ma10 else 0)
        
        print(f"\n  ① 缩量: 缩比{sr_val:.2f} → {s2_s1}/2")
        print(f"  ② 守住: 现价/大阳收{hr_val:.3f} → {s2_s2}/2")
        print(f"  ③ 板块: 排名{s_rank}/{total_sectors}(前{s_pct:.0%}) → {s2_s3}/2")
        print(f"  ④ 均线: {'MA5>10>20' if ma5>ma10>ma20 else 'MA5>10' if ma5>ma10 else '非多头'} → {s2_s4}/2")
        
        s2_total = s2_s1 + s2_s2 + s2_s3 + s2_s4
        s2_grade = "A级" if s2_total >= 7 else ("B级" if s2_total == 6 else "淘汰")
        print(f"\n  S2总分: {s2_total}/8 → {s2_grade}")

# ──────── S3 评分 ────────
print(f"\n{'━'*65}")
print("  S3：放量突破新高策略 (6分制)")
print(f"{'━'*65}")

# 用实时数据判断今日是否突破
today_break = rt['cur'] > high_20
today_vol_ratio = rt['vol'] * 10000 / vol_20 if vol_20 > 0 else 0  # 注意单位
# 也看DB最新日是否突破
db_break = latest['close'] > df['high'].tail(21).iloc[:-1].max() if len(df) > 20 else False
db_vol_ratio = latest['volume'] / vol_20 if vol_20 > 0 else 0

print(f"  DB最新({latest['date']}): 收{latest['close']:.2f} vs 20日高{high_20:.2f} → {'突破' if db_break else '未突破'}")
print(f"  DB当日量比: {db_vol_ratio:.1f}x")
print(f"  实时现价: {rt['cur']:.2f} vs 20日高{high_20:.2f} → {'突破' if today_break else '未突破'}")

# 用DB最新日或实时来判断
use_rt = today_break
if db_break:
    break_close = latest['close']
    break_vol_r = db_vol_ratio
    break_pct = latest['pctChg']
elif today_break:
    break_close = rt['cur']
    break_vol_r = today_vol_ratio
    break_pct = rt['pct']
else:
    break_close = 0
    break_vol_r = 0
    break_pct = 0

s3_f2 = break_close > high_20
s3_f3 = break_vol_r >= 1.5
s3_f4 = rt['cur'] > rt['open'] if today_break else (latest['close'] > latest['open'] if db_break else False)
s3_f5 = rt['cur'] > ma20 > ma60
s3_f6 = s_pct <= 0.5

print(f"\n  F1 流通市值{floatcap:.0f}亿: {'✅' if s2_f1 else '❌'}")
print(f"  F2 突破20日高: {'✅' if s3_f2 else '❌'}")
print(f"  F3 放量≥1.5x: {'✅' if s3_f3 else '❌'} ({break_vol_r:.1f}x)")
print(f"  F4 收阳: {'✅' if s3_f4 else '❌'}")
print(f"  F5 现价>MA20>MA60: {'✅' if s3_f5 else '❌'}")
print(f"  F6 板块前50%: {'✅' if s3_f6 else '❌'}")

s3_pre = s2_f1 and s3_f2 and s3_f3 and s3_f4 and s3_f5 and s3_f6

# X排除
s3_x1 = break_pct > 7 if break_close > 0 else False
s3_x2 = pct_5 > 20
s3_x3 = rt['turn'] > 10
print(f"  X1 涨幅>7%: {'❌排除' if s3_x1 else '✅'} ({break_pct:+.1f}%)")
print(f"  X2 5日涨>20%: {'❌排除' if s3_x2 else '✅'} ({pct_5:+.1f}%)")
print(f"  X3 换手>10%: {'❌排除' if s3_x3 else '✅'} ({rt['turn']:.1f}%)")

if not s3_pre or s3_x1 or s3_x2 or s3_x3:
    fails3 = []
    if not s3_f2: fails3.append("未突破20日高")
    if not s3_f3: fails3.append(f"量比{break_vol_r:.1f}x不足1.5x")
    if not s3_f5: fails3.append("MA20未>MA60" if not (ma20 > ma60) else "现价<MA20")
    if s3_x1: fails3.append("涨幅>7%")
    if s3_x2: fails3.append("5日涨>20%")
    print(f"  → S3未通过: {'; '.join(fails3) if fails3 else '前置条件不满足'}")
    s3_total = 0
    s3_grade = "淘汰"
else:
    break_above = (break_close / high_20 - 1) * 100
    s3_s1 = 2 if break_above > 3 else (1 if break_above > 1 else 0)
    s3_s2 = 2 if break_vol_r > 2.5 else (1 if break_vol_r > 1.5 else 0)
    s3_s3 = 2 if s_pct <= 0.3 else (1 if s_pct <= 0.5 else 0)
    
    print(f"\n  ① 突破幅度: {break_above:+.1f}% → {s3_s1}/2")
    print(f"  ② 放量程度: {break_vol_r:.1f}x → {s3_s2}/2")
    print(f"  ③ 板块力度: 前{s_pct:.0%} → {s3_s3}/2")
    
    s3_total = s3_s1 + s3_s2 + s3_s3
    s3_grade = "A级" if s3_total >= 5 else ("B级" if s3_total == 4 else "淘汰")
    print(f"\n  S3总分: {s3_total}/6 → {s3_grade}")

# ═══════════ 综合结论 ═══════════
print(f"\n{'═'*65}")
print("  【综合结论】三策略对比")
print(f"{'═'*65}")

results = []
if 's1_total' in dir() or 's1_total' in locals():
    try:
        results.append(('S1', s1_total, 20, s1_grade))
    except:
        results.append(('S1', 0, 20, '淘汰(前置不过)'))
else:
    results.append(('S1', 0, 20, '淘汰(前置不过)'))

results.append(('S2', s2_total, 8, s2_grade))
results.append(('S3', s3_total, 6, s3_grade))

best = max(results, key=lambda x: ({'A级':3,'B级':2}.get(x[3],0), x[1]))

for s, score, full, grade in results:
    marker = " ★最优" if s == best[0] and grade not in ['淘汰', '淘汰(前置不过)'] else ""
    print(f"  {s}: {score}/{full} → {grade}{marker}")

print(f"\n  最终推荐策略: {best[0]} ({best[3]})" if best[3] not in ['淘汰','淘汰(前置不过)'] else f"\n  三策略均不达标")

# 如果都淘汰，给出达标路径
all_fail = all(g in ['淘汰','淘汰(前置不过)'] for _,_,_,g in results)
if all_fail:
    print(f"\n{'─'*65}")
    print("  【达标路径分析】如何才能达标？")
    print(f"{'─'*65}")
    print("  S1: 需要均线粘合+缩量横盘5天以上+波动收窄。")
    print(f"  → 目前MA间距过大(反弹后均线发散)，需等MA5/10/20收敛到3%以内。")
    print(f"  → 预计需要横盘整理5-10天才可能达标。")
    print()
    print("  S2: 需要近5日内有大阳(≥4%放量1.5x) + 后续缩量横盘不回吐。")
    if big_yangs:
        by = big_yangs[-1]
        if by['vol_ratio'] < 1.5:
            print(f"  → 大阳线量比{by['vol_ratio']:.1f}x不足1.5x，需更强放量。")
        print(f"  → 或者等下一根大阳线出现，之后缩量守住再介入。")
    else:
        print(f"  → 需要先出现一根≥4%的放量大阳线。")
    print()
    print("  S3: 需要放量突破20日新高 + 板块配合。")
    print(f"  → 20日高点{high_20:.2f}，需收盘站上且量比≥1.5x。")

# 操作计划
if not all_fail:
    print(f"\n{'─'*65}")
    print("  【操作计划】")
    print(f"{'─'*65}")
    if best[0] == 'S2' and big_yangs:
        by = big_yangs[-1]
        buy_lo = by['close'] * 0.98
        buy_hi = by['close'] * 1.02
        stop = by['open']
        tp1 = rt['cur'] * 1.03
        tp2 = rt['cur'] * 1.05
        print(f"  策略: S2 大阳后缩量横盘二次启动")
        print(f"  买入区间: {buy_lo:.2f} ~ {buy_hi:.2f} (大阳收盘价±2%)")
        print(f"  硬止损: {stop:.2f} (大阳开盘价)")
        print(f"  M3止盈: +3%={tp1:.2f}卖1/3  +5%={tp2:.2f}卖1/3  剩1/3移动止盈")
        print(f"  D3强制清仓: 最迟{best[0]}后第3个交易日")
        print(f"  仓位: {'1/4' if best[3]=='A级' else '1/8'}")
    elif best[0] == 'S3':
        stop3 = rt['cur'] * 0.97
        tp1_3 = rt['cur'] * 1.05
        tp2_3 = rt['cur'] * 1.08
        print(f"  策略: S3 放量突破追强")
        print(f"  买入: 回踩不破{high_20:.2f}时介入")
        print(f"  硬止损: {stop3:.2f} (-3%)")
        print(f"  止盈: +5%={tp1_3:.2f}卖1/2  +8%={tp2_3:.2f}全清")
        print(f"  仓位: {'1/4' if best[3]=='A级' else '1/8'}")
    elif best[0] == 'S1':
        print(f"  策略: S1 洗盘蓄力")
        print(f"  等待启动信号: 放量突破或MA5金叉MA10")
        print(f"  仓位: {'1/4' if best[3]=='A级' else '1/8'}")

conn.close()
print()
