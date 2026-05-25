#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V8规则·近两周回测（2026-04-03 ~ 2026-04-20）
对比: V8新规 vs 旧V6规则 vs 无过滤基线

V8核心变更：
  1. S1评分: 20分7指标 → 16分5指标（合并波动收敛, 新增板块排名+筹码结构）
  2. 止盈统一: +4%卖半 + 移动止盈回落2.5%清仓
  3. 止损统一: 盘中-3%硬止损, 收盘-1.5%软止损
  4. 板块过滤: 只看前30~50%（V8激进版）
  5. 弱势策略: 仓位×0.3（替代原禁用）
  6. S1排除项: 新增X6放量滞涨/X7缩量假反弹
"""
import sys, os, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd
import numpy as np
from collections import defaultdict

init_db()
conn = get_connection()

print("=" * 80)
print("  V8规则·近两周回测")
print("=" * 80)
print("\n加载数据库...")

all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

# 加载行业映射
industry_map = {}
try:
    ind_df = pd.read_sql('SELECT code, industry FROM stock_industry', conn)
    industry_map = dict(zip(ind_df['code'], ind_df['industry']))
except:
    pass

# 加载板块每日数据
sector_daily_df = None
try:
    sector_daily_df = pd.read_sql('SELECT * FROM sector_daily ORDER BY industry, date', conn)
    for c in ['avg_pct']:
        sector_daily_df[c] = pd.to_numeric(sector_daily_df[c], errors='coerce')
    print(f"  板块数据: {len(sector_daily_df)} 条, {sector_daily_df['industry'].nunique()} 个行业")
except:
    print("  板块数据: 不可用")

conn.close()

# ═══ 构建股票字典 ═══
stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df) >= 60:
        stock_dict[code] = df

# ═══ 指数数据（大盘环境） ═══
index_dict = {}
for code, group in all_data.groupby('code'):
    if code in ('sh.000001', 'sz.399001', 'sz.399006'):
        df = group.dropna(subset=['close']).reset_index(drop=True)
        index_dict[code] = df

print(f"  有效股票数: {len(stock_dict)}")
print(f"  指数数据: {list(index_dict.keys())}")

# ═══ 回测日期范围 ═══
all_dates_list = sorted(all_data['date'].unique())
BT_START = '2026-04-03'
BT_END   = '2026-04-20'
bt_dates = [d for d in all_dates_list if BT_START <= d <= BT_END]
print(f"  回测范围: {bt_dates[0]} ~ {bt_dates[-1]}, 共{len(bt_dates)}天")

# ═══ 大盘状态/情绪评分预计算 ═══
def _compute_market_state(date):
    """计算某日的大盘状态和市场模式"""
    idx_df = index_dict.get('sh.000001')
    if idx_df is None:
        return {'mode': 'M2', 'emotion': 5, 'bearish': False}
    dl = idx_df['date'].values.tolist()
    if date not in dl:
        return {'mode': 'M2', 'emotion': 5, 'bearish': False}
    i = dl.index(date)
    if i < 60:
        return {'mode': 'M2', 'emotion': 5, 'bearish': False}

    cls = idx_df['close'].values[:i+1].astype(float)
    pcts = idx_df['pctChg'].values[:i+1].astype(float)

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:])
    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
    pct5 = (cls[-1] - cls[-6]) / cls[-6] * 100 if len(cls) > 5 else 0

    # 情绪评分 (0-10)
    bullish = sum([ma5>ma10, ma10>ma20, ma20>ma60, cls[-1]>ma60, cls[-1]>ma20])
    vol520 = np.mean(idx_df['volume'].values[max(0,i-4):i+1].astype(float)) / np.mean(idx_df['volume'].values[max(0,i-19):i+1].astype(float)) if i >= 19 else 1.0
    emotion = 5
    if bullish >= 4: emotion += 2
    elif bullish <= 1: emotion -= 2
    if vol520 > 1.2: emotion += 1
    elif vol520 < 0.7: emotion -= 1
    if pct5 > 3: emotion += 1
    elif pct5 < -3: emotion -= 1
    emotion = max(0, min(10, emotion))

    # 市场模式
    bearish = ma5 < ma10 < ma20
    if emotion <= 1 or emotion >= 10:
        mode = 'M5'
    elif emotion <= 3 and cls[-1] < ma60:
        mode = 'M1'
    elif emotion >= 8 and cls[-1] > ma60 and ma20 > ma60:
        mode = 'M4'
    elif 6 <= emotion <= 7 and pct5 > 3 and cls[-1] > ma20:
        mode = 'M3'
    else:
        mode = 'M2'

    return {
        'mode': mode, 'emotion': emotion, 'bearish': bearish,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'pct5': pct5, 'last': cls[-1],
    }


# ═══ 板块排名预计算（每个日期） ═══
def _compute_sector_rank(date):
    """计算某个日期前5日的板块排名 {industry: percentile 0~1}"""
    if sector_daily_df is None:
        return {}
    sec = sector_daily_df[sector_daily_df['date'] <= date].copy()
    if sec.empty:
        return {}
    momentum = {}
    for ind, grp in sec.groupby('industry'):
        grp = grp.sort_values('date')
        last5 = grp.tail(5)
        if len(last5) >= 3:
            momentum[ind] = float(last5['avg_pct'].mean())
    if not momentum:
        return {}
    sorted_secs = sorted(momentum.items(), key=lambda x: x[1])
    n = len(sorted_secs)
    return {ind: i / max(n-1, 1) for i, (ind, _) in enumerate(sorted_secs)}


# ═══ 个股板块内排名（每个日期） ═══
def _compute_stock_tier(date):
    """计算每只股票在板块内的排名 {code: percentile 0(强)~1(弱)}"""
    # 获取该日期索引
    tiers = {}
    sector_stocks = defaultdict(list)
    for code, df in stock_dict.items():
        dl = df['date'].values.tolist()
        if date not in dl:
            continue
        idx = dl.index(date)
        if idx < 5:
            continue
        cls = df['close'].values
        chg5 = (cls[idx] - cls[idx-5]) / cls[idx-5] * 100
        ind = industry_map.get(code, '')
        if ind:
            sector_stocks[ind].append((code, chg5))

    for ind, stocks in sector_stocks.items():
        stocks_sorted = sorted(stocks, key=lambda x: x[1], reverse=True)
        n_s = len(stocks_sorted)
        for rank_i, (cd, _) in enumerate(stocks_sorted):
            tiers[cd] = rank_i / max(n_s - 1, 1)  # 0=最强
    return tiers


# ════════════════════════════════════════════
# V8 S1选股函数（16分5指标）
# ════════════════════════════════════════════
def screen_s1_v8(df, idx, sector_rank, stock_tier, mode_cfg):
    """V8 S1: 16分制, 5指标(缩量+波动收敛+板块排名+红绿交替+筹码结构)"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values.astype(float); ops = data['open'].values.astype(float)
    his = data['high'].values.astype(float); los = data['low'].values.astype(float)
    vols = data['volume'].values.astype(float); turns = data['turn'].values.astype(float)
    pcts = data['pctChg'].values.astype(float); amts = data['amount'].values.astype(float)
    n = len(data); last = cls[-1]
    code = df['code'].iloc[0] if 'code' in df.columns else ''

    # === 前置过滤 ===
    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None

    # F1: 流通市值30~300亿
    t_last = turns[-1] if turns[-1] > 0 else 0
    if t_last <= 0: return None
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: return None

    # F5: 现价>MA60
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None

    # F3: 60日涨幅10~60%
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None

    # F4: 回撤5~20%
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): return None

    # F2: 近60日有涨停
    if np.sum(pcts[-60:] >= 9.5) < 1: return None

    # === 排除项 (V8扩展) ===
    # X1: 近5日无单日跌>5%
    if np.any(pcts[-5:] < -5): return None
    # X3: 换手率异常
    if np.any(turns[-5:] > 8): return None
    # X4: MA60向下
    if n >= 65:
        ma60_prev = np.mean(cls[-65:-5])
        if ma60 < ma60_prev * 0.99: return None

    # X6(V8): 放量滞涨 - 近3日有1天量>5日均量×1.5但涨幅<1%
    vol5 = np.mean(vols[-5:])
    for di in range(3):
        if vols[-(di+1)] > vol5 * 1.5 and pcts[-(di+1)] < 1:
            return None

    # X7(V8): 缩量假反弹 - 近3日涨>3%但量逐日萎缩
    pct3_sum = np.sum(pcts[-3:])
    if pct3_sum > 3 and vols[-1] < vols[-2] < vols[-3]:
        return None

    # === 板块过滤 ===
    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5) if ind else 0.5
    mode = mode_cfg.get('mode', 'M2')
    # V8激进板块过滤
    cutoffs = {'M1': 0.50, 'M2': 0.50, 'M3': 0.60, 'M4': 1.0, 'M5': 1.0}
    cutoff = cutoffs.get(mode, 0.50)
    if sec_pct < (1 - cutoff) and ind:  # sec_pct低=弱板块
        return None

    # === 核心评分（V8·16分5指标）===
    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0; details = []

    # ① 缩量程度 (3分)
    vol20 = np.mean(vols[-20:]); vol60m = np.mean(vols[-60:])
    vr520 = vol5/vol20 if vol20 > 0 else 999
    vr560 = vol5/vol60m if vol60m > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:]) * 1.2
    sc1 = sum([0.4 <= vr520 <= 0.8, vr560 <= 0.7, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 4: score += 3; details.append("①缩量3")
    elif sc1 >= 3: score += 2; details.append("①缩量2")
    elif sc1 >= 1: score += 1; details.append("①缩量1")
    else: details.append("①缩量0")

    # ② 波动收敛 (5分) — V8合并旧②③④
    # ATR计算
    tr_arr = np.maximum(his[-20:]-los[-20:],
                        np.maximum(np.abs(his[-20:]-cls[-21:-1]) if n > 20 else his[-20:]-los[-20:],
                                   np.abs(los[-20:]-cls[-21:-1]) if n > 20 else his[-20:]-los[-20:]))
    atr5 = np.mean(tr_arr[-5:]) if len(tr_arr) >= 5 else 999
    atr20 = np.mean(tr_arr) if len(tr_arr) >= 20 else 999
    atr_ratio = atr5 / atr20 if atr20 > 0 else 999

    c5 = cls[-5:]
    rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100  # 价格区间
    ma_sp = (max(ma5,ma10,ma20) - min(ma5,ma10,ma20)) / ((ma5+ma10+ma20)/3) * 100  # 均线间距
    cs = abs((np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:])) * 100  # 重心偏移
    support_held = np.min(cls[-5:]) > ma60 or np.min(cls[-5:]) > np.min(los[-20:-5])  # 支撑守住

    sc2 = sum([atr_ratio <= 0.6, rng5 <= 5, ma_sp <= 3, cs <= 1, support_held])
    if sc2 >= 5: score += 5; details.append("②收敛5")
    elif sc2 >= 4: score += 4; details.append("②收敛4")
    elif sc2 >= 3: score += 3; details.append("②收敛3")
    elif sc2 >= 2: score += 1; details.append("②收敛1")
    else: details.append("②收敛0")

    # ③ 板块内排名 (3分) — V8新增
    tier_pct = stock_tier.get(code, 0.5)  # 0=最强,1=最弱
    if tier_pct <= 0.15:  # 龙头
        tier = '龙头'
    elif tier_pct <= 0.50:
        tier = '跟风'
    else:
        tier = '补涨'

    sec_top50 = sec_pct >= 0.5  # 板块前50%
    if tier == '龙头' and sec_top50:
        score += 3; details.append("③排名3(龙头)")
    elif tier == '跟风' and sec_top50:
        score += 2; details.append("③排名2(跟风)")
    elif tier == '龙头':
        score += 1; details.append("③排名1(龙头弱板)")
    elif tier == '跟风':
        score += 1; details.append("③排名1(跟风弱板)")
    else:
        details.append("③排名0(补涨)")

    # ④ 红绿交替 (2分)
    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    sc4 = sum([no3, pct5r, -2 <= pct5s <= 3])
    if sc4 >= 3: score += 2; details.append("④交替2")
    elif sc4 >= 2: score += 1; details.append("④交替1")
    else: details.append("④交替0")

    # ⑤ 筹码结构 (3分) — V8新增（用价格分布近似）
    # 近60日加权平均成本
    recent_vols = vols[-60:]
    recent_cls = cls[-60:]
    if np.sum(recent_vols) > 0:
        cost_center = np.average(recent_cls, weights=recent_vols)
    else:
        cost_center = np.mean(recent_cls)
    profit_ratio = np.sum(recent_cls < last) / len(recent_cls)  # 获利盘近似

    # 上方5%套牢区占比
    upper_zone = (last, last * 1.05)
    trapped_in_zone = np.sum((recent_cls >= upper_zone[0]) & (recent_cls <= upper_zone[1]))
    trapped_pct = trapped_in_zone / len(recent_cls)

    # 筹码集中度(90%成本区间宽度)
    p5, p95 = np.percentile(recent_cls, 5), np.percentile(recent_cls, 95)
    chip_conc = (p95 - p5) / ((p95 + p5) / 2)

    sc5_items = [profit_ratio >= 0.7, trapped_pct < 0.10, chip_conc <= 0.15]
    sc5 = sum(sc5_items)
    if sc5 >= 3: score += 3; details.append(f"⑤筹码3({profit_ratio:.0%})")
    elif sc5 >= 2: score += 2; details.append(f"⑤筹码2({profit_ratio:.0%})")
    elif sc5 >= 1: score += 1; details.append(f"⑤筹码1({profit_ratio:.0%})")
    else: details.append(f"⑤筹码0({profit_ratio:.0%})")

    # === 评级 ===
    grade = 'A' if score >= 13 else 'B' if score >= 11 else 'C'
    if grade in ('A', 'B'):
        return {
            'score': score, 'grade': grade, 'price': last,
            'strategy': 'S1', 'industry': ind, 'tier': tier,
            'vr520': vr520, 'dd60': dd60, 'details': ' '.join(details)
        }
    return None


# ════════════════════════════════════════════
# V6 S1选股（旧版20分7指标，作为对照）
# ════════════════════════════════════════════
def screen_s1_old(df, idx, sector_rank, stock_tier, mode_cfg):
    """旧V6 S1: 20分7指标(缩量+横盘+均线+实体+下影+十字+交替)"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values.astype(float); ops = data['open'].values.astype(float)
    his = data['high'].values.astype(float); los = data['low'].values.astype(float)
    vols = data['volume'].values.astype(float); turns = data['turn'].values.astype(float)
    pcts = data['pctChg'].values.astype(float); amts = data['amount'].values.astype(float)
    n = len(data); last = cls[-1]
    code = df['code'].iloc[0] if 'code' in df.columns else ''

    # 前置过滤（同V8）
    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    t_last = turns[-1] if turns[-1] > 0 else 0
    if t_last <= 0: return None
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: return None

    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): return None
    if np.sum(pcts[-60:] >= 9.5) < 1: return None
    if np.any(pcts[-5:] < -5): return None
    if np.any(turns[-5:] > 8): return None

    # 板块过滤（旧版淘汰后30%=准入前70%）
    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5) if ind else 0.5
    if sec_pct < 0.30 and ind: return None  # 旧版淘汰后30%

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    score = 0

    # ① 缩量 (3分, 量比0.4-0.8)
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60m = np.mean(vols[-60:])
    vr520 = vol5/vol20 if vol20 > 0 else 999; vr560 = vol5/vol60m if vol60m > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:]) * 1.2
    sc1 = sum([0.4<=vr520<=0.8, vr560<=0.7, turn5<=2, vol_dec, floor_vol])
    if sc1 >= 4: score += 3
    elif sc1 >= 3: score += 2
    elif sc1 >= 1: score += 1

    # ② 横盘 (4分)
    rng5 = (np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
    cs_v = (np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
    sc2 = sum([rng5<=5, abs(cs_v)<=1, last>ma60])
    if sc2>=3: score+=4
    elif sc2>=2: score+=2

    # ③ 均线粘合 (4分)
    ma_sp = (max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
    sc3 = sum([ma_sp<=3, last>ma60, ma5>ma10 or ma5/ma10>0.995])
    if sc3>=3: score+=4
    elif sc3>=2: score+=2

    # ④ 实体缩小 (3分)
    bodies = np.abs(cls-ops)
    br = np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
    amp3 = np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br<=0.5, amp3<=3, pct_abs5<=1.5])
    if sc4>=2: score+=3
    elif sc4>=1: score+=1

    # ⑤ 下影阳线 (2分)
    lsb = 0
    for i in range(-5, 0):
        body = abs(cls[i]-ops[i]); ls_len = min(ops[i],cls[i])-los[i]
        if cls[i]>ops[i] and body>0 and ls_len>=2*body and pcts[i]<=2:
            lsb += 1
    if lsb>=1: score+=2

    # ⑥ 十字星 (2分)
    doji = 0
    for i in range(-5, 0):
        body = abs(cls[i]-ops[i]); bp = body/ops[i]*100 if ops[i]>0 else 999
        shadow = max(his[i]-max(ops[i],cls[i]), min(ops[i],cls[i])-los[i])
        if bp<=0.5 and body>0 and shadow>=2*body:
            doji += 1
    if doji>=2: score+=2
    elif doji>=1: score+=1

    # ⑦ 红绿交替 (2分)
    colors = ['R' if cls[i]>=ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r = all(-2<=pcts[i]<=2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2<=pct5s<=3: score+=2

    grade = 'A' if score>=16 else 'B' if score>=15 else 'C'
    if grade in ('A','B'):
        return {
            'score': score, 'grade': grade, 'price': last,
            'strategy': 'S1', 'industry': ind, 'vr520': vr520, 'dd60': dd60,
            'details': f"旧S1:{score}/20"
        }
    return None


# ════════════════════════════════════════════
# S2选股（通用，S2评分没有V8变更）
# ════════════════════════════════════════════
def screen_s2(df, idx, sector_rank, stock_tier, mode_cfg):
    """S2 大阳后缩量横盘 (8分制)"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values.astype(float); ops = data['open'].values.astype(float)
    his = data['high'].values.astype(float); los = data['low'].values.astype(float)
    vols = data['volume'].values.astype(float); turns = data['turn'].values.astype(float)
    pcts = data['pctChg'].values.astype(float); amts = data['amount'].values.astype(float)
    n = len(data); last = cls[-1]
    code = df['code'].iloc[0] if 'code' in df.columns else ''

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    t_last = turns[-1] if turns[-1] > 0 else 0
    if t_last <= 0: return None
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: return None

    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None

    # 板块过滤
    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5) if ind else 0.5
    mode = mode_cfg.get('mode', 'M2')
    s2_cutoffs = {'M1': 1.0, 'M2': 0.60, 'M3': 0.70, 'M4': 0.70, 'M5': 1.0}
    cutoff = s2_cutoffs.get(mode, 0.60)
    if sec_pct < (1 - cutoff) and ind: return None

    # 找大阳线
    vol20 = np.mean(vols[-20:])
    big_candle_idx = None
    for di in range(1, min(6, n)):
        bi = n - di
        if bi < 1: break
        day_pct = (cls[bi]/cls[bi-1]-1)*100
        day_vr = vols[bi]/vol20 if vol20>0 else 0
        if day_pct>=4 and cls[bi]>ops[bi] and day_vr>=1.5:
            big_candle_idx = bi
            break
    if big_candle_idx is None: return None

    bc_close = cls[big_candle_idx]; bc_open = ops[big_candle_idx]
    bc_vol = vols[big_candle_idx]
    days_after = n - 1 - big_candle_idx
    if days_after < 1: return None

    # 缩量
    post_vols = vols[big_candle_idx+1:]
    vol_shrink = np.mean(post_vols) / bc_vol if bc_vol > 0 else 999
    if vol_shrink >= 0.7: return None

    # 价格守住
    if last < bc_open: return None

    # 排除项
    post_pcts = pcts[big_candle_idx+1:]
    if np.any(post_pcts < -3): return None
    if np.any(turns[-5:] > 8): return None

    # 评分(8分)
    score = 0; details = []
    if vol_shrink <= 0.5: score+=2; details.append("①缩量2")
    elif vol_shrink <= 0.7: score+=1; details.append("①缩量1")
    else: details.append("①缩量0")

    price_hold = last/bc_close if bc_close>0 else 0
    if price_hold >= 0.99: score+=2; details.append("②守住2")
    elif price_hold >= 0.97: score+=1; details.append("②守住1")
    else: details.append("②守住0")

    if sec_pct >= 0.7: score+=2; details.append("③板块2")
    elif sec_pct >= 0.5: score+=1; details.append("③板块1")
    else: details.append("③板块0")

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])
    if ma5>ma10>ma20: score+=2; details.append("④均线2")
    elif ma5>ma10: score+=1; details.append("④均线1")
    else: details.append("④均线0")

    grade = 'A' if score>=7 else ('B' if score>=6 else 'C')
    if grade in ('A','B'):
        return {
            'score': score, 'grade': grade, 'price': last,
            'strategy': 'S2', 'industry': ind, 'vr520': vol_shrink,
            'dd60': 0, 'details': ' '.join(details)
        }
    return None


# ════════════════════════════════════════════
# S3选股
# ════════════════════════════════════════════
def screen_s3(df, idx, sector_rank, stock_tier, mode_cfg):
    """S3 放量突破新高 (6分制)"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values.astype(float); ops = data['open'].values.astype(float)
    his = data['high'].values.astype(float); los = data['low'].values.astype(float)
    vols = data['volume'].values.astype(float); turns = data['turn'].values.astype(float)
    pcts = data['pctChg'].values.astype(float); amts = data['amount'].values.astype(float)
    n = len(data); last = cls[-1]
    code = df['code'].iloc[0] if 'code' in df.columns else ''

    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    t_last = turns[-1] if turns[-1] > 0 else 0
    if t_last <= 0: return None
    float_mcap = (amts[-1] / (last * 100)) / (t_last / 100) * 100 * last / 1e8
    if float_mcap < 30 or float_mcap > 300: return None

    if n < 21: return None
    high20 = np.max(his[-21:-1])
    if last <= high20: return None
    brk_pct = (last/high20 - 1) * 100

    vol20 = np.mean(vols[-20:])
    vol_ratio = vols[-1]/vol20 if vol20>0 else 0
    if vol_ratio < 1.5: return None

    if last <= ops[-1]: return None

    ma20 = np.mean(cls[-20:]); ma60 = np.mean(cls[-60:])
    if not (last > ma20 > ma60): return None

    # 板块过滤
    ind = industry_map.get(code, '')
    sec_pct = sector_rank.get(ind, 0.5) if ind else 0.5
    mode = mode_cfg.get('mode', 'M2')
    s3_cutoffs = {'M1': 1.0, 'M2': 1.0, 'M3': 0.70, 'M4': 0.75, 'M5': 1.0}
    cutoff = s3_cutoffs.get(mode, 0.70)
    if sec_pct < (1-cutoff) and ind: return None

    if pcts[-1] > 7: return None
    chg5 = (cls[-1]/cls[-6]-1)*100 if n>=6 else 0
    if chg5 > 20: return None
    if turns[-1] > 10: return None

    score=0; details=[]
    if brk_pct > 3: score+=2; details.append(f"①突破2({brk_pct:+.1f}%)")
    elif brk_pct > 1: score+=1; details.append(f"①突破1({brk_pct:+.1f}%)")
    else: details.append(f"①突破0({brk_pct:+.1f}%)")

    if vol_ratio > 2.5: score+=2; details.append(f"②放量2({vol_ratio:.1f}x)")
    elif vol_ratio > 1.5: score+=1; details.append(f"②放量1({vol_ratio:.1f}x)")
    else: details.append(f"②放量0({vol_ratio:.1f}x)")

    if sec_pct >= 0.7: score+=2; details.append("③板块2")
    elif sec_pct >= 0.5: score+=1; details.append("③板块1")
    else: details.append("③板块0")

    grade = 'A' if score>=5 else ('B' if score>=4 else 'C')
    if grade in ('A','B'):
        return {
            'score': score, 'grade': grade, 'price': last,
            'strategy': 'S3', 'industry': ind, 'vr520': vol_ratio,
            'dd60': 0, 'details': ' '.join(details)
        }
    return None


# ════════════════════════════════════════════
# 交易模拟引擎（V8规则）
# ════════════════════════════════════════════
def sim_trades_v8(screen_fns, max_per_day=2, use_sector=True, v8_tp_sl=True, label=""):
    """
    screen_fns: list of (name, screen_func) — 多策略同时运行
    v8_tp_sl: True=V8统一止盈(+4%半仓+移动2.5%), False=旧版(+3%/+5%)
    """
    trades = []

    for di, scan_date in enumerate(bt_dates):
        future = [d for d in bt_dates if d > scan_date]
        if len(future) < 3:
            continue
        buy_d, hold_d, exit_d = future[0], future[1], future[2]

        # 计算当日市场环境
        mkt = _compute_market_state(scan_date)
        sec_rank = _compute_sector_rank(scan_date)
        stk_tier = _compute_stock_tier(scan_date)

        selected = []
        seen_industry = set()

        for strat_name, screen_fn in screen_fns:
            for code, df in stock_dict.items():
                dl = df['date'].values.tolist()
                if scan_date not in dl:
                    continue
                idx = dl.index(scan_date)
                r = screen_fn(df, idx, sec_rank, stk_tier, mkt)
                if r:
                    r['code'] = code
                    selected.append(r)

        # 同行业去重：只保留最高分
        dedup = []
        ind_seen = {}
        for s in sorted(selected, key=lambda x: -x['score']):
            ind = s.get('industry', '')
            if ind and ind in ind_seen:
                continue
            if ind:
                ind_seen[ind] = True
            dedup.append(s)

        # 排序：A级优先 → 板块强 → 分数高
        dedup.sort(key=lambda x: (0 if x['grade']=='A' else 1, -x['score']))
        dedup = dedup[:max_per_day]

        for s in dedup:
            code = s['code']
            df = stock_dict[code]
            dl = df['date'].values.tolist()
            if not all(d in dl for d in [buy_d, hold_d, exit_d]):
                continue

            bi = dl.index(buy_d); hi = dl.index(hold_d); ei = dl.index(exit_d)
            bp = float(df.iloc[bi]['open'])
            if bp <= 0 or np.isnan(bp):
                continue

            d1h = float(df.iloc[bi]['high']); d1l = float(df.iloc[bi]['low']); d1c = float(df.iloc[bi]['close'])
            d2h = float(df.iloc[hi]['high']); d2l = float(df.iloc[hi]['low']); d2c = float(df.iloc[hi]['close'])
            d3o = float(df.iloc[ei]['open'])

            rem = 1.0; ret = 0.0; reason = ""

            if v8_tp_sl:
                # V8: +4%半仓, 移动2.5%, 硬止损-3%, 软止损收盘-1.5%
                tp1 = bp * 1.04
                hard_sl = bp * 0.97
                soft_sl = bp * 0.985

                # D1
                if d1l <= hard_sl:
                    ret = -3.0; rem = 0; reason = "D1硬止损-3%"
                else:
                    if d1h >= tp1:
                        # 卖50% at +4%
                        ret += 0.5 * 4.0; rem = 0.5
                        # 移动止盈：剩余部分看D1收盘vs高点
                        trailing_stop = d1h * 0.975
                        if d1c <= trailing_stop:
                            d1_ret_rem = (d1c - bp) / bp * 100
                            ret += rem * d1_ret_rem; rem = 0
                            reason = "D1+4%半仓+移动止盈清仓"
                        else:
                            reason = "D1+4%半仓"
                    # 软止损检查
                    if rem > 0 and d1c < soft_sl:
                        d1_ret = (d1c - bp) / bp * 100
                        ret += rem * d1_ret; rem = 0
                        reason = (reason + "+D1软止损" if reason else f"D1软止损{d1_ret:.1f}%")

                # D2
                if rem > 0:
                    if d2l <= hard_sl:
                        ret += rem * (-3.0); rem = 0
                        reason = (reason + "+D2硬止损" if reason else "D2硬止损-3%")
                    else:
                        if d2h >= tp1 and rem == 1.0:
                            ret += 0.5 * 4.0; rem = 0.5; reason = "D2+4%半仓"
                        # 移动止盈 (用D1高点和D2高点的最高)
                        if rem > 0 and rem < 1.0:
                            peak = max(d1h, d2h)
                            trailing = peak * 0.975
                            if d2c <= trailing:
                                d2_ret = (d2c - bp) / bp * 100
                                ret += rem * d2_ret; rem = 0
                                reason = (reason + "+D2移动止盈" if reason else "D2移动止盈")
                        if rem > 0 and d2c < soft_sl:
                            d2_ret = (d2c - bp)/bp*100
                            ret += rem * d2_ret; rem = 0
                            reason = (reason + "+D2软止损" if reason else f"D2软止损{d2_ret:.1f}%")

                # D3 强制清仓
                if rem > 0:
                    d3r = (d3o - bp)/bp*100
                    ret += rem * d3r; rem = 0
                    reason = (reason + "+D3清仓" if reason else "D3强制清仓")
            else:
                # 旧版: +3%半仓, +5%全仓, 盘中-2%止损
                tp1_old = bp * 1.03; tp2_old = bp * 1.05
                sl_old = bp * 0.98

                # D1
                if d1l <= sl_old:
                    ret = -2.0; rem = 0; reason = "D1止损-2%"
                else:
                    if d1h >= tp2_old:
                        ret = 0.5*3.0 + 0.5*5.0; rem = 0; reason = "D1止盈+5%"
                    elif d1h >= tp1_old:
                        ret = 0.5*3.0; rem = 0.5; reason = "D1+3%半仓"

                # D2
                if rem > 0:
                    if d2l <= sl_old:
                        ret += rem*(-2.0); rem = 0
                        reason = ("D2止损" if not reason else reason+"+D2止损")
                    else:
                        if d2h >= tp2_old:
                            if rem==1.0: ret+=0.5*3.0+0.5*5.0
                            else: ret+=rem*5.0
                            rem = 0
                            reason = ("D2止盈+5%" if not reason else reason+"+D2止盈")
                        elif d2h >= tp1_old and rem==1.0:
                            ret+=0.5*3.0; rem=0.5; reason="D2+3%半仓"

                # D3
                if rem > 0:
                    d3r = (d3o-bp)/bp*100
                    ret += rem*d3r
                    reason = (reason+"+D3清仓" if reason else "D3强制清仓")

            # 模式仓位缩放
            strat = s.get('strategy', 'S1')
            mode = mkt.get('mode', 'M2')
            pos_weight = 1.0
            # V8: 弱势策略×0.3
            weak = {
                'M1': ['S2','S3'], 'M2': ['S3'],
                'M3': ['S1'], 'M4': ['S1'],
            }
            if strat in weak.get(mode, []):
                pos_weight = 0.3

            trades.append({
                'scan_date': scan_date, 'buy_date': buy_d, 'code': code,
                'grade': s['grade'], 'score': s['score'],
                'strategy': strat, 'industry': s.get('industry',''),
                'return_pct': ret, 'weighted_return': ret * pos_weight,
                'exit_reason': reason, 'pos_weight': pos_weight,
                'mode': mode, 'details': s.get('details',''),
            })

    return trades


# ════════════════════════════════════════════
# 统计函数
# ════════════════════════════════════════════
def calc_stats(trades, use_weighted=False):
    if not trades:
        return None
    rets = [t['weighted_return' if use_weighted else 'return_pct'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    avg_w = np.mean(wins) if wins else 0
    avg_l = np.mean(losses) if losses else 0
    wr = len(wins)/len(rets)*100
    pf = sum(wins)/abs(sum(losses)) if losses and sum(losses)!=0 else 999
    ratio = abs(avg_w/avg_l) if avg_l != 0 else 999
    cum = 0; peak = 0; max_dd = 0
    sorted_t = sorted(trades, key=lambda x: x['buy_date'])
    for t in sorted_t:
        cum += t['weighted_return' if use_weighted else 'return_pct']
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return {
        'n': len(trades), 'wr': wr, 'total': sum(rets),
        'avg': np.mean(rets), 'avg_w': avg_w, 'avg_l': avg_l,
        'ratio': ratio, 'pf': pf, 'max_dd': max_dd,
    }


# ════════════════════════════════════════════
# 运行回测
# ════════════════════════════════════════════
print("\n" + "=" * 80)
print("  运行回测 (可能需要几分钟...)")
print("=" * 80)

# 方案1: V8全策略 (S1_V8 + S2 + S3, V8止盈止损)
print("\n  [1/3] V8全策略 (S1_V8+S2+S3, 统一止盈+4%/移动2.5%)...")
trades_v8 = sim_trades_v8(
    [('S1_V8', screen_s1_v8), ('S2', screen_s2), ('S3', screen_s3)],
    max_per_day=2, v8_tp_sl=True, label="V8全策略"
)
print(f"    → {len(trades_v8)} 笔交易")

# 方案2: 旧V6规则 (S1_old + S2 + S3, 旧止盈止损)
print("  [2/3] 旧V6规则 (S1_old+S2+S3, +3%/+5%止盈)...")
trades_old = sim_trades_v8(
    [('S1_old', screen_s1_old), ('S2', screen_s2), ('S3', screen_s3)],
    max_per_day=2, v8_tp_sl=False, label="旧V6规则"
)
print(f"    → {len(trades_old)} 笔交易")

# 方案3: V8 S1单策略 (只看S1 V8, V8止盈)
print("  [3/3] V8仅S1 (S1_V8, 统一止盈)...")
trades_v8_s1 = sim_trades_v8(
    [('S1_V8', screen_s1_v8)],
    max_per_day=2, v8_tp_sl=True, label="V8仅S1"
)
print(f"    → {len(trades_v8_s1)} 笔交易")

# ════════════════════════════════════════════
# 输出对比
# ════════════════════════════════════════════
print()
print("=" * 80)
print("  核心指标对比（近两周 2026-04-03 ~ 2026-04-20）")
print("=" * 80)
print()

configs = [
    ("V8全策略(S1V8+S2+S3,+4%/移动2.5%)", trades_v8, False),
    ("V8全策略(仓位加权)", trades_v8, True),
    ("旧V6规则(S1旧+S2+S3,+3%/+5%)", trades_old, False),
    ("V8仅S1策略", trades_v8_s1, False),
]

header = f"  {'方案':<38s} {'笔数':>4s} {'胜率':>6s} {'总收益':>8s} {'均收益':>7s} {'盈亏比':>6s} {'利润因子':>8s} {'最大回撤':>8s}"
print(header)
print("  " + "─" * 95)

for label, trades, weighted in configs:
    s = calc_stats(trades, use_weighted=weighted)
    if s:
        print(f"  {label:<38s} {s['n']:>4d}  {s['wr']:>5.1f}%  {s['total']:>+7.2f}%  {s['avg']:>+6.3f}%  {s['ratio']:>5.2f}  {s['pf']:>8.2f}  {s['max_dd']:>+7.2f}%")
    else:
        print(f"  {label:<38s}    0笔")

# ════════════════════════════════════════════
# V8详细分析
# ════════════════════════════════════════════
if trades_v8:
    print()
    print("=" * 80)
    print("  V8全策略·详细分析")
    print("=" * 80)

    # 按策略
    print("\n  ── 按策略 ──")
    for strat in ['S1', 'S2', 'S3']:
        st = [t for t in trades_v8 if t['strategy'] == strat]
        if st:
            s = calc_stats(st)
            print(f"    {strat}: {s['n']:>3d}笔  胜率{s['wr']:>5.1f}%  总收益{s['total']:>+7.2f}%  均{s['avg']:>+6.3f}%  盈亏比{s['ratio']:.2f}")

    # 按评级
    print("\n  ── 按评级 ──")
    for g in ['A', 'B']:
        gt = [t for t in trades_v8 if t['grade'] == g]
        if gt:
            s = calc_stats(gt)
            print(f"    {g}级: {s['n']:>3d}笔  胜率{s['wr']:>5.1f}%  总收益{s['total']:>+7.2f}%  盈亏比{s['ratio']:.2f}")

    # 按市场模式
    print("\n  ── 按市场模式 ──")
    for m in ['M1','M2','M3','M4','M5']:
        mt = [t for t in trades_v8 if t['mode'] == m]
        if mt:
            s = calc_stats(mt)
            print(f"    {m}: {s['n']:>3d}笔  胜率{s['wr']:>5.1f}%  总收益{s['total']:>+7.2f}%")

    # 按出场方式
    print("\n  ── 按出场方式 ──")
    exit_map = {}
    for t in trades_v8:
        k = t['exit_reason']
        exit_map.setdefault(k, []).append(t['return_pct'])
    for reason, rets in sorted(exit_map.items(), key=lambda x: -len(x[1])):
        wr = len([r for r in rets if r>0])/len(rets)*100
        print(f"    {reason:<36s}: {len(rets):>3d}笔  胜率{wr:>5.1f}%  总{sum(rets):>+7.2f}%  均{np.mean(rets):>+6.3f}%")

    # 逐日列表
    print("\n  ── 按日期 ──")
    dates = sorted(set(t['scan_date'] for t in trades_v8))
    cum = 0
    for d in dates:
        dt = [t for t in trades_v8 if t['scan_date'] == d]
        day_r = sum(t['return_pct'] for t in dt)
        cum += day_r
        mkt = _compute_market_state(d)
        print(f"    {d} [{mkt['mode']}] {len(dt)}笔  日收{day_r:>+6.2f}%  累计{cum:>+7.2f}%")

    # 每笔交易明细
    print()
    print("=" * 80)
    print("  V8 每笔交易明细")
    print("=" * 80)
    print(f"\n  {'选股日':>10s} {'买入日':>10s} {'代码':<12s} {'策略':>3s} {'级':>2s} {'分':>3s} {'权重':>4s} {'收益':>7s} {'加权':>7s} {'模式':>3s}  出场原因")
    print("  " + "─" * 110)
    for t in sorted(trades_v8, key=lambda x: x['scan_date']):
        w_tag = f"{t['pos_weight']:.1f}" if t['pos_weight'] < 1 else "1.0"
        print(f"  {t['scan_date']:>10s} {t['buy_date']:>10s} {t['code']:<12s} {t['strategy']:>3s} {t['grade']:>2s} {t['score']:>3d} {w_tag:>4s} {t['return_pct']:>+6.2f}% {t['weighted_return']:>+6.2f}% {t['mode']:>3s}  {t['exit_reason']}")

    # 行业统计
    print("\n  ── 行业分布 ──")
    ind_map = defaultdict(list)
    for t in trades_v8:
        ind_map[t['industry'] or '未知'].append(t['return_pct'])
    for ind, rets in sorted(ind_map.items(), key=lambda x: -sum(x[1])):
        wr = len([r for r in rets if r>0])/len(rets)*100 if rets else 0
        print(f"    {ind:<12s}: {len(rets)}笔  胜率{wr:.0f}%  总{sum(rets):+.2f}%")

# ════════════════════════════════════════════
# V8 vs 旧V6 差异汇总
# ════════════════════════════════════════════
print()
print("=" * 80)
print("  V8 vs 旧V6 改进效果总结")
print("=" * 80)

s_v8 = calc_stats(trades_v8) if trades_v8 else None
s_old = calc_stats(trades_old) if trades_old else None

if s_v8 and s_old:
    print(f"\n  交易笔数:  V8={s_v8['n']}笔  旧={s_old['n']}笔  差{s_v8['n']-s_old['n']:+d}")
    print(f"  胜    率:  V8={s_v8['wr']:.1f}%  旧={s_old['wr']:.1f}%  差{s_v8['wr']-s_old['wr']:+.1f}%")
    print(f"  总 收 益:  V8={s_v8['total']:+.2f}%  旧={s_old['total']:+.2f}%  差{s_v8['total']-s_old['total']:+.2f}%")
    print(f"  均笔收益:  V8={s_v8['avg']:+.3f}%  旧={s_old['avg']:+.3f}%  差{s_v8['avg']-s_old['avg']:+.3f}%")
    print(f"  盈 亏 比:  V8={s_v8['ratio']:.2f}  旧={s_old['ratio']:.2f}")
    print(f"  利润因子:  V8={s_v8['pf']:.2f}  旧={s_old['pf']:.2f}")
    print(f"  最大回撤:  V8={s_v8['max_dd']:+.2f}%  旧={s_old['max_dd']:+.2f}%")

    # 综合评价
    print("\n  ── 改进评价 ──")
    better = 0
    if s_v8['wr'] > s_old['wr']: better += 1; print("  ✅ 胜率提升")
    else: print("  ❌ 胜率下降")
    if s_v8['total'] > s_old['total']: better += 1; print("  ✅ 总收益提升")
    else: print("  ❌ 总收益下降")
    if s_v8['pf'] > s_old['pf']: better += 1; print("  ✅ 利润因子提升")
    else: print("  ❌ 利润因子下降")
    if abs(s_v8['max_dd']) < abs(s_old['max_dd']): better += 1; print("  ✅ 回撤减小")
    else: print("  ❌ 回撤增大")
    print(f"\n  综合: {better}/4 项改进  {'👍V8优于旧版' if better >= 3 else '⚠️需要更长时间验证' if better >= 2 else '❌V8效果不佳'}")
elif s_v8:
    print("\n  旧规则无交易产生，无法对比")
elif s_old:
    print("\n  V8规则无交易产生，可能过滤太严")
else:
    print("\n  两个版本均无交易！数据可能不在回测范围内")

print("\n" + "=" * 80)
print("  回测完成!")
print("=" * 80)
