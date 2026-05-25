#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场情绪生态分析 (V6.1)

维度1: 涨停板生态 — 涨停/跌停家数、昨日涨停今日表现、连板高度
维度2: 情绪周期判定 — 冰点→修复→发酵→高潮→分歧→退潮
维度3: 板块轮动预判 — 板块连续排名趋势、新晋强势板块

可独立运行查看报告，也可被 market_mode.py / offline_screener.py import
"""
import sys, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

from db_cache import get_connection, init_db
import pandas as pd
import numpy as np
from collections import defaultdict

init_db()


def compute_sentiment(verbose=True):
    """
    计算市场情绪生态全景，返回 dict:
      dates: list — 最近N日日期
      limit_up: dict(date→count) — 涨停家数
      limit_down: dict(date→count) — 跌停家数
      up_count: dict(date→count) — 上涨家数
      down_count: dict(date→count) — 下跌家数
      advance_ratio: dict(date→float) — 涨跌家数比
      yesterday_limit_up_today: dict(date→dict) — 昨日涨停今日表现
      max_streak: dict(date→int) — 最高连板高度
      cycle_phase: str — 当前情绪周期阶段
      cycle_score: int — 周期量化得分
      sector_rotation: dict — 板块轮动信息
    """
    conn = get_connection()

    # 获取最近20个交易日
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM kline_daily ORDER BY date DESC LIMIT 20"
    ).fetchall()]
    dates = sorted(dates)

    if len(dates) < 5:
        conn.close()
        return {'error': '数据不足5个交易日'}

    # ═══ 维度1: 涨停板生态 ═══

    limit_up = {}       # 涨停家数
    limit_down = {}     # 跌停家数
    up_count = {}       # 上涨家数
    down_count = {}     # 下跌家数
    flat_count = {}     # 平盘家数
    total_count = {}    # 总股票数
    limit_up_codes = {} # 涨停股票代码集合
    big_gain = {}       # 涨幅>5%家数

    for d in dates:
        rows = conn.execute(
            "SELECT code, pctChg FROM kline_daily WHERE date=? AND pctChg IS NOT NULL",
            [d]
        ).fetchall()
        codes_pct = {r[0]: r[1] for r in rows}
        total_count[d] = len(codes_pct)
        limit_up[d] = sum(1 for p in codes_pct.values() if p >= 9.5)
        limit_down[d] = sum(1 for p in codes_pct.values() if p <= -9.5)
        up_count[d] = sum(1 for p in codes_pct.values() if p > 0)
        down_count[d] = sum(1 for p in codes_pct.values() if p < 0)
        flat_count[d] = sum(1 for p in codes_pct.values() if p == 0)
        big_gain[d] = sum(1 for p in codes_pct.values() if p >= 5)
        limit_up_codes[d] = {c for c, p in codes_pct.items() if p >= 9.5}

    # 涨跌家数比
    advance_ratio = {}
    for d in dates:
        dn = down_count[d]
        advance_ratio[d] = up_count[d] / max(dn, 1)

    # ═══ 昨日涨停今日表现 ═══
    yesterday_lt_today = {}
    for i in range(1, len(dates)):
        today = dates[i]
        yesterday = dates[i - 1]
        yt_codes = limit_up_codes.get(yesterday, set())
        if not yt_codes:
            yesterday_lt_today[today] = {'count': 0, 'avg_pct': 0, 'up_ratio': 0, 'limit_again': 0}
            continue

        today_pcts = conn.execute(
            f"SELECT code, pctChg FROM kline_daily WHERE date=? AND code IN ({','.join('?' * len(yt_codes))})",
            [today] + list(yt_codes)
        ).fetchall()

        pcts = [r[1] for r in today_pcts if r[1] is not None]
        if pcts:
            yesterday_lt_today[today] = {
                'count': len(yt_codes),          # 昨日涨停数
                'avg_pct': np.mean(pcts),         # 今日平均涨跌
                'up_ratio': sum(1 for p in pcts if p > 0) / len(pcts),  # 上涨比例
                'limit_again': sum(1 for p in pcts if p >= 9.5),  # 连板数
            }
        else:
            yesterday_lt_today[today] = {'count': len(yt_codes), 'avg_pct': 0, 'up_ratio': 0, 'limit_again': 0}

    # ═══ 最高连板高度（近5日内最大连板天数）═══
    # 简化算法：找最近5日持续涨停的股票
    max_streak = {}
    recent5 = dates[-5:]
    for end_idx in range(len(dates) - 1, max(len(dates) - 6, 0), -1):
        d = dates[end_idx]
        best = 0
        today_lt = limit_up_codes.get(d, set())
        for code in today_lt:
            streak = 1
            for back in range(1, min(end_idx + 1, 10)):
                prev_d = dates[end_idx - back]
                if code in limit_up_codes.get(prev_d, set()):
                    streak += 1
                else:
                    break
            best = max(best, streak)
        max_streak[d] = best

    # ═══ 维度2: 情绪周期判定 ═══
    cycle_phase, cycle_score, cycle_detail = _detect_cycle(
        dates, limit_up, limit_down, up_count, down_count,
        advance_ratio, yesterday_lt_today, max_streak, big_gain, total_count
    )

    # ═══ 维度3: 板块轮动预判 ═══
    sector_rotation = _detect_sector_rotation(conn, dates)

    conn.close()

    result = {
        'dates': dates,
        'limit_up': limit_up,
        'limit_down': limit_down,
        'up_count': up_count,
        'down_count': down_count,
        'flat_count': flat_count,
        'total_count': total_count,
        'advance_ratio': advance_ratio,
        'big_gain': big_gain,
        'yesterday_lt_today': yesterday_lt_today,
        'max_streak': max_streak,
        'cycle_phase': cycle_phase,
        'cycle_score': cycle_score,
        'cycle_detail': cycle_detail,
        'sector_rotation': sector_rotation,
    }

    if verbose:
        _print_report(result)

    return result


def _detect_cycle(dates, limit_up, limit_down, up_count, down_count,
                  advance_ratio, yesterday_lt_today, max_streak, big_gain, total_count):
    """
    情绪周期判定:
      冰点(1) → 修复(2) → 发酵(3) → 高潮(4) → 分歧(5) → 退潮(6)

    综合以下信号:
      - 涨停家数趋势
      - 涨跌家数比趋势
      - 昨日涨停今日表现
      - 连板高度
      - 大涨(>5%)家数
    """
    if len(dates) < 5:
        return '未知', 0, {}

    recent5 = dates[-5:]
    today = dates[-1]
    yesterday = dates[-2]

    # 特征提取
    lu_today = limit_up.get(today, 0)
    lu_yesterday = limit_up.get(yesterday, 0)
    ld_today = limit_down.get(today, 0)
    ar_today = advance_ratio.get(today, 1)
    ar_yesterday = advance_ratio.get(yesterday, 1)

    # 涨停趋势：近3日涨停均值 vs 近5日涨停均值
    lu_3 = np.mean([limit_up.get(d, 0) for d in dates[-3:]])
    lu_5 = np.mean([limit_up.get(d, 0) for d in dates[-5:]])
    lu_trend = lu_3 / max(lu_5, 1)  # >1=加速, <1=减速

    # 昨日涨停今日表现
    ylt = yesterday_lt_today.get(today, {})
    ylt_avg = ylt.get('avg_pct', 0)
    ylt_up_ratio = ylt.get('up_ratio', 0)
    ylt_limit_again = ylt.get('limit_again', 0)

    # 连板高度
    ms_today = max_streak.get(today, 0)

    # 大涨家数占比
    bg_ratio = big_gain.get(today, 0) / max(total_count.get(today, 1), 1)

    # 涨跌比趋势
    ar_3 = np.mean([advance_ratio.get(d, 1) for d in dates[-3:]])

    detail = {
        'lu_today': lu_today, 'lu_3avg': round(lu_3, 1), 'lu_trend': round(lu_trend, 2),
        'ld_today': ld_today,
        'ar_today': round(ar_today, 2), 'ar_3avg': round(ar_3, 2),
        'ylt_avg_pct': round(ylt_avg, 2), 'ylt_up_ratio': round(ylt_up_ratio, 2),
        'ylt_limit_again': ylt_limit_again,
        'max_streak': ms_today,
        'big_gain_ratio': round(bg_ratio * 100, 1),
    }

    # ═══ 判定逻辑（与选股参数手册V6.1对齐，使用3日均值）═══
    score = 0

    # 涨停家数信号 (0-3分) — 规则：用3日均值，阈值30/50/80
    if lu_3 >= 80:
        score += 3
    elif lu_3 >= 50:
        score += 2
    elif lu_3 >= 30:
        score += 1

    # 涨跌家数比信号 (0-3分) — 规则：用3日均值，阈值0.5/1/3
    if ar_3 >= 3:
        score += 3
    elif ar_3 >= 1:
        score += 2
    elif ar_3 >= 0.5:
        score += 1

    # 昨涨停今表现信号 (0-3分) — 规则：满分3分，阈值0/1/2
    if ylt_avg > 2:
        score += 3  # 接力非常强
    elif ylt_avg > 1:
        score += 2  # 接力良好
    elif ylt_avg > 0:
        score += 1  # 接力正常
    elif ylt_avg < -2:
        score -= 1  # 接力崩了

    # 连板高度信号 (0-3分) — 规则：满分3分，阈值3/5/6
    if ms_today >= 6:
        score += 3
    elif ms_today >= 5:
        score += 2
    elif ms_today >= 3:
        score += 1

    # 趋势信号 (加减分)
    if lu_trend > 1.3:
        score += 1  # 涨停加速
    elif lu_trend < 0.7:
        score -= 1  # 涨停衰退

    # 跌停警告
    if ld_today >= 30:
        score -= 2
    elif ld_today >= 15:
        score -= 1

    score = max(0, min(12, score))

    # 映射到周期阶段 — 与选股参数手册V6.1对齐
    if score <= 2:
        phase = '冰点'
    elif score <= 4:
        phase = '修复'
    elif score <= 7:
        phase = '发酵'
    elif score <= 10:
        phase = '高潮'
    elif score <= 11:
        phase = '分歧'
    else:
        phase = '过热'

    # 特殊修正：涨停在减少但还很多 → 分歧
    if lu_today >= 40 and lu_trend < 0.85 and ylt_avg < 1:
        phase = '分歧'
    # 特殊修正：涨停断崖下降 → 退潮
    if lu_today < lu_yesterday * 0.5 and lu_yesterday >= 50:
        phase = '退潮'
    # 特殊修正：极端高潮后 → 需要警惕
    if lu_today >= 100 and ar_today >= 4:
        phase = '高潮'

    detail['score'] = score
    return phase, score, detail


def _detect_sector_rotation(conn, dates):
    """
    板块轮动预判：
    - 找出连续3日排名上升的板块（新晋强势）
    - 找出连续3日排名下降的板块（退潮板块）
    - 找出波动最大的板块（可能轮动目标）
    """
    if len(dates) < 5:
        return {}

    sec_df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY date, avg_pct DESC', conn)
    sec_df['avg_pct'] = pd.to_numeric(sec_df['avg_pct'], errors='coerce')

    # 每日板块排名
    daily_rank = {}
    for d, grp in sec_df.groupby('date'):
        grp = grp.sort_values('avg_pct', ascending=False).reset_index(drop=True)
        for i, row in grp.iterrows():
            daily_rank[(row['industry'], d)] = i + 1

    recent5 = dates[-5:]
    all_industries = sec_df['industry'].unique()

    # 计算每个板块近5日排名变化
    rotation = []
    for ind in all_industries:
        ranks = [daily_rank.get((ind, d), 50) for d in recent5]
        if len(ranks) < 5:
            continue
        # 排名变化趋势（排名下降=变强）
        rank_change = ranks[-1] - ranks[0]  # 负数=排名上升=变强
        recent_rank = ranks[-1]
        avg_rank = np.mean(ranks)

        # 近3日排名连续上升（值变小）
        rising = ranks[-1] < ranks[-2] < ranks[-3]
        falling = ranks[-1] > ranks[-2] > ranks[-3]

        # 单日涨幅
        last_pcts = []
        for d in recent5:
            r = sec_df[(sec_df['industry'] == ind) & (sec_df['date'] == d)]
            if not r.empty:
                last_pcts.append(float(r['avg_pct'].iloc[0]))

        sum5 = sum(last_pcts) if last_pcts else 0

        rotation.append({
            'industry': ind,
            'ranks': ranks,
            'rank_change': rank_change,
            'recent_rank': recent_rank,
            'avg_rank': avg_rank,
            'rising': rising,
            'falling': falling,
            'sum5_pct': round(sum5, 2),
        })

    rotation.sort(key=lambda x: x['rank_change'])
    rising_sectors = [r for r in rotation if r['rising'] and r['recent_rank'] <= 30]
    falling_sectors = [r for r in rotation if r['falling'] and r['recent_rank'] >= 50]

    return {
        'rising': rising_sectors[:5],   # 新晋强势TOP5
        'falling': falling_sectors[:5],  # 退潮TOP5
        'top5_today': sorted(rotation, key=lambda x: x['recent_rank'])[:5],
        'bottom5_today': sorted(rotation, key=lambda x: -x['recent_rank'])[:5],
    }


def _print_report(result):
    dates = result['dates']
    recent = dates[-7:] if len(dates) >= 7 else dates

    print("=" * 80)
    print("  市场情绪生态报告 (V6.1)")
    print("=" * 80)

    # ── 涨停板生态 ──
    print("\n  ── 涨停板生态 ──")
    print(f"  {'日期':>12s} {'涨停':>5s} {'跌停':>5s} {'上涨':>6s} {'下跌':>6s} {'涨跌比':>6s} {'涨>5%':>6s} {'连板高度':>6s}")
    for d in recent:
        lu = result['limit_up'].get(d, 0)
        ld = result['limit_down'].get(d, 0)
        uc = result['up_count'].get(d, 0)
        dc = result['down_count'].get(d, 0)
        ar = result['advance_ratio'].get(d, 0)
        bg = result['big_gain'].get(d, 0)
        ms = result['max_streak'].get(d, 0)
        # 涨停柱状图
        lu_bar = "█" * min(lu // 10, 12)
        print(f"  {d:>12s} {lu:>5d} {ld:>5d} {uc:>6d} {dc:>6d} {ar:>6.2f} {bg:>6d} {ms:>4d}板  {lu_bar}")

    # ── 昨日涨停今日表现 ──
    print("\n  ── 昨日涨停→今日表现（资金接力意愿）──")
    for d in recent[1:]:
        ylt = result['yesterday_lt_today'].get(d, {})
        if ylt.get('count', 0) == 0:
            continue
        avg = ylt['avg_pct']
        ur = ylt['up_ratio']
        la = ylt['limit_again']
        tag = "🟢强接力" if avg > 2 else "🟡正常" if avg > 0 else "🔴崩了"
        print(f"  {d}: 昨{ylt['count']}家涨停→今均涨{avg:+.2f}% 上涨{ur:.0%} 连板{la}家 {tag}")

    # ── 情绪周期 ──
    cd = result['cycle_detail']
    phase = result['cycle_phase']
    cycle_score = result['cycle_score']

    phase_map = {
        '冰点': ('🧊', '极度恐慌，无人敢买', '适合S1蓄力选股，等待转折'),
        '修复': ('🌱', '跌停减少，有资金试探', 'S1为主，可开始关注S2'),
        '发酵': ('🔥', '涨停增加，板块效应显现', 'S2/S3主力，追热点板块'),
        '高潮': ('🎆', '涨停潮，百股涨停', '⚠️不追高！锁利为主，准备防守'),
        '分歧': ('⚡', '涨停衰退，昨涨停今分化', '⚠️减仓观望，只守不攻'),
        '退潮': ('🌊', '涨停断崖，接力失败', '空仓等待冰点信号'),
        '过热': ('💥', '情绪极端，注意风险', '⛔不开新仓'),
    }
    emoji, desc, advice = phase_map.get(phase, ('❓', '未知', '观望'))

    print(f"\n  ── 情绪周期判定 ──")
    print(f"  当前阶段: {emoji} {phase} (得分{cycle_score}/12)")
    print(f"  特征描述: {desc}")
    print(f"  操作建议: {advice}")
    print(f"  核心指标:")
    print(f"    涨停: {cd.get('lu_today', 0)}家(3日均{cd.get('lu_3avg', 0)}), 趋势{'↑加速' if cd.get('lu_trend', 1) > 1.1 else '→平稳' if cd.get('lu_trend', 1) > 0.9 else '↓衰退'}")
    print(f"    跌停: {cd.get('ld_today', 0)}家")
    print(f"    涨跌比: {cd.get('ar_today', 0)}(3日均{cd.get('ar_3avg', 0)})")
    print(f"    昨涨停→今: 均涨{cd.get('ylt_avg_pct', 0):+.2f}%, 上涨{cd.get('ylt_up_ratio', 0):.0%}")
    print(f"    连板高度: {cd.get('max_streak', 0)}板")
    print(f"    大涨(>5%)占比: {cd.get('big_gain_ratio', 0):.1f}%")

    # ── 板块轮动 ──
    sr = result.get('sector_rotation', {})
    if sr:
        print(f"\n  ── 板块轮动预判 ──")

        rising = sr.get('rising', [])
        if rising:
            print(f"  🔺 新晋强势板块（排名连续上升）:")
            for r in rising[:5]:
                ranks_str = '→'.join(str(x) for x in r['ranks'][-3:])
                print(f"    {r['industry'][:15]:<16s} 排名{ranks_str} 5日累涨{r['sum5_pct']:+.2f}%")

        falling = sr.get('falling', [])
        if falling:
            print(f"  🔻 退潮板块（排名连续下降）:")
            for r in falling[:5]:
                ranks_str = '→'.join(str(x) for x in r['ranks'][-3:])
                print(f"    {r['industry'][:15]:<16s} 排名{ranks_str} 5日累涨{r['sum5_pct']:+.2f}%")

    print()


def get_cycle_phase():
    """静默返回情绪周期阶段和得分，供其他模块调用"""
    result = compute_sentiment(verbose=False)
    return {
        'phase': result.get('cycle_phase', '未知'),
        'score': result.get('cycle_score', 0),
        'detail': result.get('cycle_detail', {}),
        'sector_rotation': result.get('sector_rotation', {}),
    }


if __name__ == '__main__':
    compute_sentiment(verbose=True)
