#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
申万三级行业 两周轮动分析

口径优势：申万三级(336个)"能分级、不重叠"，比证监会大类(单层~86，太粗)和
同花顺概念(扁平、重叠)都干净。本脚本沿时间轴看最近两周(默认10个交易日)谁在
轮动走强：把窗口切成前后两周，逐日给三级行业按当日涨幅排名，识别四档：
    ① 持续强势领涨   两周内多数日排名前20%，主线级别
    ② 加速轮入       后一周排名明显跃升、资金正在切入
    ③ 稳步抬升       累计涨幅居前、多数日收红的温和走强
    ④ 高位退潮       前强后弱，减仓回避方向

依赖数据：
    sw_industry_daily  申万行业指数日K   由 10_数据更新/_fetch_sw_industry.py 维护
    sw_industry_info   申万三层树信息(三级→二级→一级)

纯分析脚本：只读连接，不建表、不调 init_db（遵守 MySQL 铁律）。
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
from db_cache import get_connection
import pandas as pd
import numpy as np


def _fmt_pct(value):
    if value is None or pd.isna(value):
        return "  -- "
    return f"{float(value):+5.1f}%"


def _compound(pcts):
    v = 1.0
    for p in pcts:
        v *= (1 + (p or 0) / 100)
    return (v - 1) * 100


def _load_info(conn):
    """返回 code->(name,level,parent), 以及三级→一级祖父映射。"""
    rows = conn.execute(
        "SELECT code, level, name, parent_name FROM sw_industry_info"
    ).fetchall()
    info = {}
    l2_parent = {}   # 二级name -> 一级name
    for code, level, name, parent in rows:
        info[code] = {'name': name, 'level': int(level), 'parent': parent}
        if int(level) == 2:
            l2_parent[name] = parent
    l3_codes = [c for c, v in info.items() if v['level'] == 3]
    l1_codes = [c for c, v in info.items() if v['level'] == 1]
    return info, l2_parent, l3_codes, l1_codes


def _grandparent(info, l2_parent, code):
    """三级行业的一级(门类)名。"""
    v = info.get(code, {})
    l2name = v.get('parent')
    return l2_parent.get(l2name, l2name or '-')


def _load_daily(conn, codes, start_date, end_date=None):
    if not codes:
        return pd.DataFrame()
    frames = []
    chunk = 300
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        ph = ','.join(['?'] * len(part))
        sql = f"SELECT code,date,close,amount,pctChg FROM sw_industry_daily WHERE code IN ({ph}) AND date >= ?"
        params = part + [start_date]
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df['date'] = df['date'].astype(str)
    for c in ('close', 'amount', 'pctChg'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def _build_metrics(df, window, info, l2_parent):
    all_dates = sorted(df['date'].unique())
    if len(all_dates) < window:
        window = len(all_dates)
    if window < 4:
        return [], all_dates
    win = all_dates[-window:]
    half = window // 2
    wa, wb = win[:half], win[half:]
    recent20 = all_dates[-20:]

    # 逐日排名分位(仅三级宇宙内)
    rank_pct = {}
    for d, g in df[df['date'].isin(all_dates)].groupby('date'):
        g = g.dropna(subset=['pctChg']).sort_values('pctChg', ascending=False).reset_index(drop=True)
        n = len(g)
        for i, row in g.iterrows():
            rank_pct[(row['code'], d)] = (i + 1) / max(n, 1)

    metrics = []
    for code, g in df.groupby('code'):
        g = g.sort_values('date')
        dd = dict(zip(g['date'], g['pctChg']))
        am = dict(zip(g['date'], g['amount']))
        present = sum(d in dd for d in win)
        if present < window - 1:
            continue
        pw = [dd.get(d, 0) for d in win]
        rp_full = [rank_pct.get((code, d), 1.0) for d in win]
        rp_a = [rank_pct.get((code, d), 1.0) for d in wa]
        rp_b = [rank_pct.get((code, d), 1.0) for d in wb]
        rp20 = [rank_pct.get((code, d), 1.0) for d in recent20 if (code, d) in rank_pct]
        amA = np.mean([am.get(d, 0) for d in wa]) if wa else 0
        amB = np.mean([am.get(d, 0) for d in wb]) if wb else 0
        name = info.get(code, {}).get('name', code)
        metrics.append({
            'code': code,
            'name': name,
            'l2': info.get(code, {}).get('parent', '-'),
            'l1': _grandparent(info, l2_parent, code),
            'cum10': _compound(pw),
            'cumA': _compound([dd.get(d, 0) for d in wa]),
            'cumB': _compound([dd.get(d, 0) for d in wb]),
            'avg_rp': float(np.mean(rp_full)),
            'avg_rpA': float(np.mean(rp_a)) if rp_a else 1.0,
            'avg_rpB': float(np.mean(rp_b)) if rp_b else 1.0,
            'top20_full': int(sum(r <= 0.20 for r in rp_full)),
            'top20_A': int(sum(r <= 0.20 for r in rp_a)),
            'top20_B': int(sum(r <= 0.20 for r in rp_b)),
            'top20_20d': int(sum(r <= 0.20 for r in rp20)),
            'up_days': int(sum(p > 0 for p in pw)),
            'rank_improve': float(np.mean(rp_a) - np.mean(rp_b)) if (rp_a and rp_b) else 0.0,
            'amt_ratio': float(amB / amA) if amA and amA > 0 else 1.0,
            'last_rp': rp_full[-1],
            'window': window,
            'win_start': win[0],
            'win_end': win[-1],
        })

    for m in metrics:
        m['stage'] = _classify(m, window)
        m['score'] = _score(m)
    metrics.sort(key=lambda x: x['score'], reverse=True)
    return metrics, win


def _classify(m, window):
    half = window // 2
    strong_full = max(3, round(window * 0.5))
    if m['top20_A'] >= 2 and (m['avg_rpB'] - m['avg_rpA']) >= 0.15 and m['cumB'] < 0:
        return '高位退潮'
    if m['top20_full'] >= strong_full and m['cum10'] > 0:
        return '持续领涨'
    if m['avg_rpB'] <= 0.33 and m['rank_improve'] >= 0.10 and m['cumB'] > 1 and m['top20_B'] >= 2:
        return '加速轮入'
    if m['cum10'] >= 5 and m['up_days'] >= max(4, round(window * 0.6)) and m['cumB'] >= 0:
        return '稳步抬升'
    if m['last_rp'] <= 0.15 and m['cumB'] > 0:
        return '新晋活跃'
    return '普通'


def _score(m):
    s = 0.0
    s += m['top20_full'] * 6
    s += min(max(m['cum10'], -10), 30) * 0.7
    s += min(max(m['cumB'], -10), 25) * 0.6
    s += max(m['rank_improve'], 0) * 30
    s += min(max(m['amt_ratio'] - 1, 0), 2) * 5
    s += m['top20_20d'] * 0.8
    if m['stage'] == '高位退潮':
        s -= 25
    elif m['stage'] == '持续领涨':
        s += 6
    elif m['stage'] == '加速轮入':
        s += 5
    return round(s, 1)


def _is_mainboard(code):
    """只保留主板 00/60（用户账户只能买主板）。"""
    return code.startswith('sh.60') or code.startswith('sz.00')


def _latest_kline_date(conn):
    row = conn.execute("SELECT MAX(date) FROM kline_daily WHERE code='sh.000001'").fetchone()
    return row[0] if row and row[0] else None


def _load_leaders(conn, l3_code, win_start, win_end, today, top_n=5):
    """某三级行业成分股的两周涨幅+今日涨幅，标注主板可买。
    只读 join sw_industry_member + kline_daily（个股K有到最新交易日，可看轮动是否延续到今天）。"""
    mem = conn.execute(
        "SELECT stock_code, stock_name FROM sw_industry_member WHERE l3_code = ?", (l3_code,)
    ).fetchall()
    codes = [m[0] for m in mem]
    name_map = {m[0]: m[1] for m in mem}
    if not codes:
        return []
    out = []
    for i in range(0, len(codes), 300):
        part = codes[i:i + 300]
        ph = ','.join(['?'] * len(part))
        df = pd.read_sql_query(
            f"SELECT code,date,close,pctChg FROM kline_daily WHERE code IN ({ph}) AND date >= ? AND date <= ?",
            conn, params=part + [win_start, today])
        if df.empty:
            continue
        df['date'] = df['date'].astype(str)
        for c in ('close', 'pctChg'):
            df[c] = pd.to_numeric(df[c], errors='coerce')
        for code, g in df.groupby('code'):
            g = g.sort_values('date').dropna(subset=['close'])
            in_win = g[g['date'] <= win_end]
            if len(in_win) < 2 or in_win.iloc[0]['close'] <= 0:
                continue
            chg2w = (in_win.iloc[-1]['close'] / in_win.iloc[0]['close'] - 1) * 100
            trow = g[g['date'] == today]['pctChg']
            today_pct = float(trow.iloc[0]) if len(trow) else float('nan')
            out.append({
                'code': code, 'name': name_map.get(code, ''),
                'chg2w': chg2w, 'today': today_pct, 'mb': _is_mainboard(code),
            })
    out.sort(key=lambda x: x['chg2w'], reverse=True)
    return out[:top_n]


def _load_l1_context(conn, l1_codes, info, window):
    df = _load_daily(conn, l1_codes, (datetime.now() - timedelta(days=40)).strftime('%Y-%m-%d'))
    if df.empty:
        return []
    all_dates = sorted(df['date'].unique())
    win = all_dates[-window:] if len(all_dates) >= window else all_dates
    out = []
    for code, g in df.groupby('code'):
        g = g.sort_values('date')
        dd = dict(zip(g['date'], g['pctChg']))
        out.append({
            'name': info.get(code, {}).get('name', code),
            'cum': _compound([dd.get(d, 0) for d in win]),
        })
    out.sort(key=lambda x: x['cum'], reverse=True)
    return out


def analyze(window=10, lookback=45, as_of=None, with_leaders=False, leader_top=5):
    """可被其他脚本导入：返回结构化轮动结果。只读连接。
    with_leaders=True 时给走强三级附上主板可买龙头(读 sw_industry_member + kline_daily)。"""
    conn = get_connection(readonly=True)
    info, l2_parent, l3_codes, l1_codes = _load_info(conn)
    if not l3_codes:
        conn.close()
        return {'error': 'sw_industry_info 为空，请先运行 _fetch_sw_industry.py'}
    start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
    df = _load_daily(conn, l3_codes, start_date, end_date=as_of)
    if df.empty:
        conn.close()
        return {'error': 'sw_industry_daily 为空，请先运行 _fetch_sw_industry.py --levels 1,3'}
    metrics, win = _build_metrics(df, window, info, l2_parent)
    l1_ctx = _load_l1_context(conn, l1_codes, info, window)
    if with_leaders and metrics:
        today = _latest_kline_date(conn) or win[-1]
        strong_stages = ('持续领涨', '加速轮入', '稳步抬升', '新晋活跃')
        for m in metrics:
            if m['stage'] in strong_stages:
                m['leaders'] = _load_leaders(conn, m['code'], win[0], win[-1], today, top_n=leader_top)
        r_today = today
    else:
        r_today = None
    conn.close()
    if not metrics:
        return {'error': '三级行业日K不足，至少需要4个交易日'}
    return {
        'win_start': win[0], 'win_end': win[-1], 'window': len(win),
        'stock_today': r_today,
        'l3_count': len(metrics),
        'l3_total': len(l3_codes),
        'l3_stale': len(l3_codes) - len(metrics),
        'l1_context': l1_ctx,
        'top': metrics,
        'persist': [m for m in metrics if m['stage'] == '持续领涨'],
        'accel': [m for m in metrics if m['stage'] == '加速轮入'],
        'steady': [m for m in metrics if m['stage'] == '稳步抬升'],
        'fresh': [m for m in metrics if m['stage'] == '新晋活跃'],
        'fade': sorted([m for m in metrics if m['stage'] == '高位退潮'], key=lambda x: x['cumB']),
    }


def _print_section(title, items, n, note=None):
    print()
    print("▓" * 92)
    print(f"  {title}")
    print("▓" * 92)
    if not items:
        print("  （暂无）")
        return
    print(f"  {'#':>2s} {'三级行业':<12s}{'一级':<8s}{'两周%':>7s}{'A周%':>7s}{'B周%':>7s}"
          f"{'前20%':>7s}{'A/B':>7s}{'量能':>6s} {'档':<6s}")
    print("-" * 92)
    for i, m in enumerate(items[:n], 1):
        print(f"  {i:>2d} {m['name'][:11]:<12s}{str(m['l1'])[:7]:<8s}"
              f"{m['cum10']:>6.1f}%{m['cumA']:>6.1f}%{m['cumB']:>6.1f}%"
              f"{m['top20_full']:>4d}/{m['window']:<2d}"
              f"{m['top20_A']:>3d}/{m['top20_B']:<2d}{m['amt_ratio']:>5.2f}x {m['stage']:<6s}")
    if note:
        print(f"\n  {note}")


def main():
    parser = argparse.ArgumentParser(description='申万三级行业两周轮动分析')
    parser.add_argument('--window', type=int, default=10, help='轮动窗口交易日数，默认10(两周)')
    parser.add_argument('--lookback', type=int, default=45, help='读取最近多少自然日日K作上下文，默认45')
    parser.add_argument('--top', type=int, default=18, help='各榜单最多输出多少个')
    parser.add_argument('--as-of', default=None, help='只分析到指定日期 YYYY-MM-DD')
    parser.add_argument('--no-leaders', action='store_true', help='不展示主板龙头(跳过 kline join，更快)')
    args = parser.parse_args()

    r = analyze(window=args.window, lookback=args.lookback, as_of=args.as_of,
                with_leaders=not args.no_leaders)
    if 'error' in r:
        print(r['error'])
        return 1

    print("=" * 92)
    print("  申万三级行业 · 两周轮动雷达")
    print("=" * 92)
    print(f"  窗口: {r['win_start']} ~ {r['win_end']}  ({r['window']}个交易日)  "
          f"参与三级行业: {r['l3_count']}/{r['l3_total']}"
          + (f"（{r['l3_stale']}个申万旧代码已停更，剔除）" if r.get('l3_stale') else ""))
    if r['l1_context']:
        top_l1 = "、".join(f"{x['name']}{x['cum']:+.1f}%" for x in r['l1_context'][:6])
        bot_l1 = "、".join(f"{x['name']}{x['cum']:+.1f}%" for x in r['l1_context'][-4:])
        print(f"  一级强→弱: {top_l1}  ...  垫底: {bot_l1}")

    _print_section("① 持续强势领涨（两周多数日前20%，主线级别）", r['persist'], args.top)
    _print_section("② 加速轮入 / 接力走强（后一周排名跃升，资金正在切入）", r['accel'], args.top)
    _print_section("③ 稳步抬升（累计涨幅居前、多数日收红，温和走强）", r['steady'], min(args.top, 12))
    _print_section("④ 高位退潮 / 走弱（前强后弱，减仓回避方向）", r['fade'], min(args.top, 12))

    print()
    print("=" * 92)
    print("  ★ 两周『轮动走强』综合总榜 TOP20  (供选股定方向)")
    print("-" * 92)
    strong = [m for m in r['top'] if m['stage'] in ('持续领涨', '加速轮入', '稳步抬升', '新晋活跃')]
    for i, m in enumerate(strong[:20], 1):
        print(f"  {i:>2d}. {m['name'][:12]:<13s}[{m['stage']:<6s}] 一级:{str(m['l1'])[:6]:<7s} "
              f"两周{m['cum10']:+5.1f}% B周{m['cumB']:+5.1f}% 前20%={m['top20_full']}/{m['window']}天 量能{m['amt_ratio']:.2f}x")
    print("=" * 92)

    if not args.no_leaders:
        _print_leaders(strong, r.get('stock_today'), r['win_start'], r['win_end'])

    print("  用法: ①②优先做低吸主线龙头; ③温和建仓型可跟; ④减仓回避。三级定方向,主板龙头(★)才是你能下单的票。")
    print("=" * 92)
    return 0


def _print_leaders(strong, today, win_start, win_end, n_industries=12, per=5):
    """走强三级的主板可买龙头：★=主板00/60可买  ○=创业/科创/北交你买不了。"""
    print()
    print("=" * 92)
    print(f"  ★ 走强三级 · 龙头股（★主板可买 ○买不了; 两周={win_start}~{win_end}  今={today or '-'}）")
    print("-" * 92)
    shown = 0
    for m in strong:
        leaders = m.get('leaders') or []
        if not leaders:
            continue
        mb = [l for l in leaders if l['mb']]
        tag = '' if mb else ' ⚠️无主板可买龙头'
        cells = []
        for l in leaders:
            mark = '★' if l['mb'] else '○'
            t = f"{l['today']:+.0f}%" if l['today'] == l['today'] else " -"
            cells.append(f"{mark}{l['name'][:5]}(两周{l['chg2w']:+.0f}% 今{t})")
        print(f"  【{m['name'][:10]}·{m['stage']}】{tag}")
        print(f"      " + "  ".join(cells))
        shown += 1
        if shown >= n_industries:
            break
    if shown == 0:
        print("  （成分表为空，请先运行 _fetch_sw_industry.py --members）")
    print("=" * 92)


if __name__ == '__main__':
    raise SystemExit(main())
