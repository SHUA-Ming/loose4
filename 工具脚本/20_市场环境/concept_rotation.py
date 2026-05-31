#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念主线与轮动分析脚本

用途：早盘/盘中先判断当前市场在炒哪些概念、处于什么节奏，
再决定是顺势跟随、等分歧低吸，还是回避退潮方向。

依赖数据：
  concept_daily   由 10_数据更新/_fetch_concept_data.py 维护
  concept_member  当前概念成分股
  kline_daily     个股日K，用于概念内前排/梯队定位
"""
import argparse
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_connection, init_db
import pandas as pd
import numpy as np

SOURCE = 'eastmoney'


def _fmt_pct(value):
    if value is None or pd.isna(value):
        return "  -- "
    return f"{float(value):+5.2f}%"


def _safe_float(value, default=0.0):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _calc_return(values, window):
    arr = [v for v in values if pd.notna(v) and v > 0]
    if len(arr) <= window:
        return 0.0
    return (arr[-1] / arr[-1 - window] - 1) * 100


def _classify_stage(metric):
    rank_today = metric['rank_today']
    rank_pct_today = metric['rank_pct_today']
    avg_rank5 = metric['avg_rank5']
    pct_today = metric['pct_today']
    pct3 = metric['pct3']
    pct5 = metric['pct5']
    pct20 = metric['pct20']
    rank_change5 = metric['rank_change5']
    amount_ratio = metric['amount_ratio']
    above_ma20 = metric['above_ma20']

    if rank_change5 >= 30 and pct5 < 0:
        return '退潮回避'
    if avg_rank5 <= 0.25 and pct20 > 4 and pct_today <= -1.0 and above_ma20:
        return '主线分歧'
    if rank_pct_today <= 0.10 and pct3 >= 8 and amount_ratio >= 1.5:
        return '高潮谨慎'
    if avg_rank5 <= 0.18 and pct20 >= 8 and above_ma20:
        return '主线'
    if rank_pct_today <= 0.25 and rank_change5 <= -20 and pct5 > 1:
        return '新晋发酵'
    if rank_today <= 30 and pct5 > 0:
        return '强势观察'
    return '普通轮动'


def _score_metric(metric):
    score = 0.0
    score += max(0.0, 0.30 - metric['rank_pct_today']) / 0.30 * 25
    score += max(0.0, 0.35 - metric['avg_rank5']) / 0.35 * 20
    score += min(max(metric['pct5'], 0.0), 15.0) * 1.5
    score += min(max(metric['pct20'], 0.0), 30.0) * 0.6
    if metric['rank_change5'] < 0:
        score += min(abs(metric['rank_change5']), 50) * 0.35
    score += min(max(metric['amount_ratio'] - 1, 0.0), 2.0) * 8
    score += metric['strong_days5'] * 2
    if metric['stage'] == '主线分歧':
        score += 8
    elif metric['stage'] == '退潮回避':
        score -= 20
    elif metric['stage'] == '高潮谨慎':
        score -= 5
    return round(score, 1)


def _load_concept_daily(conn, start_date, end_date=None):
    sql = "SELECT * FROM concept_daily WHERE source = ? AND date >= ?"
    params = [SOURCE, start_date]
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    sql += " ORDER BY date, concept_name"
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return df
    for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'amplitude', 'pctChg', 'change_amount', 'turn']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    df['date'] = df['date'].astype(str)
    return df


def _resolve_source(conn, requested_source):
    if requested_source != 'auto':
        return requested_source
    rows = conn.execute(
        "SELECT source, COUNT(*), MAX(date) FROM concept_daily GROUP BY source ORDER BY MAX(date) DESC, COUNT(*) DESC"
    ).fetchall()
    if rows:
        return rows[0][0]
    return 'eastmoney'


def _build_metrics(df):
    latest_date = df['date'].max()
    all_dates = sorted(df['date'].unique())
    if len(all_dates) < 5:
        return [], latest_date, all_dates

    daily_rank = {}
    daily_count = {}
    for date_val, grp in df.groupby('date'):
        grp = grp.dropna(subset=['pctChg']).sort_values('pctChg', ascending=False).reset_index(drop=True)
        daily_count[date_val] = len(grp)
        for idx, row in grp.iterrows():
            daily_rank[(row['concept_name'], date_val)] = idx + 1

    metrics = []
    for concept_name, grp in df.groupby('concept_name'):
        grp = grp.sort_values('date').reset_index(drop=True)
        if len(grp) < 5:
            continue
        dates = grp['date'].tolist()
        closes = grp['close'].tolist()
        pcts = grp['pctChg'].fillna(0).tolist()
        amounts = grp['amount'].fillna(0).tolist()
        latest = grp.iloc[-1]

        recent_dates = dates[-5:]
        ranks5 = [daily_rank.get((concept_name, date_val), daily_count.get(date_val, 999)) for date_val in recent_dates]
        rank_today = ranks5[-1]
        total_today = daily_count.get(dates[-1], 1)
        rank_pct_today = rank_today / max(total_today, 1)
        avg_rank5 = np.mean([r / max(daily_count.get(d, 1), 1) for r, d in zip(ranks5, recent_dates)])
        rank_change5 = ranks5[-1] - ranks5[0]
        strong_days5 = sum(1 for r, d in zip(ranks5, recent_dates) if r / max(daily_count.get(d, 1), 1) <= 0.20)

        amount5 = np.mean(amounts[-5:]) if len(amounts) >= 5 else np.mean(amounts)
        amount20 = np.mean(amounts[-20:]) if len(amounts) >= 20 else np.mean(amounts)
        amount_ratio = amount5 / amount20 if amount20 and amount20 > 0 else 1.0
        ma20 = np.mean([c for c in closes[-20:] if pd.notna(c)]) if len(closes) >= 20 else np.mean([c for c in closes if pd.notna(c)])
        close_last = _safe_float(latest.get('close'))

        metric = {
            'concept_name': concept_name,
            'date': latest_date,
            'rank_today': rank_today,
            'total_today': total_today,
            'rank_pct_today': rank_pct_today,
            'ranks5': ranks5,
            'avg_rank5': float(avg_rank5),
            'rank_change5': int(rank_change5),
            'strong_days5': int(strong_days5),
            'pct_today': _safe_float(latest.get('pctChg')),
            'pct3': _calc_return(closes, 3),
            'pct5': _calc_return(closes, 5),
            'pct20': _calc_return(closes, 20),
            'amount_ratio': float(amount_ratio),
            'close': close_last,
            'above_ma20': bool(close_last > ma20) if ma20 and not pd.isna(ma20) else False,
        }
        metric['stage'] = _classify_stage(metric)
        metric['score'] = _score_metric(metric)
        metrics.append(metric)

    metrics.sort(key=lambda x: x['score'], reverse=True)
    return metrics, latest_date, all_dates


def _latest_stock_date(conn):
    try:
        row = conn.execute(
            """
            SELECT MAX(date)
            FROM kline_daily
            WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'
            """
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _latest_stock_window(conn, end_date, window=6):
    rows = conn.execute(
        "SELECT DISTINCT date FROM kline_daily WHERE code='sh.000001' AND date <= ? ORDER BY date DESC LIMIT %s",
        (end_date, window)
    ).fetchall()
    dates = sorted([r[0] for r in rows])
    if len(dates) >= 2:
        return dates[0], dates[-1]
    return None, None


def _load_leaders(conn, concept_name, end_date, top_n=5):
    start_date, last_date = _latest_stock_window(conn, end_date, window=6)
    if not start_date or not last_date:
        return []
    members = conn.execute(
        "SELECT code, code_name FROM concept_member WHERE source = ? AND concept_name = ?",
        (SOURCE, concept_name)
    ).fetchall()
    codes = [m[0] for m in members if m[0].startswith(('sh.', 'sz.'))]
    name_map = {m[0]: m[1] for m in members}
    if not codes:
        return []

    leaders = []
    chunk_size = 300
    for start_idx in range(0, len(codes), chunk_size):
        chunk = codes[start_idx:start_idx + chunk_size]
        placeholders = ','.join(['?'] * len(chunk))
        sql = f"""
            SELECT code, date, close, pctChg, amount
            FROM kline_daily
            WHERE code IN ({placeholders}) AND date >= ? AND date <= ?
            ORDER BY code, date
        """
        df = pd.read_sql_query(sql, conn, params=chunk + [start_date, last_date])
        if df.empty:
            continue
        for col in ['close', 'pctChg', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        for code, grp in df.groupby('code'):
            grp = grp.sort_values('date').dropna(subset=['close'])
            if len(grp) < 2:
                continue
            first = grp.iloc[0]
            last = grp.iloc[-1]
            if first['close'] <= 0:
                continue
            chg5 = (last['close'] / first['close'] - 1) * 100
            leaders.append({
                'code': code,
                'name': name_map.get(code, ''),
                'chg5': chg5,
                'pct_today': _safe_float(last.get('pctChg')),
                'amount': _safe_float(last.get('amount')),
            })

    leaders.sort(key=lambda x: (x['chg5'], x['amount']), reverse=True)
    total = len(leaders)
    for idx, item in enumerate(leaders):
        rank_pct = idx / max(total - 1, 1)
        if rank_pct <= 0.15:
            item['tier'] = '龙头'
        elif rank_pct <= 0.50:
            item['tier'] = '跟风'
        else:
            item['tier'] = '补涨'
    return leaders[:top_n]


def _print_section(title, items, conn, latest_date, top_n, with_rule=False):
    print()
    print("▓" * 80)
    print(f"  {title}")
    print("▓" * 80)
    if not items:
        print("  （暂无）")
        return
    print(f"  {'#':>2s} {'概念':<16s} {'阶段':<8s} {'分数':>5s} {'今涨':>8s} {'3日':>8s} {'5日':>8s} {'20日':>8s} {'今排名':>7s} {'量能':>6s}  前排")
    print("-" * 120)
    for idx, item in enumerate(items[:top_n], 1):
        leaders = _load_leaders(conn, item['concept_name'], latest_date, top_n=3)
        leader_text = '、'.join(
            f"{l['name'] or l['code']}({l['tier']},{l['chg5']:+.1f}%)" for l in leaders
        ) or '-'
        print(
            f"  {idx:>2d} {item['concept_name'][:16]:<16s} {item['stage']:<8s} {item['score']:>5.1f} "
            f"{_fmt_pct(item['pct_today']):>8s} {_fmt_pct(item['pct3']):>8s} {_fmt_pct(item['pct5']):>8s} {_fmt_pct(item['pct20']):>8s} "
            f"{item['rank_today']:>3d}/{item['total_today']:<3d} "
            f"{item['amount_ratio']:>5.2f}x  {leader_text}"
        )
    if with_rule:
        print()
        print("  观察纪律：只低吸主线仍在、龙头不破位的分歧；退潮概念不做低吸，高潮概念不追后排。")


def analyze_concepts(days=120, top=20, as_of=None, source='auto'):
    global SOURCE
    init_db()
    conn = get_connection()
    SOURCE = _resolve_source(conn, source)
    end_date = as_of
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    df = _load_concept_daily(conn, start_date, end_date=end_date)
    if df.empty:
        conn.close()
        return {'error': 'concept_daily 为空，请先运行 _fetch_concept_data.py --full --days 365'}
    metrics, latest_date, all_dates = _build_metrics(df)
    if not metrics:
        conn.close()
        return {'error': '概念历史数据不足，至少需要5个交易日'}

    stock_latest = _latest_stock_date(conn)
    result = {
        'latest_date': latest_date,
        'stock_latest_date': stock_latest,
        'is_stale': bool(stock_latest and latest_date < stock_latest),
        'date_count': len(all_dates),
        'concept_count': len(metrics),
        'top': metrics[:top],
        'mainline': [m for m in metrics if m['stage'] in ('主线', '主线分歧')],
        'divergence': [m for m in metrics if m['stage'] == '主线分歧'],
        'emerging': [m for m in metrics if m['stage'] == '新晋发酵'],
        'climax': [m for m in metrics if m['stage'] == '高潮谨慎'],
        'falling': sorted([m for m in metrics if m['stage'] == '退潮回避'], key=lambda x: x['score']),
    }
    conn.close()
    return result


def main():
    global SOURCE
    parser = argparse.ArgumentParser(description='分析概念主线与轮动节奏')
    parser.add_argument('--days', type=int, default=120, help='分析最近多少自然日概念历史')
    parser.add_argument('--top', type=int, default=20, help='每个榜单最多输出多少个概念')
    parser.add_argument('--as-of', default=None, help='只分析到指定日期 YYYY-MM-DD')
    parser.add_argument('--source', choices=['auto', 'eastmoney', 'ths'], default='auto', help='概念数据源，默认自动选择最新可用源')
    args = parser.parse_args()

    init_db()
    conn = get_connection()
    SOURCE = _resolve_source(conn, args.source)
    start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
    df = _load_concept_daily(conn, start_date, end_date=args.as_of)
    if df.empty:
        print("concept_daily 为空，请先运行：")
        print("  python 工具脚本/10_数据更新/_fetch_concept_data.py --full --days 365")
        conn.close()
        return 1

    metrics, latest_date, all_dates = _build_metrics(df)
    if not metrics:
        print("概念历史数据不足，至少需要5个交易日")
        conn.close()
        return 1

    top_items = metrics[:args.top]
    divergence = [m for m in metrics if m['stage'] == '主线分歧']
    emerging = [m for m in metrics if m['stage'] == '新晋发酵']
    mainline = [m for m in metrics if m['stage'] == '主线']
    climax = [m for m in metrics if m['stage'] == '高潮谨慎']
    falling = sorted([m for m in metrics if m['stage'] == '退潮回避'], key=lambda x: x['score'])
    stock_latest = _latest_stock_date(conn)

    print("=" * 80)
    print("  概念主线与轮动雷达")
    print("=" * 80)
    print(f"  数据源: {SOURCE}  数据日期: {latest_date}  统计交易日: {len(all_dates)}  概念数: {len(metrics)}")
    if stock_latest and latest_date < stock_latest:
        print(f"  ⚠️ 概念数据滞后：概念最新{latest_date}，个股最新{stock_latest}。盘中/收盘分析前请先运行 _fetch_concept_data.py。")
    print(f"  分析口径: 今天强度 + 3/5/20日趋势 + 排名变化 + 量能放大 + 节奏标签")

    _print_section("主线/强势概念 TOP", top_items, conn, latest_date, args.top)
    _print_section("分歧低吸观察池", divergence, conn, latest_date, min(args.top, 10), with_rule=True)
    _print_section("新晋发酵概念", emerging, conn, latest_date, min(args.top, 10))
    _print_section("稳定主线概念", mainline, conn, latest_date, min(args.top, 10))
    _print_section("高潮谨慎，不追后排", climax, conn, latest_date, min(args.top, 10))
    _print_section("退潮回避", falling, conn, latest_date, min(args.top, 10))

    print()
    print("=" * 80)
    print("  今日使用方式")
    print("=" * 80)
    print("  1. 先看是否有稳定主线；没有主线时降低交易频率。")
    print("  2. 主线分歧只看龙头/前排低吸，不吸后排补涨。")
    print("  3. 新晋发酵可小仓试错，必须等板块内多股同步。")
    print("  4. 高潮概念只做持仓止盈，不追新仓。")
    print("  5. 退潮概念即使个股形态好，也降级观察或回避。")
    print("=" * 80)
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())