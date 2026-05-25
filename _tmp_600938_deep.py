"""
中国海油 (sh600938) 深度分析脚本
生成时间: 2026-05-07
"""
import sqlite3, sys, io, requests, numpy as np, pandas as pd
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

CODE_BS = "sh.600938"
CODE_QQ = "sh600938"
CODE_NUM = "600938"
NAME = "中国海油"
DB_PATH = r"数据缓存/stock_cache.db"

# ─────────────────────────────────────────
# 1. 实时行情（腾讯）
# ─────────────────────────────────────────
print("=" * 70)
print(f"  {NAME}({CODE_NUM}) 深度分析  |  2026-05-07")
print("=" * 70)

try:
    url = f"https://qt.gtimg.cn/q={CODE_QQ}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split("~")
    def sf(i):
        try: return float(items[i])
        except: return 0.0

    rt_name  = items[1]
    rt_code  = items[2]
    cur  = sf(3); pre  = sf(4); opn  = sf(5)
    hi   = sf(33); lo   = sf(34); amp  = sf(43)
    chg  = sf(31); pct  = sf(32)
    vol  = sf(36); amt  = sf(37); turn = sf(38)
    outer= sf(7);  inner= sf(8)
    upd  = items[30] if len(items) > 30 else ""

    sell_prices=[]; sell_vols=[]; buy_prices=[]; buy_vols=[]
    for i in range(5,0,-1):
        sell_prices.append(sf(19+i*2)); sell_vols.append(sf(18+i*2))
    for i in range(1,6):
        buy_prices.append(sf(9+i*2)); buy_vols.append(sf(8+i*2))
    total_buy_vol  = sum(buy_vols)
    total_sell_vol = sum(sell_vols)

    print(f"\n【一】实时行情  (更新: {upd})")
    print(f"  现价: {cur:.2f}   昨收: {pre:.2f}   开盘: {opn:.2f}")
    print(f"  最高: {hi:.2f}   最低: {lo:.2f}   振幅: {amp:.2f}%")
    print(f"  涨跌额: {chg:+.2f}   涨跌幅: {pct:+.2f}%")
    print(f"  成交量: {vol:,.0f}手  成交额: {amt:,.0f}万  换手率: {turn:.2f}%")
    print(f"  外盘: {outer:,.0f}手  内盘: {inner:,.0f}手  外/内: {outer/max(inner,1):.2f}")

    # 五档盘口
    print(f"\n  --- 五档盘口 ---")
    for i in range(len(sell_prices)):
        sp = sell_prices[i]; sv = sell_vols[i]
        bar_len = int(sv / max(max(sell_vols+buy_vols),1)*20)
        print(f"  卖{5-i}: {sp:>8.2f} x {sv:>8,.0f}  {'▓'*bar_len}")
    print(f"  {'─'*40}")
    for i in range(len(buy_prices)):
        bp = buy_prices[i]; bv = buy_vols[i]
        bar_len = int(bv / max(max(sell_vols+buy_vols),1)*20)
        print(f"  买{i+1}: {bp:>8.2f} x {bv:>8,.0f}  {'█'*bar_len}")

    # 盘口增强
    print(f"\n  --- 盘口增强分析 ---")
    if total_sell_vol > 0:
        pr = total_buy_vol / total_sell_vol
        sig = "🟢买盘强势" if pr>1.5 else "🟢买盘偏强" if pr>1.1 else "⚪均衡" if pr>0.9 else "🔴卖盘偏强" if pr>0.6 else "🔴卖盘强势"
        print(f"  买卖压力比: {pr:.2f}  {sig}")
    if hi>lo:
        pos = (cur-lo)/(hi-lo)
        p_sig = "🟢高位(强势)" if pos>0.8 else "🟢中上位" if pos>0.5 else "🔴中下位" if pos>0.2 else "🔴低位(弱势)"
        print(f"  日内位置: {pos:.0%} {p_sig}")
    if total_buy_vol+total_sell_vol>0:
        wb = (total_buy_vol-total_sell_vol)/(total_buy_vol+total_sell_vol)*100
        wb_sig = "🟢买方优势" if wb>30 else "🟢偏多" if wb>10 else "⚪均衡" if wb>-10 else "🔴偏空"
        print(f"  委比: {wb:+.1f}%  {wb_sig}")
    oi_r = outer/max(inner,1)
    oi_sig = "🟢主动买入强" if oi_r>1.3 else "🟢偏买入" if oi_r>1.05 else "⚪均衡" if oi_r>0.95 else "🔴偏卖出" if oi_r>0.7 else "🔴主动卖出强"
    print(f"  外/内盘: {oi_sig}")
    rt_ok = True
except Exception as e:
    print(f"  ⚠️ 实时行情获取失败: {e}")
    cur = 0; pct = 0; vol = 0; amt = 0; turn = 0; hi = 0; lo = 0
    rt_ok = False

# ─────────────────────────────────────────
# 2. 120日K线技术分析
# ─────────────────────────────────────────
print(f"\n\n【二】120日K线技术分析")
print("=" * 70)

conn = sqlite3.connect(DB_PATH)
df_all = pd.read_sql(
    "SELECT date,open,high,low,close,volume,amount FROM kline_daily WHERE code=? ORDER BY date",
    conn, params=(CODE_BS,))
df_all['date'] = pd.to_datetime(df_all['date'])

total_days = len(df_all)
print(f"  DB记录: {total_days} 个交易日")
if total_days < 20:
    print("  ⚠️ 数据不足，请先更新缓存")
    conn.close()
    sys.exit(0)

df = df_all.tail(120).copy().reset_index(drop=True)
print(f"  分析区间: {df.iloc[0]['date'].strftime('%Y-%m-%d')} ~ {df.iloc[-1]['date'].strftime('%Y-%m-%d')}")

last = df.iloc[-1]
close = last['close'] if not rt_ok else cur

# 均线
print(f"\n  --- 均线体系 (基于收盘价) ---")
mas = {}
for n in [5,10,20,30,60,120]:
    tail = df_all.tail(n)
    if len(tail) >= n:
        ma_val = tail['close'].mean()
        mas[n] = ma_val
        dist = (close - ma_val) / ma_val * 100
        flag = "✅上方" if close >= ma_val else "⚠️下方"
        print(f"  MA{n:3d} = {ma_val:.3f}  ({dist:+.1f}%)  {flag}")

# 均线排列
if all(k in mas for k in [5,10,20]):
    if mas[5]>mas[10]>mas[20]:
        arrange = "✅多头排列(MA5>MA10>MA20)"
    elif mas[5]<mas[10]<mas[20]:
        arrange = "❌空头排列(MA5<MA10<MA20)"
    else:
        arrange = "⚪均线交织(震荡)"
    print(f"  均线排列: {arrange}")

# 分阶段趋势
print(f"\n  --- 分阶段涨跌 ---")
for label, n in [('近5日',5),('近10日',10),('近20日',20),('近60日',60),('近120日',120)]:
    seg = df.tail(n) if n <= len(df) else df
    chg_seg = (seg.iloc[-1]['close'] / seg.iloc[0]['close'] - 1) * 100
    h_seg = seg['high'].max(); l_seg = seg['low'].min()
    print(f"  {label:6s}: 涨跌{chg_seg:+.1f}%  高{h_seg:.3f}  低{l_seg:.3f}")

# 近20日K线明细
print(f"\n  --- 近20日K线明细 ---")
df20 = df.tail(20).copy().reset_index(drop=True)
for _, row in df20.iterrows():
    d = str(row['date'])[:10]
    chg_d = (row['close']-row['open'])/row['open']*100
    body = abs(row['close']-row['open'])
    upper = row['high']-max(row['open'],row['close'])
    lower = min(row['open'],row['close'])-row['low']
    total_range = row['high']-row['low']
    bar_type = "阳" if row['close']>=row['open'] else "阴"
    shape = ""
    if total_range > 0:
        br = body/total_range
        if br<0.1: shape="十字星"
        elif abs(chg_d)>=5: shape="大阳线" if chg_d>0 else "大阴线"
        elif br<0.3 and lower>body*1.5: shape="锤子"
        elif br<0.3 and upper>body*1.5: shape="射击星"
    vol_m = row['volume']/10000
    print(f"  {d} {bar_type} {chg_d:+5.2f}% O{row['open']:7.3f} H{row['high']:7.3f} L{row['low']:7.3f} C{row['close']:7.3f} V{vol_m:6.0f}万 {shape}")

# 量能分析
print(f"\n  --- 量能分析 ---")
v5  = df.tail(5)['volume'].mean()
v10 = df.tail(10)['volume'].mean()
v20 = df.tail(20)['volume'].mean()
v60 = df.tail(60)['volume'].mean()
print(f"  5日均量: {v5/10000:.0f}万  10日: {v10/10000:.0f}万  20日: {v20/10000:.0f}万  60日: {v60/10000:.0f}万")
print(f"  量比(5/20): {v5/v20:.2f}  量比(5/60): {v5/v60:.2f}")
today_vol = df.iloc[-1]['volume']
print(f"  最新成交量: {today_vol/10000:.0f}万  vs 20日均量倍数: {today_vol/v20:.2f}x")

# 关键价位
print(f"\n  --- 关键价位支撑/压力 ---")
h120 = df['high'].max(); l120 = df['low'].min()
h60  = df.tail(60)['high'].max(); l60  = df.tail(60)['low'].min()
h20  = df.tail(20)['high'].max(); l20  = df.tail(20)['low'].min()
pos120 = (close-l120)/(h120-l120)*100 if h120>l120 else 0
print(f"  120日高{h120:.3f} 低{l120:.3f}  当前位置: {pos120:.0f}%")
print(f"  60日 高{h60:.3f} 低{l60:.3f}  当前位置: {(close-l60)/(h60-l60)*100 if h60>l60 else 0:.0f}%")
print(f"  20日 高{h20:.3f} 低{l20:.3f}  当前位置: {(close-l20)/(h20-l20)*100 if h20>l20 else 0:.0f}%")

# 近60日量价密集区
print(f"\n  --- 近60日筹码密集区 ---")
df60 = df.tail(60).copy()
price_bins = np.linspace(df60['low'].min(), df60['high'].max(), 11)
for i in range(len(price_bins)-1):
    lo_b, hi_b = price_bins[i], price_bins[i+1]
    mask = (df60['close'] >= lo_b) & (df60['close'] <= hi_b)
    vol_in = df60[mask]['volume'].sum()
    pct_c = vol_in / df60['volume'].sum() * 100 if df60['volume'].sum()>0 else 0
    bar = "#" * int(pct_c)
    marker = " <<< 当前" if lo_b <= close <= hi_b else ""
    print(f"  {lo_b:7.3f}-{hi_b:7.3f}: {pct_c:5.1f}% {bar}{marker}")

# MACD/KDJ/RSI/BOLL
print(f"\n  --- 技术指标 ---")
closes_arr = df_all.tail(130)['close'].values

ema12 = pd.Series(closes_arr).ewm(span=12).mean().values
ema26 = pd.Series(closes_arr).ewm(span=26).mean().values
dif = ema12 - ema26
dea = pd.Series(dif).ewm(span=9).mean().values
macd_bar = (dif - dea) * 2
print(f"  MACD:  DIF={dif[-1]:.4f}  DEA={dea[-1]:.4f}  柱={macd_bar[-1]:.4f}")
macd_trend = "多头" if dif[-1]>dea[-1] else "空头"
macd_accel = "红柱放大(多头加速)" if macd_bar[-1]>0 and macd_bar[-1]>macd_bar[-2] else \
             "红柱缩小(多头衰减)" if macd_bar[-1]>0 else \
             "绿柱缩小(空头衰减)" if macd_bar[-1]<0 and macd_bar[-1]>macd_bar[-2] else "绿柱放大(空头加速)"
print(f"         趋势: {macd_trend}  柱状: {macd_accel}")

df_kdj = df_all.tail(130).copy().reset_index(drop=True)
low9  = df_kdj['low'].rolling(9).min()
high9 = df_kdj['high'].rolling(9).max()
rsv   = (df_kdj['close']-low9)/(high9-low9)*100
kk    = rsv.ewm(com=2).mean()
dd    = kk.ewm(com=2).mean()
jj    = 3*kk - 2*dd
k_val = kk.iloc[-1]; d_val = dd.iloc[-1]; j_val = jj.iloc[-1]
kdj_sig = "超卖区↑" if k_val<20 else "超买区↓" if k_val>80 else \
          "金叉✅" if (k_val>d_val and kk.iloc[-2]<=dd.iloc[-2]) else \
          "死叉❌" if (k_val<d_val and kk.iloc[-2]>=dd.iloc[-2]) else \
          "多头" if k_val>d_val else "空头"
print(f"  KDJ:   K={k_val:.1f}  D={d_val:.1f}  J={j_val:.1f}  → {kdj_sig}")

delta = pd.Series(closes_arr).diff()
gain = delta.where(delta>0, 0); loss = -delta.where(delta<0, 0)
rsi14 = (100 - (100/(1+gain.rolling(14).mean()/loss.rolling(14).mean().replace(0,1e-9)))).iloc[-1]
rsi_sig = "超买⚠️" if rsi14>70 else "超卖✅" if rsi14<30 else "中性"
print(f"  RSI14: {rsi14:.1f}  → {rsi_sig}")

ma20_v = pd.Series(closes_arr).rolling(20).mean().iloc[-1]
std20  = pd.Series(closes_arr).rolling(20).std().iloc[-1]
boll_up = ma20_v + 2*std20; boll_dn = ma20_v - 2*std20
boll_pos = (close-boll_dn)/(boll_up-boll_dn)*100 if boll_up>boll_dn else 50
print(f"  BOLL:  上{boll_up:.3f}  中{ma20_v:.3f}  下{boll_dn:.3f}  位置{boll_pos:.0f}%")

conn.close()

# ─────────────────────────────────────────
# 3. 大盘指数背景
# ─────────────────────────────────────────
print(f"\n\n【三】大盘指数背景")
print("=" * 70)
conn2 = sqlite3.connect(DB_PATH)
for idx_code, idx_name in [('sh.000001','上证指数'),('sz.399001','深证成指'),('sz.399006','创业板')]:
    try:
        idf = pd.read_sql("SELECT date,close,volume FROM kline_daily WHERE code=? ORDER BY date", conn2, params=(idx_code,))
        if len(idf) >= 20:
            i5 = idf.tail(5)['close'].mean()
            i20 = idf.tail(20)['close'].mean()
            i60 = idf.tail(60)['close'].mean() if len(idf)>=60 else 0
            last_c = idf.iloc[-1]['close']
            chg5 = (last_c / idf.iloc[-6]['close']-1)*100 if len(idf)>5 else 0
            if i5>i20>i60 and i60>0: arr="多头排列"
            elif i5<i20<i60 and i60>0: arr="空头排列"
            else: arr="交织震荡"
            print(f"  {idx_name}: 最新{last_c:.2f}  近5日涨跌{chg5:+.1f}%  均线{arr}")
    except Exception as e:
        print(f"  {idx_name}: 读取失败 {e}")
conn2.close()

# ─────────────────────────────────────────
# 4. 多策略评分 S1/S2/S3
# ─────────────────────────────────────────
print(f"\n\n【四】多策略评分 (S1/S2/S3)")
print("=" * 70)

conn3 = sqlite3.connect(DB_PATH)
df_all2 = pd.read_sql(
    "SELECT date,open,high,low,close,volume,amount FROM kline_daily WHERE code=? ORDER BY date",
    conn3, params=(CODE_BS,))
df_all2['date'] = pd.to_datetime(df_all2['date'])
conn3.close()

if len(df_all2) < 30:
    print("  数据不足，跳过策略评分")
else:
    df_s = df_all2.tail(120).copy().reset_index(drop=True)
    last_row = df_s.iloc[-1]
    c = last_row['close']; o = last_row['open']
    h_d = last_row['high']; l_d = last_row['low']
    v_last = last_row['volume']
    v20_s = df_s.tail(20)['volume'].mean()
    v5_s  = df_s.tail(5)['volume'].mean()
    
    ma5_s  = df_s.tail(5)['close'].mean()
    ma10_s = df_s.tail(10)['close'].mean()
    ma20_s = df_s.tail(20)['close'].mean()
    ma60_s = df_s.tail(60)['close'].mean() if len(df_s)>=60 else 0

    price = c if not rt_ok else cur

    # ── S1 蓄力候选 (16分制，V8简化) ──
    print(f"\n  ▶ S1 蓄力候选 (16分制，A≥13 / B=11-12)")
    s1 = 0; s1_detail = []

    # ①缩量(3分): 近3日成交量 vs 20日均量
    shrink_3 = df_s.tail(3)['volume'].mean() / v20_s if v20_s>0 else 1
    if shrink_3 < 0.5:
        s1 += 3; s1_detail.append("①缩量极佳(3/3)")
    elif shrink_3 < 0.7:
        s1 += 2; s1_detail.append("①缩量良好(2/3)")
    elif shrink_3 < 0.85:
        s1 += 1; s1_detail.append("①缩量一般(1/3)")
    else:
        s1_detail.append("①缩量不足(0/3)")

    # ②横盘(4分): 近5日振幅 < 5%
    hi5 = df_s.tail(5)['high'].max(); lo5 = df_s.tail(5)['low'].min()
    amp5 = (hi5-lo5)/lo5*100 if lo5>0 else 99
    if amp5 < 2:
        s1 += 4; s1_detail.append(f"②横盘极好振幅{amp5:.1f}%(4/4)")
    elif amp5 < 3.5:
        s1 += 3; s1_detail.append(f"②横盘良好振幅{amp5:.1f}%(3/4)")
    elif amp5 < 5:
        s1 += 2; s1_detail.append(f"②横盘一般振幅{amp5:.1f}%(2/4)")
    elif amp5 < 7:
        s1 += 1; s1_detail.append(f"②横盘较差振幅{amp5:.1f}%(1/4)")
    else:
        s1_detail.append(f"②振幅太大{amp5:.1f}%(0/4)")

    # ③均线(4分): 价格在MA5上方且均线多头
    score_ma = 0
    if price > ma5_s: score_ma += 1
    if price > ma10_s: score_ma += 1
    if price > ma20_s: score_ma += 1
    if ma5_s > ma10_s: score_ma += 1
    s1 += score_ma; s1_detail.append(f"③均线得分({score_ma}/4)")

    # ④实体(3分): 近3日实体幅度 < 2%
    body_avg = df_s.tail(3).apply(lambda r: abs(r['close']-r['open'])/r['open']*100, axis=1).mean()
    if body_avg < 0.5:
        s1 += 3; s1_detail.append(f"④实体极小{body_avg:.1f}%(3/3)")
    elif body_avg < 1.2:
        s1 += 2; s1_detail.append(f"④实体较小{body_avg:.1f}%(2/3)")
    elif body_avg < 2:
        s1 += 1; s1_detail.append(f"④实体中等{body_avg:.1f}%(1/3)")
    else:
        s1_detail.append(f"④实体太大{body_avg:.1f}%(0/3)")

    # ⑤下影线(2分): 今日有下影线
    lower_w = min(o,c)-l_d
    range_w = h_d-l_d
    if range_w>0 and lower_w/range_w > 0.3:
        s1 += 2; s1_detail.append("⑤下影线明显(2/2)")
    elif range_w>0 and lower_w/range_w > 0.15:
        s1 += 1; s1_detail.append("⑤下影线弱(1/2)")
    else:
        s1_detail.append("⑤下影线无(0/2)")

    print(f"  S1总分: {s1}/16")
    for d in s1_detail: print(f"    {d}")
    if s1 >= 13: print(f"  ✅ S1 达到A级！")
    elif s1 >= 11: print(f"  ✅ S1 达到B级")
    else: print(f"  ❌ S1 未达标 (差{11-s1}分到B级)")

    # ── S2 大阳后缩量横盘 (10分制，A≥9 / B=8) ──
    print(f"\n  ▶ S2 大阳后缩量横盘 (10分制，A≥9 / B=8)")
    s2 = 0; s2_detail = []

    # 寻找近7日大阳线 (≥4%, 阳, vol≥1.5x)
    df_find = df_s.tail(10).copy().reset_index(drop=True)
    big_candle_idx = None; big_candle = None
    for i in range(len(df_find)-2, -1, -1):
        row_c = df_find.iloc[i]
        pct_bar = (row_c['close']-row_c['open'])/row_c['open']*100
        vol_ratio = row_c['volume'] / (df_s.head(len(df_s)-len(df_find)+i)['volume'].mean() if len(df_s)>5 else v20_s)
        if pct_bar >= 4 and row_c['close'] > row_c['open'] and vol_ratio >= 1.5:
            big_candle_idx = i; big_candle = row_c
            break

    if big_candle is None:
        # 放宽到3%
        for i in range(len(df_find)-2, -1, -1):
            row_c = df_find.iloc[i]
            pct_bar = (row_c['close']-row_c['open'])/row_c['open']*100
            if pct_bar >= 3 and row_c['close'] > row_c['open']:
                big_candle_idx = i; big_candle = row_c
                break

    if big_candle is None:
        print(f"  ❌ 近10日无≥3%大阳线，S2不适用")
        s2_detail.append("无大阳线(0)")
    else:
        days_since = len(df_find)-1-big_candle_idx
        bc_pct = (big_candle['close']-big_candle['open'])/big_candle['open']*100
        s2_detail.append(f"大阳线: {str(big_candle['date'])[:10]} 涨{bc_pct:.1f}% (距今{days_since}日)")
        print(f"  大阳线: {str(big_candle['date'])[:10]}  涨幅{bc_pct:.1f}%  距今{days_since}日")

        # ①缩量程度(2分)
        if days_since > 0:
            after_vols = df_find.iloc[big_candle_idx+1:]['volume'].mean()
            vol_shrink = after_vols / big_candle['volume'] if big_candle['volume']>0 else 1
            if vol_shrink < 0.5:
                s2 += 2; s2_detail.append(f"①缩量极佳{vol_shrink:.2f}(2/2)")
            elif vol_shrink < 0.7:
                s2 += 1; s2_detail.append(f"①缩量良好{vol_shrink:.2f}(1/2)")
            else:
                s2_detail.append(f"①缩量不足{vol_shrink:.2f}(0/2)")
        else:
            s2_detail.append("①当日大阳，无后续缩量数据")

        # ②价格守住(2分): 现价 >= 大阳开盘价
        bc_open = big_candle['open']
        if price >= big_candle['close']:
            s2 += 2; s2_detail.append(f"②守收盘{big_candle['close']:.3f}✅(2/2)")
        elif price >= bc_open:
            s2 += 1; s2_detail.append(f"②守开盘{bc_open:.3f}(1/2)")
        else:
            s2_detail.append(f"②跌破开盘价{bc_open:.3f}❌(0/2)")

        # ③板块强度(2分): 能源/石油板块是否强势(简化判断)
        s2 += 1; s2_detail.append("③板块(默认中性1/2，需板块数据验证)")

        # ④均线配合(2分)
        if price > ma5_s and price > ma10_s:
            s2 += 2; s2_detail.append("④均线配合好(2/2)")
        elif price > ma10_s:
            s2 += 1; s2_detail.append("④均线一般(1/2)")
        else:
            s2_detail.append("④均线差(0/2)")

        # V8.8新增: ⑤大阳幅度≥6%(1分)
        if bc_pct >= 6:
            s2 += 1; s2_detail.append(f"⑤大阳幅度{bc_pct:.1f}%≥6%(+1)")

        # V8.8新增: ⑥大阳量能≥2x(1分)
        # (用大阳当日量 vs 之前20日均量粗估)
        hist_vol_avg = df_s.tail(30).iloc[:-days_since-1]['volume'].mean() if days_since < 25 else v20_s
        candle_vol_ratio = big_candle['volume'] / hist_vol_avg if hist_vol_avg>0 else 0
        if candle_vol_ratio >= 2:
            s2 += 1; s2_detail.append(f"⑥大阳量能{candle_vol_ratio:.1f}x≥2x(+1)")
        else:
            s2_detail.append(f"⑥大阳量能{candle_vol_ratio:.1f}x<2x(0)")

    print(f"  S2总分: {s2}/10")
    for d in s2_detail: print(f"    {d}")
    if s2 >= 9: print(f"  ✅ S2 达到A级！")
    elif s2 >= 8: print(f"  ✅ S2 达到B级")
    else: print(f"  ❌ S2 未达标 (差{8-s2}分到B级)")

    # ── S3 放量突破新高 (6分制，A≥5 / B=4) ──
    print(f"\n  ▶ S3 放量突破新高 (6分制，A≥5 / B=4)")
    s3 = 0; s3_detail = []

    # 前置：当日是否突破20日新高
    high20_prev = df_s.tail(21).iloc[:-1]['high'].max()
    is_breakout = price > high20_prev
    print(f"  20日前高: {high20_prev:.3f}  当前: {price:.3f}  {'✅突破' if is_breakout else '❌未突破'}")

    if not is_breakout:
        s3_detail.append("未突破20日高点，S3不适用")
    else:
        # ①突破幅度(2分)
        break_pct = (price - high20_prev) / high20_prev * 100
        if break_pct >= 3:
            s3 += 2; s3_detail.append(f"①突破幅度{break_pct:.1f}%≥3%(2/2)")
        elif break_pct >= 1:
            s3 += 1; s3_detail.append(f"①突破幅度{break_pct:.1f}%≥1%(1/2)")
        else:
            s3_detail.append(f"①突破幅度{break_pct:.1f}%<1%(0/2)")

        # ②放量程度(2分)
        if v_last/v20_s >= 2:
            s3 += 2; s3_detail.append(f"②放量{v_last/v20_s:.1f}x≥2x(2/2)")
        elif v_last/v20_s >= 1.5:
            s3 += 1; s3_detail.append(f"②放量{v_last/v20_s:.1f}x≥1.5x(1/2)")
        else:
            s3_detail.append(f"②放量不足{v_last/v20_s:.1f}x(0/2)")

        # ③均线配合(2分): 收盘>MA20>MA60
        if price > ma20_s and ma20_s > ma60_s and ma60_s > 0:
            s3 += 2; s3_detail.append("③均线完美排列(2/2)")
        elif price > ma20_s:
            s3 += 1; s3_detail.append("③价格>MA20(1/2)")
        else:
            s3_detail.append("③均线不支撑(0/2)")

    print(f"  S3总分: {s3}/6")
    for d in s3_detail: print(f"    {d}")
    if s3 >= 5: print(f"  ✅ S3 达到A级！")
    elif s3 >= 4: print(f"  ✅ S3 达到B级")
    else: print(f"  ❌ S3 未达标")

    # 最优策略汇总
    best_s = max([(s1, 'S1', 16, 13, 11), (s2,'S2',10,9,8), (s3,'S3',6,5,4)],
        key=lambda x: x[0]/x[2])
    print(f"\n  ⭐ 最优策略: {best_s[1]} (分数{best_s[0]}, 满分{best_s[2]})")

# ─────────────────────────────────────────
# 5. 操作建议
# ─────────────────────────────────────────
print(f"\n\n【五】操作建议")
print("=" * 70)

if rt_ok and cur > 0:
    ma5_op  = df_all.tail(5)['close'].mean()
    ma10_op = df_all.tail(10)['close'].mean()
    ma20_op = df_all.tail(20)['close'].mean()
    
    stop_loss_hard = cur * 0.97
    stop_loss_soft = cur * 0.985
    tp1 = cur * 1.04
    tp2 = cur * 1.06
    
    rr = (tp1 - cur) / (cur - stop_loss_hard)
    
    print(f"  现价:        {cur:.3f}")
    print(f"  硬止损(-3%): {stop_loss_hard:.3f}")
    print(f"  软止损(-1.5%收盘): {stop_loss_soft:.3f}")
    print(f"  止盈1(+4%):  {tp1:.3f}  卖50%")
    print(f"  止盈2(+6%):  {tp2:.3f}  清仓")
    print(f"  盈亏比(RR):  {rr:.2f}x  {'✅≥2.0合格' if rr>=2 else '⚠️<2.0偏低'}")
    print(f"  MA5支撑:     {ma5_op:.3f}")
    print(f"  MA10支撑:    {ma10_op:.3f}")
    print(f"\n  逻辑失效条件:")
    print(f"    · 放量跌破MA20({ma20_op:.3f}) → 立即清仓")
    print(f"    · 跌破近期低点 → 止损执行")
    print(f"    · 大盘跌>2% + 个股破MA5 → 不等止损直接走")

print(f"\n  ⚠️ 心理提醒:")
print(f"    · 大型权重股波动小，操作节奏要耐心")
print(f"    · 中国海油属低估防御型，趋势破位时不要死扛")
print(f"    · 若有浮盈>4%，严格执行止盈1，不要贪心")

print(f"\n{'='*70}")
print(f"  分析完成  |  {NAME}({CODE_NUM})")
print(f"{'='*70}")
