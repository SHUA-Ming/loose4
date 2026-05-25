"""临时扫描：在最强板块中找接近S2/S3门槛的候选票"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np
from db_cache import get_connection, init_db

conn = get_connection()

# 1. 获取板块5日动量排名
print("=" * 70)
print("  近miss候选扫描 — 最强板块中的S2/S3候选")
print("=" * 70)

sectors = pd.read_sql("""
    SELECT industry,
           SUM(CASE WHEN date >= date((SELECT MAX(date) FROM sector_daily),'-7 days') 
               THEN avg_pct ELSE 0 END) as sum_5d
    FROM sector_daily 
    WHERE date >= date((SELECT MAX(date) FROM sector_daily),'-30 days')
    GROUP BY industry
    HAVING sum_5d IS NOT NULL
    ORDER BY sum_5d DESC
""", conn)

total = len(sectors)
top_n = max(1, int(total * 0.3))
top_sectors = sectors.head(top_n)
print(f"\n板块总数: {total}, 前30%={top_n}个")
print("\n强势板块TOP10:")
for i, (_, r) in enumerate(top_sectors.head(10).iterrows()):
    print(f"  {i+1:2d}. {r['industry']:30s} 5日累涨: {r['sum_5d']:+.2f}%")

top_industry_list = top_sectors['industry'].tolist()

# 2. 获取所有K线数据
latest_date = pd.read_sql("SELECT MAX(date) as d FROM kline_daily", conn).iloc[0]['d']
print(f"\n数据最新日期: {latest_date}")

# 获取在强板块中的股票代码
placeholders = ','.join(['?' for _ in top_industry_list])
stocks = pd.read_sql(f"""
    SELECT code, code_name as name, industry FROM stock_industry
    WHERE industry IN ({placeholders})
""", conn, params=top_industry_list)
print(f"强板块股票数: {len(stocks)}")

codes = stocks['code'].tolist()
if not codes:
    print("无股票数据")
    exit()

# 分批查 K线
all_kline = []
batch_size = 200
for i in range(0, len(codes), batch_size):
    batch = codes[i:i+batch_size]
    ph = ','.join(['?' for _ in batch])
    df = pd.read_sql(f"""
        SELECT code, date, open, close, high, low, volume, pctChg as pct_chg, turn
        FROM kline_daily
        WHERE code IN ({ph}) AND date >= date(?, '-120 days')
        ORDER BY code, date
    """, conn, params=batch + [latest_date])
    all_kline.append(df)

kline = pd.concat(all_kline, ignore_index=True)
print(f"K线数据行数: {len(kline)}")

# 3. 逐股分析S2/S3特征
results_s2 = []
results_s3 = []

for code in codes:
    sk = kline[kline['code'] == code].sort_values('date').reset_index(drop=True)
    if len(sk) < 30:
        continue
    
    info = stocks[stocks['code'] == code].iloc[0]
    name = info['name']
    industry = info['industry']
    
    last = sk.iloc[-1]
    last_close = last['close']
    last_pct = last['pct_chg']
    
    # 流通市值粗略过滤(用换手率过滤极小盘)
    if last['turn'] is not None and last['turn'] > 15:
        continue
    
    # MA计算
    sk['ma5'] = sk['close'].rolling(5).mean()
    sk['ma10'] = sk['close'].rolling(10).mean()
    sk['ma20'] = sk['close'].rolling(20).mean()
    sk['ma60'] = sk['close'].rolling(60).mean()
    sk['vol_ma5'] = sk['volume'].rolling(5).mean()
    sk['vol_ma20'] = sk['volume'].rolling(20).mean()
    
    cur = sk.iloc[-1]
    if pd.isna(cur['ma20']) or pd.isna(cur['ma60']):
        continue
    
    # === S2: 大阳后缩量横盘 ===
    # 找近7日有大阳线(>=4%, 阳线, 量>=1.3x)
    recent7 = sk.tail(7)
    big_candle = None
    for idx in range(max(0, len(sk)-7), len(sk)-1):  # 不含最后一天
        row = sk.iloc[idx]
        vol_avg = sk.iloc[max(0,idx-20):idx]['volume'].mean() if idx > 5 else sk.iloc[:idx]['volume'].mean()
        if (row['pct_chg'] >= 4 and row['close'] > row['open'] and 
            vol_avg > 0 and row['volume'] >= vol_avg * 1.3):
            big_candle = (idx, row)
    
    if big_candle is not None:
        bc_idx, bc_row = big_candle
        # 大阳线后的天数
        post_days = sk.iloc[bc_idx+1:]
        if len(post_days) >= 1:
            # 检查后续缩量
            post_vol_avg = post_days['volume'].mean()
            shrink_ratio = post_vol_avg / bc_row['volume'] if bc_row['volume'] > 0 else 1
            
            # 检查价格守住大阳线开盘价
            price_hold = (post_days['close'] >= bc_row['open'] * 0.97).all()
            
            # 板块强度(已在强板块内，给2分)
            sector_score = 2
            
            # 均线配合(MA5>MA10>MA20?)
            ma_align = 0
            if cur['ma5'] > cur['ma10']:
                ma_align += 1
            if cur['ma10'] > cur['ma20']:
                ma_align += 1
            
            # S2评分(8分制): 缩量2 + 价格守住2 + 板块强度2 + 均线配合2
            score = 0
            # 缩量
            if shrink_ratio <= 0.5:
                score += 2
            elif shrink_ratio <= 0.7:
                score += 1
            
            # 价格守住
            if price_hold and last_close >= bc_row['open']:
                score += 2
            elif last_close >= bc_row['open'] * 0.97:
                score += 1
            
            # 板块
            score += sector_score
            
            # 均线
            score += ma_align
            
            # 排除项
            excluded = False
            reason = ""
            # X1: 近5日单日跌>5%
            if (sk.tail(5)['pct_chg'] < -5).any():
                excluded = True
                reason = "X1:近5日暴跌"
            # X6: 放量滞涨  
            for j in range(max(0,len(sk)-3), len(sk)):
                r = sk.iloc[j]
                vol5 = sk.iloc[max(0,j-5):j]['volume'].mean() if j > 2 else 0
                if vol5 > 0 and r['volume'] > vol5 * 1.5 and r['pct_chg'] < 1:
                    excluded = True
                    reason = "X6:放量滞涨"
            # X8: 价格>MA60且MA60向下
            if cur['ma60'] > 0 and cur['close'] < cur['ma60']:
                excluded = True
                reason = "X4:价<MA60"
            
            # 梯队定位
            sector_stocks = kline[kline['code'].isin(
                stocks[stocks['industry']==industry]['code'].tolist()
            )]
            tier = "跟风"
            if len(sector_stocks) > 0:
                latest_sector = sector_stocks[sector_stocks['date']==latest_date]
                if len(latest_sector) > 0:
                    # 用近5日涨幅排名
                    pass  # 简化处理
            
            results_s2.append({
                'code': code, 'name': name, 'industry': industry,
                'score': score, 'last_close': last_close, 'last_pct': last_pct,
                'big_candle_pct': bc_row['pct_chg'], 'big_candle_date': bc_row['date'],
                'shrink_ratio': shrink_ratio, 'price_hold': price_hold,
                'ma_align': ma_align, 'excluded': excluded, 'reason': reason,
                'tier': tier, 'post_days': len(post_days)
            })
    
    # === S3: 放量突破新高 ===
    if len(sk) >= 20:
        high_20d = sk.tail(21).head(20)['close'].max()  # 前20日最高收盘
        vol_avg_20d = sk.tail(21).head(20)['volume'].mean()
        
        # 最新一天突破?
        if (last_close > high_20d and 
            last['close'] > last['open'] and
            vol_avg_20d > 0 and last['volume'] >= vol_avg_20d * 1.3 and
            cur['close'] > cur['ma20']):
            
            # S3评分(6分): 突破幅度2 + 放量程度2 + 板块力度2
            s3_score = 0
            
            # 突破幅度
            breakout_pct = (last_close - high_20d) / high_20d * 100
            if breakout_pct >= 3:
                s3_score += 2
            elif breakout_pct >= 1:
                s3_score += 1
            
            # 放量
            vol_ratio = last['volume'] / vol_avg_20d if vol_avg_20d > 0 else 0
            if vol_ratio >= 2.0:
                s3_score += 2
            elif vol_ratio >= 1.5:
                s3_score += 1
            
            # 板块(已在强板块)
            s3_score += 2
            
            # 排除
            s3_excluded = False
            s3_reason = ""
            # X2: 5日涨幅>25%
            pct_5d = (last_close / sk.iloc[-6]['close'] - 1) * 100 if len(sk) >= 6 else 0
            if pct_5d > 25:
                s3_excluded = True
                s3_reason = "X2:5日涨>25%"
            if cur['close'] < cur['ma60']:
                s3_excluded = True
                s3_reason = "价<MA60"
            
            results_s3.append({
                'code': code, 'name': name, 'industry': industry,
                'score': s3_score, 'last_close': last_close, 'last_pct': last_pct,
                'breakout_pct': breakout_pct, 'vol_ratio': vol_ratio,
                'pct_5d': pct_5d,
                'excluded': s3_excluded, 'reason': s3_reason
            })

# 4. 输出结果
print("\n" + "=" * 70)
print("  S2 大阳后缩量横盘 — 最强板块候选(含near-miss)")
print("=" * 70)

df_s2 = pd.DataFrame(results_s2)
if len(df_s2) > 0:
    df_s2 = df_s2.sort_values('score', ascending=False)
    
    # 先显示达标的(>=6分)
    passed = df_s2[~df_s2['excluded'] & (df_s2['score'] >= 6)]
    near = df_s2[~df_s2['excluded'] & (df_s2['score'] >= 4) & (df_s2['score'] < 6)]
    excluded = df_s2[df_s2['excluded']]
    
    print(f"\n✅ 达标 (score>=6): {len(passed)}只")
    for _, r in passed.head(10).iterrows():
        print(f"  {r['code']} {r['name']:10s} {r['industry']:20s} "
              f"评分:{r['score']}/8 现价:{r['last_close']:.2f} 今日:{r['last_pct']:+.2f}% "
              f"大阳:{r['big_candle_pct']:+.1f}%({r['big_candle_date']}) "
              f"缩量比:{r['shrink_ratio']:.2f} 守价:{'✓' if r['price_hold'] else '✗'} "
              f"均线:{r['ma_align']}/2 后{r['post_days']}天")
    
    print(f"\n⚠️ 接近达标 (score 4-5): {len(near)}只")
    for _, r in near.head(15).iterrows():
        print(f"  {r['code']} {r['name']:10s} {r['industry']:20s} "
              f"评分:{r['score']}/8 现价:{r['last_close']:.2f} 今日:{r['last_pct']:+.2f}% "
              f"大阳:{r['big_candle_pct']:+.1f}%({r['big_candle_date']}) "
              f"缩量比:{r['shrink_ratio']:.2f} 守价:{'✓' if r['price_hold'] else '✗'} "
              f"均线:{r['ma_align']}/2 后{r['post_days']}天")
    
    print(f"\n❌ 被排除 (有信号但触发排除项): {len(excluded)}只")
    for _, r in excluded.head(10).iterrows():
        print(f"  {r['code']} {r['name']:10s} 评分:{r['score']}/8 原因:{r['reason']}")
else:
    print("  无S2候选")

print("\n" + "=" * 70)
print("  S3 放量突破新高 — 最强板块候选(含near-miss)")
print("=" * 70)

df_s3 = pd.DataFrame(results_s3)
if len(df_s3) > 0:
    df_s3 = df_s3.sort_values('score', ascending=False)
    passed3 = df_s3[~df_s3['excluded'] & (df_s3['score'] >= 4)]
    near3 = df_s3[~df_s3['excluded'] & (df_s3['score'] >= 2) & (df_s3['score'] < 4)]
    excluded3 = df_s3[df_s3['excluded']]
    
    print(f"\n✅ 达标 (score>=4): {len(passed3)}只")
    for _, r in passed3.head(10).iterrows():
        print(f"  {r['code']} {r['name']:10s} {r['industry']:20s} "
              f"评分:{r['score']}/6 现价:{r['last_close']:.2f} 今日:{r['last_pct']:+.2f}% "
              f"突破:{r['breakout_pct']:.1f}% 量比:{r['vol_ratio']:.1f}x 5日涨:{r['pct_5d']:+.1f}%")
    
    print(f"\n⚠️ 接近达标 (score 2-3): {len(near3)}只")
    for _, r in near3.head(15).iterrows():
        print(f"  {r['code']} {r['name']:10s} {r['industry']:20s} "
              f"评分:{r['score']}/6 现价:{r['last_close']:.2f} 今日:{r['last_pct']:+.2f}% "
              f"突破:{r['breakout_pct']:.1f}% 量比:{r['vol_ratio']:.1f}x 5日涨:{r['pct_5d']:+.1f}%")
    
    print(f"\n❌ 被排除: {len(excluded3)}只")
    for _, r in excluded3.head(10).iterrows():
        print(f"  {r['code']} {r['name']:10s} 评分:{r['score']}/6 原因:{r['reason']} 5日涨:{r['pct_5d']:+.1f}%")
else:
    print("  无S3候选")

conn.close()
print("\n扫描完成。")
