#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产业链主线家族雷达

把零散概念按产业链归到“家族”，用 20 日持续性识别真正的主线，
并在家族内按当日轮动指出该买哪个细分的前排。解决“只看今天主线、
跑出偶发轮动方向”的问题：即使半导体链今天回调，只要它 20 日持续
强势，依然把它当主线，在它的细分（液冷/铜箔/封测/存储/光互连…）里按轮动选票。

家族映射文件：同目录 theme_family_map.json（人工固化，可用 --draft 出聚类草稿辅助维护）。

用法：
  python 工具脚本/20_市场环境/theme_family.py                # 产业链主线家族雷达
  python 工具脚本/20_市场环境/theme_family.py --as-of 2026-06-23
  python 工具脚本/20_市场环境/theme_family.py --draft        # 成分股重叠自动聚类草稿(维护映射用)

依赖：concept_daily / concept_member（由 10_数据更新/_fetch_concept_data.py 维护）
"""
import argparse
import json
import math
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
import concept_rotation as cr
import numpy as np

MAP_PATH = Path(__file__).resolve().parent / "theme_family_map.json"
TOP_TIER = 0.20      # 概念当日处于前20%算“强”
PERSIST_WINDOW = 20  # 持续性观察窗口(交易日)


# ─────────────────────────── 家族映射加载 ───────────────────────────

def load_family_map():
    if not MAP_PATH.exists():
        return {}, {}
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return data.get('families', {}), data.get('_meta', {})


def _resolve_members(cfg, all_names):
    """把家族配置解析为当前概念池里实际存在的成员概念列表。"""
    present = []
    for c in cfg.get('concepts', []):
        if c in all_names and c not in present:
            present.append(c)
    for kw in cfg.get('keywords', []):
        for name in all_names:
            if kw in name and name not in present:
                present.append(name)
    return present


# ─────────────────────────── 家族持续性分析 ───────────────────────────

def _classify_family(active_days, active_last5, active_prev, strong_today_n, member_n, avg_pct_today):
    if active_days >= 10:
        return '持续主线'
    if active_days >= 6:
        return '阶段主线'
    if strong_today_n >= max(2, math.ceil(member_n * 0.25)) and active_days < 6:
        return '轮动活跃'
    if active_prev >= 4 and active_last5 == 0 and avg_pct_today < 0:
        return '退潮中'
    return '普通'


def analyze_families(days=120, as_of=None, source='auto'):
    """返回产业链家族持续性结构。纯只读分析，不建表。"""
    families, meta = load_family_map()
    if not families:
        return {'error': f'未找到家族映射文件 {MAP_PATH.name}'}

    conn = get_connection(readonly=True)
    cr.SOURCE = cr._resolve_source(conn, source)
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    df = cr._load_concept_daily(conn, start_date, end_date=as_of)
    if df.empty:
        conn.close()
        return {'error': 'concept_daily 为空，请先运行 _fetch_concept_data.py'}

    daily_rank, daily_count = cr._compute_daily_ranks(df)
    metrics, latest_date, all_dates = cr._build_metrics(df)
    if not metrics:
        conn.close()
        return {'error': '概念历史数据不足，至少需要5个交易日'}

    metric_by_name = {m['concept_name']: m for m in metrics}
    all_names = list(metric_by_name.keys())
    window = all_dates[-PERSIST_WINDOW:]
    last5 = window[-5:]
    prev = window[:-5]
    today = all_dates[-1]

    def _rank_pct(concept, d):
        return daily_rank.get((concept, d), daily_count.get(d, 1)) / max(daily_count.get(d, 1), 1)

    results = []
    for fam, cfg in families.items():
        present = _resolve_members(cfg, all_names)
        if not present:
            continue
        n = len(present)
        need = max(2, math.ceil(n * 0.20))

        def _active_on(d):
            return sum(1 for c in present if _rank_pct(c, d) <= TOP_TIER) >= need

        active_days = sum(1 for d in window if _active_on(d))
        active_last5 = sum(1 for d in last5 if _active_on(d))
        active_prev = sum(1 for d in prev if _active_on(d))

        members = sorted(present, key=lambda c: metric_by_name[c]['rank_pct_today'])
        strong_today = [c for c in present if metric_by_name[c]['rank_pct_today'] <= 0.25]
        avg_pct_today = float(np.mean([metric_by_name[c]['pct_today'] for c in present]))
        avg_pct5 = float(np.mean([metric_by_name[c]['pct5'] for c in present]))
        avg_pct20 = float(np.mean([metric_by_name[c]['pct20'] for c in present]))
        avg_persist = float(np.mean([metric_by_name[c]['persist20'] for c in present]))
        best_score = max(metric_by_name[c]['score'] for c in present)
        leader = members[0]

        status = _classify_family(active_days, active_last5, active_prev,
                                  len(strong_today), n, avg_pct_today)
        results.append({
            'family': fam,
            'note': cfg.get('note', ''),
            'status': status,
            'member_count': n,
            'members_sorted': members,
            'active_days20': active_days,
            'active_last5': active_last5,
            'strong_today': len(strong_today),
            'avg_pct_today': avg_pct_today,
            'avg_pct5': avg_pct5,
            'avg_pct20': avg_pct20,
            'avg_persist20': avg_persist,
            'best_score': round(best_score, 1),
            'leader_concept': leader,
            'leader_stage': metric_by_name[leader]['stage'],
        })

    status_rank = {'持续主线': 0, '阶段主线': 1, '轮动活跃': 2, '退潮中': 4, '普通': 3}
    results.sort(key=lambda x: (status_rank.get(x['status'], 9), -x['active_days20'], -x['best_score']))
    out = {
        'latest_date': latest_date,
        'today': today,
        'source': cr.SOURCE,
        'families': results,
        'metric_by_name': metric_by_name,
    }
    conn.close()
    return out


def code_to_family(days=120, as_of=None, source='auto', statuses=('持续主线', '阶段主线')):
    """给 offline_screener 用：返回 code → 最强家族信息（仅持续/阶段主线家族）。"""
    res = analyze_families(days=days, as_of=as_of, source=source)
    if res.get('error'):
        return {}, res['error']
    fam_results = [f for f in res['families'] if f['status'] in statuses]
    if not fam_results:
        return {}, None

    conn = get_connection(readonly=True)
    rows = conn.execute(
        "SELECT concept_name, code FROM concept_member WHERE source=?", (cr.SOURCE,)
    ).fetchall()
    conn.close()
    members_by_concept = {}
    for cn, code in rows:
        members_by_concept.setdefault(cn, []).append(code)

    code_fam = {}
    for fam in fam_results:
        concepts = set(fam['members_sorted'])
        for cn in concepts:
            for code in members_by_concept.get(cn, []):
                info = {
                    'family': fam['family'],
                    'family_status': fam['status'],
                    'family_active_days20': fam['active_days20'],
                    'family_leader_concept': fam['leader_concept'],
                }
                old = code_fam.get(code)
                if old is None or fam['active_days20'] > old['family_active_days20']:
                    code_fam[code] = info
    return code_fam, None


# ─────────────────────────── 自动聚类草稿(维护用) ───────────────────────────

def auto_cluster_draft(source='auto', threshold=0.30, min_overlap=5, top=40):
    conn = get_connection(readonly=True)
    src = cr._resolve_source(conn, source)
    rows = conn.execute(
        "SELECT concept_name, code FROM concept_member WHERE source=?", (src,)
    ).fetchall()
    conn.close()
    sets = {}
    for cn, code in rows:
        sets.setdefault(cn, set()).add(code)
    names = [n for n in sets if len(sets[n]) >= 3]

    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    edges = []
    for i in range(len(names)):
        a = names[i]
        sa = sets[a]
        for j in range(i + 1, len(names)):
            b = names[j]
            sb = sets[b]
            inter = len(sa & sb)
            if inter < min_overlap:
                continue
            jac = inter / len(sa | sb)
            if jac >= threshold:
                edges.append((jac, a, b))
                union(a, b)

    clusters = {}
    for n in names:
        clusters.setdefault(find(n), []).append(n)
    clusters = {k: v for k, v in clusters.items() if len(v) >= 2}

    print("=" * 80)
    print("  产业链家族·成分股重叠自动聚类草稿")
    print("=" * 80)
    print(f"  数据源:{src}  阈值 Jaccard>={threshold}  最小重叠>={min_overlap}只  概念数:{len(names)}")
    print("  说明：这是辅助维护用的草稿，请人工核对后增删到 theme_family_map.json")
    ordered = sorted(clusters.values(), key=len, reverse=True)[:top]
    for idx, group in enumerate(ordered, 1):
        group_sorted = sorted(group, key=lambda n: len(sets[n]), reverse=True)
        print(f"\n  簇{idx:>2d}（{len(group)}个概念）: " + "、".join(group_sorted))
    print("\n" + "=" * 80)
    return 0


# ─────────────────────────── 持仓家族归属 ───────────────────────────

def _norm_code(code):
    """取6位数字部分，兼容 600000 / sh.600000 / sh600000。"""
    digits = ''.join(ch for ch in str(code) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def print_codes_family(codes, days=120, as_of=None, source='auto'):
    """把一组持仓代码归到产业链家族（含所有家族，不止持续/阶段主线）。"""
    res = analyze_families(days=days, as_of=as_of, source=source)
    if res.get('error'):
        print(f"  错误：{res['error']}")
        return 1
    # 全量家族（含轮动活跃/退潮中/普通），按6位代码建索引
    code_fam, _ = code_to_family(
        days=days, as_of=as_of, source=source,
        statuses=('持续主线', '阶段主线', '轮动活跃', '退潮中', '普通'),
    )
    by6 = {}
    for code, info in code_fam.items():
        by6.setdefault(_norm_code(code), info)

    print("=" * 80)
    print("  持仓 → 产业链家族归属")
    print("=" * 80)
    as_of_txt = as_of or res.get('latest_date', '最新')
    print(f"  截至 {as_of_txt}  数据源 {cr.SOURCE}")
    print("-" * 80)
    for raw in codes:
        c6 = _norm_code(raw)
        info = by6.get(c6)
        if info is None:
            print(f"  {raw:<10} → 未归入任何已配置家族（不在 theme_family_map.json 概念成分内）")
        else:
            print(f"  {raw:<10} → {info['family']} · {info['family_status']} "
                  f"· 活跃{info['family_active_days20']}/20 · 领涨细分 {info['family_leader_concept']}")
    print("=" * 80)
    return 0


# ─────────────────────────── CLI ───────────────────────────

def _print_radar(res, top=15):
    if res.get('error'):
        print(res['error'])
        return 1
    print("=" * 96)
    print("  产业链主线家族雷达（20日持续性）")
    print("=" * 96)
    print(f"  数据源:{res['source']}  数据日期:{res['latest_date']}  持续性窗口:{PERSIST_WINDOW}个交易日")
    print(f"  口径: 家族内有≥20%成员概念当日处于前20% 记为该家族‘活跃日’，统计近{PERSIST_WINDOW}日活跃天数")
    print("-" * 96)
    print(f"  {'家族':<14s} {'状态':<8s} {'持续':>7s} {'今强':>5s} {'今涨':>7s} {'5日':>7s} {'20日':>7s}  今日领涨细分 / 前排")
    print("-" * 96)
    conn = get_connection(readonly=True)
    metric_by_name = res['metric_by_name']
    for fam in res['families'][:top]:
        leader = fam['leader_concept']
        lm = metric_by_name.get(leader, {})
        leaders = cr._load_leaders(conn, leader, res['latest_date'], top_n=2)
        leader_stocks = '、'.join(f"{l['name'] or l['code']}({l['tier']})" for l in leaders) or '-'
        print(
            f"  {fam['family'][:14]:<14s} {fam['status']:<8s} {fam['active_days20']:>2d}/{PERSIST_WINDOW:<2d}  "
            f"{fam['strong_today']:>2d}/{fam['member_count']:<2d} "
            f"{cr._fmt_pct(fam['avg_pct_today']):>7s} {cr._fmt_pct(fam['avg_pct5']):>7s} {cr._fmt_pct(fam['avg_pct20']):>7s}  "
            f"{leader}({lm.get('stage','')}) → {leader_stocks}"
        )
    conn.close()
    print("\n" + "=" * 96)
    print("  使用方式")
    print("=" * 96)
    print("  1. 优先在‘持续主线/阶段主线’家族里选票；这才是产业链主线，回调日也在这里找。")
    print("  2. 家族今日回调（今涨为负）但持续天数高 → 是低吸机会，不是退潮，按家族内今日领涨细分选前排。")
    print("  3. ‘轮动活跃’是刚轮到的方向，只快进快出；‘退潮中’家族即使个股形态好也回避。")
    print("  4. 同一家族内按‘今日领涨细分’轮动换票，强确定性主线成员可中长期持有(见中长期策略)。")
    print("=" * 96)
    return 0


def main():
    parser = argparse.ArgumentParser(description='产业链主线家族雷达')
    parser.add_argument('--days', type=int, default=120, help='加载最近多少自然日概念历史')
    parser.add_argument('--top', type=int, default=15, help='最多输出多少个家族')
    parser.add_argument('--as-of', default=None, help='只分析到指定日期 YYYY-MM-DD')
    parser.add_argument('--source', choices=['auto', 'eastmoney', 'ths'], default='auto')
    parser.add_argument('--draft', action='store_true', help='输出成分股重叠自动聚类草稿(维护映射用)')
    parser.add_argument('--codes', nargs='+', default=None,
                        help='查询持仓代码的家族归属，如 --codes 600000 000001')
    args = parser.parse_args()

    if args.draft:
        return auto_cluster_draft(source=args.source)

    if args.codes:
        return print_codes_family(args.codes, days=args.days, as_of=args.as_of, source=args.source)

    res = analyze_families(days=args.days, as_of=args.as_of, source=args.source)
    return _print_radar(res, top=args.top)


if __name__ == '__main__':
    raise SystemExit(main())
