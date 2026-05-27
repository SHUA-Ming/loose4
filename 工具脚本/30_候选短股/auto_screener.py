#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场自动选股器
每天收盘后运行，扫描沪深主板全部股票，输出：
  1. 蓄力候选：洗盘到位、即将突破的票
  2. 突破候选：刚放量突破、可能是启动信号的票

基于《选股参数手册》的评分体系，自动打分排名。
"""

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
import warnings
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import read_kline, upsert_kline, get_last_date, get_all_codes, get_industry_map, get_connection

# 抑制numpy空切片警告（某些停牌/数据不全的股票会触发，无害）
warnings.filterwarnings('ignore', category=RuntimeWarning)

# ═══════════════════════════════════════════════════════════════
# 配置区
# ═══════════════════════════════════════════════════════════════

DAYS = 120              # 拉取天数
MIN_TRADE_DAYS = 60     # 至少需要60个交易日数据
TOP_N = 20              # 每个榜单输出前N只

# 排除条件
EXCLUDE_ST = True                   # 排除ST/*ST
MIN_DAILY_AMOUNT = 1000             # 日均成交额下限（万元）
MIN_PRICE = 3.0                     # 最低价格
MAX_PRICE = 200.0                   # 最高价格（排除贵州茅台等）

# 数据库缓存（替代原CSV文件，首次全量拉取后只补增量）
FORCE_REFRESH = '--force' in sys.argv  # 加 --force 参数可强制全量刷新
SINGLE_STOCK_MIN_POSITION = 0.5

# 缓存统计
_cache_hits = 0
_cache_fetches = 0


def get_all_stocks():
    """获取沪深主板全部股票代码"""
    # 用最近交易日获取全部证券列表
    # 尝试今天和前几天，因为baostock可能还没更新当天数据
    for offset in range(0, 5):
        day = (datetime.now() - timedelta(days=offset)).strftime('%Y-%m-%d')
        rs = bs.query_all_stock(day=day)
        stocks = []
        while rs.next():
            row = rs.get_row_data()
            code = row[0]       # sh.600000
            name = row[2]       # 上证综合指数
            status = row[1]     # 1=上市
            if status != '1':
                continue
            # 只要沪深主板个股（排除指数、基金、债券等）
            if not (code.startswith('sh.60') or code.startswith('sz.00')):
                continue
            if EXCLUDE_ST and ('ST' in name or 'st' in name):
                continue
            stocks.append((code, name))
        if stocks:
            print(f"  使用日期 {day} 的股票列表")
            return stocks
    return []


def fetch_market_snapshot(end_date):
    """大盘快照：从 DB 读取指数最近收盘数据"""
    indexes = {
        'sh.000001': '上证',
        'sz.399001': '深成',
        'sz.399006': '创业板',
    }
    values = {}
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')

    for code, name in indexes.items():
        df = read_kline(code, start_date=start_date, end_date=end_date)
        if df.empty:
            values[name] = {'pct': 0.0, 'close': 0.0}
            continue
        for col in ['close', 'pctChg']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        last = df.dropna(subset=['close', 'pctChg']).iloc[-1]
        values[name] = {'pct': float(last['pctChg']), 'close': float(last['close'])}

    sh_pct = values['上证']['pct']
    sz_pct = values['深成']['pct']
    cyb_pct = values['创业板']['pct']

    if sh_pct <= -2:
        state = '恐慌'
        action = '只卖不买'
        max_new_positions = 0
    elif sh_pct <= -1:
        state = '偏弱'
        action = '轻仓试错'
        max_new_positions = 1
    elif sh_pct >= 1:
        state = '偏强'
        action = '可正常操作'
        max_new_positions = 2
    else:
        state = '中性'
        action = '精选个股'
        max_new_positions = 1

    return {
        'sh_pct': sh_pct,
        'sz_pct': sz_pct,
        'cyb_pct': cyb_pct,
        'state': state,
        'action': action,
        'max_new_positions': max_new_positions,
    }


def build_industry_map():
    """从DB获取行业映射"""
    try:
        raw = get_industry_map()  # {bs_code: industry}
        mapping = {}
        for bs_code, industry in raw.items():
            code_raw = bs_code.replace('sh.', '').replace('sz.', '')
            if industry:
                mapping[code_raw] = industry
        return mapping
    except Exception:
        return {}


def get_sector_snapshot(sector_pool, industry_map):
    """按行业统计热度，并返回行业强度映射"""
    bucket = {}
    for item in sector_pool:
        industry = industry_map.get(item['code'])
        if not industry:
            continue
        if industry not in bucket:
            bucket[industry] = {'count': 0, 'up': 0, 'sum_pct': 0.0}
        bucket[industry]['count'] += 1
        bucket[industry]['sum_pct'] += item['pct']
        if item['pct'] > 0:
            bucket[industry]['up'] += 1

    rows = []
    for industry, data in bucket.items():
        if data['count'] < 5:
            continue
        avg_pct = data['sum_pct'] / data['count']
        up_ratio = data['up'] / data['count']
        rows.append((industry, data['count'], avg_pct, up_ratio))

    rows.sort(key=lambda x: (x[2], x[3]), reverse=True)
    strength_map = {
        industry: {'count': count, 'avg_pct': avg_pct, 'up_ratio': up_ratio, 'rank': idx + 1}
        for idx, (industry, count, avg_pct, up_ratio) in enumerate(rows)
    }
    return rows[:10], strength_map


def fetch_kline(code, start_date, end_date):
    """从DB获取单只股票K线数据（DB由 _update_cache_ak.py 维护）"""
    global _cache_hits, _cache_fetches
    _cache_hits += 1
    window = read_kline(code, start_date=start_date, end_date=end_date)
    if window.empty:
        return None
    return window if len(window) >= MIN_TRADE_DAYS else None


def compute_indicators(df):
    """计算所有技术指标"""
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    volumes = df['volume'].values
    turns = df['turn'].values
    pcts = df['pctChg'].values
    amounts = df['amount'].values

    n = len(df)
    last = closes[-1]

    # 均线
    ma5 = np.mean(closes[-5:])
    ma10 = np.mean(closes[-10:])
    ma20 = np.mean(closes[-20:]) if n >= 20 else np.mean(closes)
    ma60 = np.mean(closes[-60:]) if n >= 60 else np.mean(closes)

    # 量均线
    vol5 = np.mean(volumes[-5:])
    vol10 = np.mean(volumes[-10:])
    vol20 = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes)
    vol60 = np.mean(volumes[-60:]) if n >= 60 else np.mean(volumes)

    # 换手率
    turn5 = np.mean(turns[-5:])
    turn20 = np.mean(turns[-20:]) if n >= 20 else np.mean(turns)

    # 日均成交额（万）
    avg_amount_20 = np.mean(amounts[-20:]) / 10000 if n >= 20 else np.mean(amounts) / 10000

    # 量比
    vol_ratio_5_20 = vol5 / vol20 if vol20 > 0 else 999
    vol_ratio_5_60 = vol5 / vol60 if vol60 > 0 else 999

    # 近60日数据
    c60 = closes[-60:] if n >= 60 else closes
    p60 = pcts[-60:] if n >= 60 else pcts
    max60 = np.max(c60)
    max60_idx = np.argmax(c60)
    drawdown60 = (last - max60) / max60 * 100
    pct60 = (last - c60[0]) / c60[0] * 100

    # 涨停次数（近60日）
    limit_up_count = np.sum(np.array(p60) >= 9.5)

    # 近5日波动
    c5 = closes[-5:]
    hi5 = np.max(c5)
    lo5 = np.min(c5)
    avg5 = np.mean(c5)
    range5 = (hi5 - lo5) / avg5 * 100 if avg5 > 0 else 999

    # 重心偏移
    avg5_price = np.mean(closes[-5:])
    avg10_price = np.mean(closes[-10:])
    center_shift = (avg5_price - avg10_price) / avg10_price * 100 if avg10_price > 0 else 0

    # 均线间距
    ma_max = max(ma5, ma10, ma20)
    ma_min = min(ma5, ma10, ma20)
    ma_avg = (ma5 + ma10 + ma20) / 3
    ma_spread = (ma_max - ma_min) / ma_avg * 100 if ma_avg > 0 else 999

    # 连续缩量（近3日逐日递减）
    vol_declining = (volumes[-1] < volumes[-2] < volumes[-3]) if n >= 3 else False

    # 地量信号（当日量接近60日最低）
    vol_min60 = np.min(volumes[-60:]) if n >= 60 else np.min(volumes)
    is_floor_vol = volumes[-1] <= vol_min60 * 1.2

    # K线实体缩小
    bodies = np.abs(closes - opens)
    body5 = np.mean(bodies[-5:])
    body20 = np.mean(bodies[-20:]) if n >= 20 else np.mean(bodies)
    body_ratio = body5 / body20 if body20 > 0 else 999

    # 近3日最大振幅
    amp3 = np.max((highs[-3:] - lows[-3:]) / closes[-4:-1] * 100) if n >= 4 else 999

    # 近5日涨跌幅绝对值均值
    pct_abs5 = np.mean(np.abs(pcts[-5:]))

    # 下影线阳线（近5日）
    lower_shadow_bull = 0
    for i in range(-5, 0):
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        body = abs(c - o)
        lower_shadow = min(o, c) - l
        if c > o and body > 0 and lower_shadow >= 2 * body and pcts[i] <= 2:
            lower_shadow_bull += 1

    # 小十字星（近5日）
    doji_count = 0
    for i in range(-5, 0):
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        body = abs(c - o)
        body_pct = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if body_pct <= 0.5 and body > 0 and shadow >= 2 * body:
            doji_count += 1

    # 红绿交替（近5日）
    colors5 = ['R' if closes[i] >= opens[i] else 'G' for i in range(-5, 0)]
    no_3_consecutive = True
    for i in range(len(colors5) - 2):
        if colors5[i] == colors5[i+1] == colors5[i+2]:
            no_3_consecutive = False
    pct5_range = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5_sum = np.sum(pcts[-5:])
    rg_alternate = no_3_consecutive and pct5_range and -2 <= pct5_sum <= 3

    # 近5日有无单日跌幅>5%（排除项X1）
    has_big_drop_5d = np.any(pcts[-5:] < -5)

    # 近20日连续下跌超过10天（排除项X2）
    consecutive_down = 0
    max_consecutive_down = 0
    for p in pcts[-20:]:
        if p < 0:
            consecutive_down += 1
            max_consecutive_down = max(max_consecutive_down, consecutive_down)
        else:
            consecutive_down = 0

    # 换手突然飙升>8%（排除项X3）
    has_high_turn = np.any(turns[-5:] > 8)

    # ── 突破检测（最近1-3天） ──
    # 放量突破：最近3天内成交量>=5日均量2倍 且 涨幅>3%
    breakout_signal = False
    breakout_day = None
    for i in [-1, -2, -3]:
        if n + i >= 0:
            if volumes[i] >= vol5 * 2 and pcts[i] > 3:
                breakout_signal = True
                breakout_day = df.iloc[i]['date']
                break

    # 突破横盘上沿
    if n >= 10:
        upper_range = np.max(closes[-15:-1])  # 前15日最高（不含今天）
        breakout_range = closes[-1] > upper_range and pcts[-1] > 0
    else:
        breakout_range = False

    # MA5金叉MA10（最近3天内发生）
    ma5_cross = False
    if n >= 12:
        for i in [-1, -2, -3]:
            prev_ma5 = np.mean(closes[i-5:i])
            prev_ma10 = np.mean(closes[i-10:i])
            curr_ma5 = np.mean(closes[i-4:i+1])
            curr_ma10 = np.mean(closes[i-9:i+1])
            if prev_ma5 <= prev_ma10 and curr_ma5 > curr_ma10:
                ma5_cross = True
                break

    return {
        'last': last,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'vol5': vol5, 'vol20': vol20, 'vol60': vol60,
        'turn5': turn5, 'turn20': turn20,
        'avg_amount_20': avg_amount_20,
        'vol_ratio_5_20': vol_ratio_5_20,
        'vol_ratio_5_60': vol_ratio_5_60,
        'vol_declining': vol_declining,
        'is_floor_vol': is_floor_vol,
        'max60': max60, 'drawdown60': drawdown60, 'pct60': pct60,
        'limit_up_count': limit_up_count,
        'range5': range5,
        'center_shift': center_shift,
        'ma_spread': ma_spread,
        'body_ratio': body_ratio,
        'amp3': amp3,
        'pct_abs5': pct_abs5,
        'lower_shadow_bull': lower_shadow_bull,
        'doji_count': doji_count,
        'rg_alternate': rg_alternate,
        'has_big_drop_5d': has_big_drop_5d,
        'max_consecutive_down': max_consecutive_down,
        'has_high_turn': has_high_turn,
        'breakout_signal': breakout_signal,
        'breakout_day': breakout_day,
        'breakout_range': breakout_range,
        'ma5_cross': ma5_cross,
        # 用于输出
        'pct_today': pcts[-1],
        'turn_today': turns[-1],
    }


def check_pre_filter(ind):
    """第一关：前置过滤"""
    # F1: 日均成交额 > 1000万
    if ind['avg_amount_20'] < MIN_DAILY_AMOUNT:
        return False, 'F1:成交额不足'
    # F2: 近60日至少1次涨停
    if ind['limit_up_count'] < 1:
        return False, 'F2:无涨停'
    # F3: 近60日涨幅 10%~60%
    if not (10 <= ind['pct60'] <= 60):
        return False, f'F3:涨幅{ind["pct60"]:.1f}%'
    # F4: 从高点回撤 -5%~-20%（回测优化：浅回撤胜率更高）
    if not (-20 <= ind['drawdown60'] <= -5):
        return False, f'F4:回撤{ind["drawdown60"]:.1f}%'
    # F5: 现价 > MA60
    if ind['last'] <= ind['ma60']:
        return False, 'F5:低于MA60'
    # 价格范围
    if ind['last'] < MIN_PRICE or ind['last'] > MAX_PRICE:
        return False, 'F:价格范围'
    return True, 'OK'


def check_exclusions(ind):
    """排除项检查"""
    if ind['has_big_drop_5d']:
        return False, 'X1:近5日大跌>5%'
    if ind['max_consecutive_down'] >= 10:
        return False, 'X2:连续下跌>10天'
    if ind['has_high_turn']:
        return False, 'X3:换手飙升>8%'
    if ind['last'] < ind['ma60']:
        return False, 'X4:低于MA60'
    return True, 'OK'


def score_stock(ind):
    """第二关：7大指标评分（满分20分，V2终极版·2026-04消融实验优化）"""
    score = 0
    details = []

    # ① 缩量程度（3分）— V2终极版：组合优化后3分+收盘止损+浅回撤=最优
    s1 = 0
    sub_count = 0
    if 0.4 <= ind['vol_ratio_5_20'] <= 0.8:
        sub_count += 1
    if ind['vol_ratio_5_60'] <= 0.7:
        sub_count += 1
    if ind['turn5'] <= 2:
        sub_count += 1
    if ind['vol_declining']:
        sub_count += 1
    if ind['is_floor_vol']:
        sub_count += 1
    if sub_count >= 3:
        s1 = 3
        details.append('①缩量✅')
    elif sub_count >= 1:
        s1 = 1
        details.append('①缩量⚠️')
    else:
        details.append('①缩量❌')
    score += s1

    # ② 横盘整理（4分）
    s2 = 0
    sub_count = 0
    if ind['range5'] <= 5:
        sub_count += 1
    if abs(ind['center_shift']) <= 1:
        sub_count += 1
    if ind['last'] > ind['ma60']:
        sub_count += 1
    if sub_count >= 3:
        s2 = 4
        details.append('②横盘✅')
    elif sub_count >= 2:
        s2 = 2
        details.append('②横盘⚠️')
    else:
        details.append('②横盘❌')
    score += s2

    # ③ 均线粘合（4分）
    s3 = 0
    sub_count = 0
    if ind['ma_spread'] <= 3:
        sub_count += 1
    if ind['last'] > ind['ma60']:
        sub_count += 1
    if ind['ma5'] > ind['ma10'] or (ind['ma5'] / ind['ma10'] > 0.995):
        sub_count += 1
    if sub_count >= 3:
        s3 = 4
        details.append('③均线✅')
    elif sub_count >= 2:
        s3 = 2
        details.append('③均线⚠️')
    else:
        details.append('③均线❌')
    score += s3

    # ④ K线实体缩小（3分）
    s4 = 0
    sub_count = 0
    if ind['body_ratio'] <= 0.5:
        sub_count += 1
    if ind['amp3'] <= 3:
        sub_count += 1
    if ind['pct_abs5'] <= 1.5:
        sub_count += 1
    if sub_count >= 2:
        s4 = 3
        details.append('④实体✅')
    elif sub_count >= 1:
        s4 = 1
        details.append('④实体⚠️')
    else:
        details.append('④实体❌')
    score += s4

    # ⑤ 下影线阳线（2分）— V2终极版：降权，回测贡献偏弱
    s5 = 0
    if ind['lower_shadow_bull'] >= 1:
        s5 = 2
        details.append('⑤下影✅')
    else:
        details.append('⑤下影—')
    score += s5

    # ⑥ 小十字星（2分）
    s6 = 0
    if ind['doji_count'] >= 2:
        s6 = 2
        details.append('⑥十字✅')
    elif ind['doji_count'] >= 1:
        s6 = 1
        details.append('⑥十字⚠️')
    else:
        details.append('⑥十字—')
    score += s6

    # ⑦ 红绿交替（2分）
    s7 = 0
    if ind['rg_alternate']:
        s7 = 2
        details.append('⑦交替✅')
    else:
        details.append('⑦交替❌')
    score += s7

    # 等级（V2终极版：满分20分，B级≥10为最佳操作区间）
    if score >= 16:
        grade = 'A'
    elif score >= 10:
        grade = 'B'
    else:
        grade = 'C'

    return score, grade, details


def score_buy_priority(ind, score, grade, signals, sector_meta=None):
    """第二阶段：买入优先级评分（用于回答“优先买谁”）"""
    p = 0
    reasons = []

    # V3回测结论：A级赚钱主力，B级半仓（A满仓B半仓策略+0.53%/3月）
    if grade == 'A':
        p += 3
        reasons.append('A级优先')
    elif grade == 'B':
        if score >= 15:
            p += 1
            reasons.append('B15半仓')
        else:
            p -= 2
            reasons.append('B低分不入选')

    # V3：A级高分更优，低分B级扣分
    if score >= 18:
        p += 2
        reasons.append('A高分精选')
    elif score >= 16:
        p += 1
        reasons.append('A级标准')
    elif score == 15:
        pass  # B15不加不减
    elif score < 15:
        p -= 2
        reasons.append('V3淘汰线下')

    # 启动信号越多，优先级越高
    if signals:
        sig_score = min(6, len(signals) * 3)
        p += sig_score
        reasons.append('启动信号')

    # 当日强弱：弱于0不急着排前
    if 1 <= ind['pct_today'] <= 5:
        p += 2
        reasons.append('当日强势')
    elif 0 < ind['pct_today'] < 1 or 5 < ind['pct_today'] <= 7:
        p += 1
        reasons.append('当日偏强')
    elif ind['pct_today'] < 0:
        p -= 2
        reasons.append('当日走弱')

    # 换手处于可交易区间，加分
    turn_today = ind.get('turn_today', ind.get('turn5', 0))
    if 1 <= turn_today <= 6:
        p += 1
        reasons.append('换手健康')

    # 趋势确认：MA5>MA10>MA20更适合短波优先
    if ind['ma5'] > ind['ma10'] > ind['ma20']:
        p += 2
        reasons.append('均线多头')
    elif ind['ma5'] > ind['ma10']:
        p += 1
        reasons.append('短线转强')

    # 横盘重心不下移更稳
    if -0.5 <= ind['center_shift'] <= 1.0:
        p += 1
        reasons.append('重心稳定')

    # 量比太低难启动，太高易追高，取中间值
    vol_ratio = ind.get('vol_ratio_5_20', ind.get('vol_ratio', 999))
    if 0.7 <= vol_ratio <= 1.3:
        p += 2
        reasons.append('量能适中')
    elif 0.4 <= vol_ratio <= 1.8:
        p += 1

    # 深回撤尾端再扣一分，避免抄到继续下探
    if ind['drawdown60'] < -18:
        p -= 1
    elif -15 <= ind['drawdown60'] <= -5:
        p += 2
        reasons.append('浅回撤优先')
    elif -18 <= ind['drawdown60'] < -15:
        p += 1
        reasons.append('中回撤可做')

    # 板块联动：强板块内的个股优先级更高
    if sector_meta:
        if sector_meta['avg_pct'] >= 0.8 and sector_meta['up_ratio'] >= 0.6:
            p += 2
            reasons.append('板块联动')
        elif sector_meta['avg_pct'] > 0:
            p += 1
            reasons.append('板块偏强')

    if p >= 12:
        priority = '高'
    elif p >= 9:
        priority = '中'
    else:
        priority = '低'

    return p, priority, reasons


def has_breakout_signal(ind):
    """是否有启动/突破信号"""
    signals = []
    if ind['breakout_signal']:
        signals.append(f'放量突破({ind["breakout_day"]})')
    if ind['breakout_range']:
        signals.append('突破横盘上沿')
    if ind['ma5_cross']:
        signals.append('MA5金叉MA10')
    return signals


def main():
    print("═" * 80)
    print("  全市场自动选股器")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 80)

    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=DAYS + 30)).strftime('%Y-%m-%d')
    market = fetch_market_snapshot(end_date)
    print(f"大盘快照: 上证{market['sh_pct']:+.2f}% 深成{market['sz_pct']:+.2f}% 创业板{market['cyb_pct']:+.2f}%  状态:{market['state']}  建议:{market['action']}")

    # 获取股票列表
    print("正在获取沪深主板股票列表...")
    stock_list = get_all_stocks()
    industry_map = build_industry_map()
    total = len(stock_list)
    print(f"共 {total} 只股票待扫描")
    if FORCE_REFRESH:
        print("  ⚠️ 强制刷新模式：忽略缓存，重新拉取全部数据")
    else:
        print("  📦 使用数据库缓存（仅拉取增量数据，大幅加速）")
    print()

    # 两个候选池
    power_candidates = []   # 蓄力候选（高分洗盘股）
    breakout_candidates = []  # 突破候选（刚发出信号的）

    scanned = 0
    passed_pre = 0
    passed_excl = 0
    sector_pool = []

    for i, (code, name) in enumerate(stock_list):
        if (i + 1) % 100 == 0:
            print(f"  扫描进度: {i+1}/{total} ({(i+1)/total*100:.0f}%)  已发现蓄力{len(power_candidates)}只 突破{len(breakout_candidates)}只", flush=True)

        df = fetch_kline(code, start_date, end_date)
        if df is None:
            continue
        scanned += 1

        ind = compute_indicators(df)

        # 快速排除：成交额太小
        if ind['avg_amount_20'] < MIN_DAILY_AMOUNT:
            continue
        if ind['last'] < MIN_PRICE or ind['last'] > MAX_PRICE:
            continue
        sector_pool.append({'code': code, 'pct': ind['pct_today']})

        # 第一关：前置过滤
        ok, reason = check_pre_filter(ind)
        if not ok:
            continue
        passed_pre += 1

        # 排除项
        ok, reason = check_exclusions(ind)
        if not ok:
            continue
        passed_excl += 1

        # 第二关：评分
        score, grade, details = score_stock(ind)

        # 检查突破信号
        signals = has_breakout_signal(ind)

        entry = {
            'code': code,
            'name': name,
            'price': ind['last'],
            'pct_today': ind['pct_today'],
            'turn_today': ind['turn_today'],
            'score': score,
            'grade': grade,
            'details': details,
            'signals': signals,
            'vol_ratio': ind['vol_ratio_5_20'],
            'vol_ratio_5_20': ind['vol_ratio_5_20'],
            'turn5': ind['turn5'],
            'ma_spread': ind['ma_spread'],
            'range5': ind['range5'],
            'drawdown60': ind['drawdown60'],
            'pct60': ind['pct60'],
            'center_shift': ind['center_shift'],
            'ma5': ind['ma5'],
            'ma10': ind['ma10'],
            'ma20': ind['ma20'],
            'ma60': ind['ma60'],
            'industry': industry_map.get(code, '未知'),
        }

        # 分流
        if signals and grade in ('A', 'B'):
            breakout_candidates.append(entry)
        if grade in ('A', 'B') and score >= 14:
            power_candidates.append(entry)

    sector_rows, sector_strength_map = get_sector_snapshot(sector_pool, industry_map)

    # 第二阶段：补充板块强度后再算优先级
    for pool in (power_candidates, breakout_candidates):
        for item in pool:
            sector_meta = sector_strength_map.get(item['industry'])
            item['sector_avg_pct'] = sector_meta['avg_pct'] if sector_meta else 0.0
            item['sector_up_ratio'] = sector_meta['up_ratio'] if sector_meta else 0.0
            item['buy_priority_score'], item['buy_priority_level'], item['buy_priority_reasons'] = score_buy_priority(
                item, item['score'], item['grade'], item['signals'], sector_meta
            )

    # 排序：先看“优先买谁”，再看结构分
    power_candidates.sort(key=lambda x: (x['buy_priority_score'], x['score']), reverse=True)
    breakout_candidates.sort(key=lambda x: (x['buy_priority_score'], x['score']), reverse=True)

    # 合并得到“优先买谁”总榜（去重）
    buy_candidates = []
    seen_codes = set()
    merged = power_candidates + breakout_candidates
    merged.sort(key=lambda x: (x['buy_priority_score'], x['score']), reverse=True)
    for c in merged:
        if c['code'] in seen_codes:
            continue
        seen_codes.add(c['code'])
        buy_candidates.append(c)

    # ═══ 输出结果 ═══
    print()
    print("═" * 80)
    print(f"  扫描完成！共扫描 {scanned} 只，通过前置 {passed_pre} 只，通过排除 {passed_excl} 只")
    print(f"  📦 缓存命中 {_cache_hits} 只（跳过网络），网络拉取 {_cache_fetches} 只")
    print(f"  大盘判定: {market['state']}  执行建议: {market['action']}  建议新开仓数: {market['max_new_positions']}只")
    print("═" * 80)

    print()
    print("▓" * 80)
    print("  🌍 板块热度 TOP5")
    print("▓" * 80)
    print()
    if sector_rows:
        for rank, row in enumerate(sector_rows[:5], 1):
            print(f"  {rank}. {row[0]}  样本{row[1]}  均涨{row[2]:+.2f}%  上涨占比{row[3]*100:.0f}%")
    else:
        print("  （暂无可用板块热度数据）")

    # 优先买谁总榜
    print()
    print("▓" * 80)
    print("  🎯 优先买谁 TOP10（先看这个，再看分项榜）")
    print("▓" * 80)
    print()
    if not buy_candidates:
        print("  （无符合条件的候选）")
    else:
        print(f"{'排名':>4s} {'代码':<12s} {'名称':<8s} {'行业':<10s} {'价格':>7s} {'今涨跌':>7s} {'优先分':>6s} {'优先级':>5s} {'结构分':>6s} {'等级':>3s} {'信号':<24s} {'优先原因'}")
        print("-" * 160)
        for rank, c in enumerate(buy_candidates[:10], 1):
            signal_str = '|'.join(c['signals']) if c['signals'] else '-'
            reason_str = '、'.join(c['buy_priority_reasons']) if c['buy_priority_reasons'] else '-'
            print(f"{rank:>4d} {c['code']:<12s} {c['name']:<8s} {c['industry']:<10.10s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['buy_priority_score']:>6d} {c['buy_priority_level']:>5s} {c['score']:>6d} {c['grade']:>3s} {signal_str:<24.24s} {reason_str}")

    print()
    print(f"  仓位建议: 若当天只买1支，最少 {SINGLE_STOCK_MIN_POSITION:.0%} 仓；当前大盘环境建议最多开 {market['max_new_positions']} 支新仓")

    # 蓄力候选
    print()
    print("▓" * 80)
    print("  ⏳ 蓄力候选 TOP%d（洗盘到位、等待突破信号）" % TOP_N)
    print("▓" * 80)
    print()
    if not power_candidates:
        print("  （无符合条件的蓄力候选）")
    else:
        print(f"{'排名':>4s} {'代码':<12s} {'名称':<8s} {'行业':<10s} {'价格':>7s} {'今涨跌':>7s} {'优先分':>6s} {'优先级':>5s} {'评分':>4s} {'等级':>3s} {'量比5/20':>8s} {'5日换手':>7s} {'均线距':>6s} {'5日幅':>6s} {'60日涨':>7s} {'回撤':>7s} {'指标明细'}")
        print("-" * 140)
        for rank, c in enumerate(power_candidates[:TOP_N], 1):
            detail_str = ' '.join(c['details'])
            signal_str = f" 💥{'|'.join(c['signals'])}" if c['signals'] else ""
            print(f"{rank:>4d} {c['code']:<12s} {c['name']:<8s} {c['industry']:<10.10s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['buy_priority_score']:>6d} {c['buy_priority_level']:>5s} {c['score']:>4d} {c['grade']:>3s} {c['vol_ratio']:>8.2f} {c['turn5']:>6.2f}% {c['ma_spread']:>5.2f}% {c['range5']:>5.2f}% {c['pct60']:>+6.1f}% {c['drawdown60']:>+6.1f}% {detail_str}{signal_str}")

    # 突破候选
    print()
    print("▓" * 80)
    print("  💥 突破候选 TOP%d（近3天出现放量突破/金叉信号）" % TOP_N)
    print("▓" * 80)
    print()
    if not breakout_candidates:
        print("  （无符合条件的突破候选）")
    else:
        print(f"{'排名':>4s} {'代码':<12s} {'名称':<8s} {'行业':<10s} {'价格':>7s} {'今涨跌':>7s} {'优先分':>6s} {'优先级':>5s} {'评分':>4s} {'等级':>3s} {'信号':<30s} {'指标明细'}")
        print("-" * 140)
        for rank, c in enumerate(breakout_candidates[:TOP_N], 1):
            detail_str = ' '.join(c['details'])
            signal_str = '|'.join(c['signals'])
            print(f"{rank:>4d} {c['code']:<12s} {c['name']:<8s} {c['industry']:<10.10s} {c['price']:>7.2f} {c['pct_today']:>+6.2f}% {c['buy_priority_score']:>6d} {c['buy_priority_level']:>5s} {c['score']:>4d} {c['grade']:>3s} {signal_str:<30s} {detail_str}")

    print()
    print("═" * 80)
    print("  使用说明：")
    print("  1. 蓄力候选：这些是洗盘到位、等弹簧弹起来的票。")
    print("     先看优先分（买入先后），再看结构评分（是否符合形态）。")
    print("     下一步：从TOP里选3-5只加入每日观察池，等放量突破信号出现再买。")
    print(f"     若当日只做1只，最少按 {SINGLE_STOCK_MIN_POSITION:.0%} 仓执行；当前环境建议最多新开 {market['max_new_positions']} 只。")
    print()
    print("  2. 突破候选：这些是最近1-3天刚出现突破信号的票。")
    print("     需要人工验证突破是否有效（是真突破还是假突破）。")
    print("     下一步：检查突破当天量能是否>5日均量2倍，是否站稳关键位。")
    print()
    print("  ⚠️ 程序只负责「海选」，最终买入决策仍需人工判断+遵守铁律！")
    print()
    print("  💡 本次数据已缓存至 数据缓存/ 目录，下次运行仅拉取增量，速度大幅提升。")
    print("     如需全量刷新（除权除息后建议）：python auto_screener.py --force")
    print("═" * 80)


if __name__ == '__main__':
    main()
