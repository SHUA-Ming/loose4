#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富三级行业 两周轮动分析。

统一板块口径：
  - em_board_l1/l2/l3            东方财富一级/二级/三级行业树
  - em_stock_board_l3            个股唯一绑定到三级行业
  - em_board_daily               三层行业指数日K

本脚本只读连接，不建表、不写库；可独立运行，也可被 market_mode/offline_screener 导入。
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
warnings.filterwarnings("ignore")

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_connection
import numpy as np
import pandas as pd


STRONG_STAGES = ("持续主线", "上升轮动", "强势板块", "新晋活跃")

TOMORROW_PRIORITY = "\u660e\u65e5\u4f18\u5148"
TOMORROW_DOWNGRADE = "\u964d\u6743\u884c\u4e1a"
TOMORROW_BAN = "\u7981\u5165\u884c\u4e1a"
TOMORROW_WATCH = "\u89c2\u5bdf\u5f85\u5b9a"
STAGE_PERSIST = "\u6301\u7eed\u4e3b\u7ebf"
STAGE_ACCEL = "\u4e0a\u5347\u8f6e\u52a8"
STAGE_STEADY = "\u5f3a\u52bf\u677f\u5757"
STAGE_FRESH = "\u65b0\u664b\u6d3b\u8dc3"
STAGE_FADE = "\u9000\u6f6e"
STAGE_NORMAL = "\u666e\u901a\u8f6e\u52a8"


def _compound(pcts):
    value = 1.0
    for pct in pcts:
        value *= 1 + (pct or 0) / 100
    return (value - 1) * 100


def _load_info(conn):
    l1_rows = conn.execute(
        "SELECT board_code, board_name FROM em_board_l1 ORDER BY board_code"
    ).fetchall()
    l3_rows = conn.execute("""
        SELECT l3.board_code, l3.board_name, l2.board_name, l1.board_name
        FROM em_board_l3 l3
        JOIN em_board_l2 l2 ON l3.l2_id = l2.id
        JOIN em_board_l1 l1 ON l3.l1_id = l1.id
        ORDER BY l3.board_code
    """).fetchall()
    info = {}
    for code, name in l1_rows:
        info[code] = {"name": name, "level": 1, "l1": name, "l2": ""}
    for code, name, l2, l1 in l3_rows:
        info[code] = {"name": name, "level": 3, "l1": l1, "l2": l2}
    return info, [r[0] for r in l3_rows], [r[0] for r in l1_rows]


def _load_daily(conn, codes, start_date, end_date=None):
    if not codes:
        return pd.DataFrame()
    frames = []
    for i in range(0, len(codes), 300):
        part = codes[i:i + 300]
        ph = ",".join(["?"] * len(part))
        sql = (
            f"SELECT board_code AS code,date,close,amount,pctChg "
            f"FROM em_board_daily WHERE board_code IN ({ph}) AND date >= ?"
        )
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
    df["date"] = df["date"].astype(str)
    for col in ("close", "amount", "pctChg"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _build_metrics(df, window, info):
    all_dates = sorted(df["date"].unique())
    if len(all_dates) < window:
        window = len(all_dates)
    if window < 4:
        return [], all_dates

    win = all_dates[-window:]
    half = window // 2
    wa, wb = win[:half], win[half:]
    recent20 = all_dates[-20:]

    rank_pct = {}
    for date, grp in df.groupby("date"):
        grp = grp.dropna(subset=["pctChg"]).sort_values("pctChg", ascending=False).reset_index(drop=True)
        n = len(grp)
        for i, row in grp.iterrows():
            rank_pct[(row["code"], date)] = (i + 1) / max(n, 1)

    metrics = []
    for code, grp in df.groupby("code"):
        grp = grp.sort_values("date")
        dd = dict(zip(grp["date"], grp["pctChg"]))
        am = dict(zip(grp["date"], grp["amount"]))
        present = sum(date in dd for date in win)
        if present < window - 1:
            continue
        pw = [dd.get(date, 0) for date in win]
        rp_full = [rank_pct.get((code, date), 1.0) for date in win]
        rp_a = [rank_pct.get((code, date), 1.0) for date in wa]
        rp_b = [rank_pct.get((code, date), 1.0) for date in wb]
        rp20 = [rank_pct.get((code, date), 1.0) for date in recent20 if (code, date) in rank_pct]
        am_a = np.mean([am.get(date, 0) or 0 for date in wa]) if wa else 0
        am_b = np.mean([am.get(date, 0) or 0 for date in wb]) if wb else 0
        meta = info.get(code, {})
        metrics.append({
            "code": code,
            "name": meta.get("name", code),
            "l1": meta.get("l1", "-"),
            "l2": meta.get("l2", "-"),
            "cum10": _compound(pw),
            "cumA": _compound([dd.get(date, 0) for date in wa]),
            "cumB": _compound([dd.get(date, 0) for date in wb]),
            "avg_rp": float(np.mean(rp_full)),
            "avg_rpA": float(np.mean(rp_a)) if rp_a else 1.0,
            "avg_rpB": float(np.mean(rp_b)) if rp_b else 1.0,
            "top20_full": int(sum(r <= 0.20 for r in rp_full)),
            "top20_A": int(sum(r <= 0.20 for r in rp_a)),
            "top20_B": int(sum(r <= 0.20 for r in rp_b)),
            "top20_20d": int(sum(r <= 0.20 for r in rp20)),
            "up_days": int(sum((pct or 0) > 0 for pct in pw)),
            "rank_improve": float(np.mean(rp_a) - np.mean(rp_b)) if (rp_a and rp_b) else 0.0,
            "amt_ratio": float(am_b / am_a) if am_a and am_a > 0 else 1.0,
            "last_rp": rp_full[-1],
            "window": window,
            "win_start": win[0],
            "win_end": win[-1],
        })

    for m in metrics:
        m["stage"] = _classify(m, window)
        m["score"] = _score(m)
        bucket, reason, bonus = _tomorrow_plan(m)
        m["tomorrow_bucket"] = bucket
        m["tomorrow_reason"] = reason
        m["tomorrow_bonus"] = bonus
    metrics.sort(key=lambda x: x["score"], reverse=True)
    return metrics, win


def _classify(m, window):
    strong_full = max(3, round(window * 0.5))
    if m["top20_A"] >= 2 and (m["avg_rpB"] - m["avg_rpA"]) >= 0.15 and m["cumB"] < 0:
        return "退潮"
    if m["top20_full"] >= strong_full and m["cum10"] > 0:
        return "持续主线"
    if m["avg_rpB"] <= 0.33 and m["rank_improve"] >= 0.10 and m["cumB"] > 1 and m["top20_B"] >= 2:
        return "上升轮动"
    if m["cum10"] >= 5 and m["up_days"] >= max(4, round(window * 0.6)) and m["cumB"] >= 0:
        return "强势板块"
    if m["last_rp"] <= 0.15 and m["cumB"] > 0:
        return "新晋活跃"
    return "普通轮动"


def _score(m):
    score = 0.0
    score += m["top20_full"] * 6
    score += min(max(m["cum10"], -10), 30) * 0.7
    score += min(max(m["cumB"], -10), 25) * 0.6
    score += max(m["rank_improve"], 0) * 30
    score += min(max(m["amt_ratio"] - 1, 0), 2) * 5
    score += m["top20_20d"] * 0.8
    if m["stage"] == "退潮":
        score -= 25
    elif m["stage"] == "持续主线":
        score += 7
    elif m["stage"] == "上升轮动":
        score += 5
    return round(score, 1)


def _tomorrow_plan(m):
    """Map rotation stage into next-day execution buckets.

    The bucket is intentionally conservative:
    - priority: enough continuity or fresh acceleration to justify stock selection first
    - downgrade: can be watched, but should not win tie-breaks by itself
    - ban: trend has faded enough that technical stock signals should not override it
    """
    stage = m.get("stage")
    cum_b = m.get("cumB", 0) or 0
    cum10 = m.get("cum10", 0) or 0
    avg_rpb = m.get("avg_rpB", 1.0) or 1.0
    last_rp = m.get("last_rp", 1.0) or 1.0
    top20_b = m.get("top20_B", 0) or 0
    rank_improve = m.get("rank_improve", 0) or 0
    amt_ratio = m.get("amt_ratio", 1.0) or 1.0

    if stage == STAGE_FADE:
        return TOMORROW_BAN, "\u5df2\u8fdb\u5165\u9000\u6f6e\uff0c\u524d\u5f3a\u540e\u5f31", -99
    if cum_b <= -5 and avg_rpb >= 0.60:
        return TOMORROW_BAN, "\u540e\u534a\u7a0b\u8dcc\u5e45\u5927\u4e14\u6392\u540d\u9760\u540e", -99
    if last_rp >= 0.80 and cum_b < 0:
        return TOMORROW_BAN, "\u6700\u65b0\u6392\u540d\u843d\u5230\u540e20%\u4e14\u540e\u534a\u7a0b\u8d70\u5f31", -99

    overheated = (cum_b >= 20 and amt_ratio >= 1.40) or cum10 >= 30
    if stage in (STAGE_PERSIST, STAGE_ACCEL):
        if overheated:
            return TOMORROW_DOWNGRADE, "\u8d70\u5f3a\u4f46\u77ed\u7ebf\u6da8\u5e45/\u91cf\u80fd\u504f\u70ed\uff0c\u53ea\u770b\u56de\u8e29", -1
        if cum_b > 0 and (top20_b >= 2 or last_rp <= 0.35):
            return TOMORROW_PRIORITY, "\u8fde\u7eed\u6027/\u8d44\u91d1\u5207\u5165\u786e\u8ba4", 1
        return TOMORROW_DOWNGRADE, "\u9636\u6bb5\u5f3a\u4f46\u8fd1\u534a\u7a0b\u786e\u8ba4\u4e0d\u8db3", -1

    if stage == STAGE_STEADY:
        if overheated:
            return TOMORROW_DOWNGRADE, "\u5f3a\u52bf\u4f46\u5df2\u6709\u8fc7\u70ed\u8ff9\u8c61", -1
        if cum_b >= 1 and last_rp <= 0.40:
            return TOMORROW_PRIORITY, "\u5f3a\u52bf\u4ecd\u5728\u5ef6\u7eed", 1
        return TOMORROW_DOWNGRADE, "\u5f3a\u52bf\u4e0d\u7a33\uff0c\u7b49\u4e2a\u80a1\u5f3a\u786e\u8ba4", -1

    if stage == STAGE_FRESH:
        if rank_improve >= 0.12 and cum_b > 2 and last_rp <= 0.20:
            return TOMORROW_PRIORITY, "\u65b0\u664b\u6d3b\u8dc3\u4e14\u6392\u540d\u660e\u663e\u4e0a\u79fb", 1
        return TOMORROW_DOWNGRADE, "\u65b0\u664b\u6d3b\u8dc3\u9700\u7b49\u6301\u7eed\u6027", -1

    return TOMORROW_DOWNGRADE, "\u666e\u901a\u8f6e\u52a8\uff0c\u4e0d\u4f5c\u9009\u80a1\u4e3b\u653b\u65b9\u5411", -1


def _build_tomorrow_buckets(metrics):
    priority = [m for m in metrics if m.get("tomorrow_bucket") == TOMORROW_PRIORITY]
    downgrade = [m for m in metrics if m.get("tomorrow_bucket") == TOMORROW_DOWNGRADE]
    ban = [m for m in metrics if m.get("tomorrow_bucket") == TOMORROW_BAN]
    priority.sort(key=lambda x: (-x["score"], x["avg_rpB"], -x["cumB"]))
    downgrade.sort(key=lambda x: (-x["score"], x["avg_rpB"], -x["cumB"]))
    ban.sort(key=lambda x: (x["cumB"], -x["last_rp"], x["score"]))
    return priority, downgrade, ban


def _is_mainboard(code):
    return str(code).startswith("sh.60") or str(code).startswith("sz.00")


def _latest_kline_date(conn):
    row = conn.execute(
        "SELECT MAX(date) FROM kline_daily WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'"
    ).fetchone()
    return row[0] if row and row[0] else None


def _load_leaders(conn, l3_code, win_start, win_end, today, top_n=5):
    mem = conn.execute("""
        SELECT s.code, s.code_name
        FROM em_stock_board_l3 s
        JOIN em_board_l3 l3 ON s.l3_id = l3.id
        WHERE l3.board_code = ?
    """, (l3_code,)).fetchall()
    codes = [row[0] for row in mem]
    name_map = {row[0]: row[1] for row in mem}
    if not codes:
        return []

    out = []
    for i in range(0, len(codes), 300):
        part = codes[i:i + 300]
        ph = ",".join(["?"] * len(part))
        df = pd.read_sql_query(
            f"SELECT code,date,close,pctChg FROM kline_daily "
            f"WHERE code IN ({ph}) AND date >= ? AND date <= ?",
            conn,
            params=part + [win_start, today],
        )
        if df.empty:
            continue
        df["date"] = df["date"].astype(str)
        for col in ("close", "pctChg"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for code, grp in df.groupby("code"):
            grp = grp.sort_values("date").dropna(subset=["close"])
            in_win = grp[grp["date"] <= win_end]
            if len(in_win) < 2 or in_win.iloc[0]["close"] <= 0:
                continue
            chg2w = (in_win.iloc[-1]["close"] / in_win.iloc[0]["close"] - 1) * 100
            trow = grp[grp["date"] == str(today)]["pctChg"]
            today_pct = float(trow.iloc[0]) if len(trow) else float("nan")
            out.append({
                "code": code,
                "name": name_map.get(code, ""),
                "chg2w": chg2w,
                "today": today_pct,
                "mb": _is_mainboard(code),
            })
    out.sort(key=lambda x: x["chg2w"], reverse=True)
    return out[:top_n]


def _load_l1_context(conn, l1_codes, info, window):
    df = _load_daily(conn, l1_codes, (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d"))
    if df.empty:
        return []
    dates = sorted(df["date"].unique())
    win = dates[-window:] if len(dates) >= window else dates
    out = []
    for code, grp in df.groupby("code"):
        grp = grp.sort_values("date")
        dd = dict(zip(grp["date"], grp["pctChg"]))
        out.append({
            "code": code,
            "name": info.get(code, {}).get("name", code),
            "cum": _compound([dd.get(date, 0) for date in win]),
        })
    out.sort(key=lambda x: x["cum"], reverse=True)
    return out


def analyze(window=10, lookback=45, as_of=None, with_leaders=False, leader_top=5):
    conn = get_connection(readonly=True)
    info, l3_codes, l1_codes = _load_info(conn)
    if not l3_codes:
        conn.close()
        return {"error": "em_board_l3 为空，请先运行 _fetch_em_board_hierarchy.py"}

    start_date = (datetime.now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
    df = _load_daily(conn, l3_codes, start_date, end_date=as_of)
    if df.empty:
        conn.close()
        return {"error": "em_board_daily 为空，请先运行 _fetch_em_board_daily.py --days 60"}

    metrics, win = _build_metrics(df, window, info)
    l1_ctx = _load_l1_context(conn, l1_codes, info, window)
    tomorrow_priority, tomorrow_downgrade, tomorrow_ban = _build_tomorrow_buckets(metrics)

    if with_leaders and metrics:
        today = _latest_kline_date(conn) or win[-1]
        for m in metrics:
            if m["stage"] in STRONG_STAGES:
                m["leaders"] = _load_leaders(conn, m["code"], win[0], win[-1], today, top_n=leader_top)
        stock_today = today
    else:
        stock_today = None
    conn.close()

    if not metrics:
        return {"error": "东方财富三级行业日K不足，至少需要4个交易日"}
    return {
        "win_start": win[0],
        "win_end": win[-1],
        "window": len(win),
        "stock_today": stock_today,
        "l3_count": len(metrics),
        "l3_total": len(l3_codes),
        "l3_stale": len(l3_codes) - len(metrics),
        "l1_context": l1_ctx,
        "top": metrics,
        "tomorrow_priority": tomorrow_priority,
        "tomorrow_downgrade": tomorrow_downgrade,
        "tomorrow_ban": tomorrow_ban,
        "persist": [m for m in metrics if m["stage"] == "持续主线"],
        "accel": [m for m in metrics if m["stage"] == "上升轮动"],
        "steady": [m for m in metrics if m["stage"] == "强势板块"],
        "fresh": [m for m in metrics if m["stage"] == "新晋活跃"],
        "fade": sorted([m for m in metrics if m["stage"] == "退潮"], key=lambda x: x["cumB"]),
    }


def _print_section(title, items, n):
    print()
    print("▓" * 92)
    print(f"  {title}")
    print("▓" * 92)
    if not items:
        print("  （暂无）")
        return
    print(f"  {'#':>2s} {'三级行业':<12s}{'一级':<8s}{'二级':<10s}{'两周%':>7s}{'A周%':>7s}{'B周%':>7s}"
          f"{'前20%':>7s}{'A/B':>7s}{'量能':>6s} {'档':<6s}")
    print("-" * 92)
    for i, m in enumerate(items[:n], 1):
        print(f"  {i:>2d} {m['name'][:11]:<12s}{str(m['l1'])[:7]:<8s}{str(m['l2'])[:9]:<10s}"
              f"{m['cum10']:>6.1f}%{m['cumA']:>6.1f}%{m['cumB']:>6.1f}%"
              f"{m['top20_full']:>4d}/{m['window']:<2d}"
              f"{m['top20_A']:>3d}/{m['top20_B']:<2d}{m['amt_ratio']:>5.2f}x {m['stage']:<6s}")


def _print_tomorrow_plan(result, n):
    print()
    print("=" * 92)
    print("  \u660e\u65e5\u4e09\u7ea7\u884c\u4e1a\u9884\u6848\uff1a\u4f18\u5148 / \u964d\u6743 / \u7981\u5165")
    print("-" * 92)
    sections = [
        ("\u660e\u65e5\u4f18\u5148\u4e09\u7ea7\u884c\u4e1a", result.get("tomorrow_priority", []), n),
        ("\u964d\u6743\u884c\u4e1a", result.get("tomorrow_downgrade", []), min(n, 12)),
        ("\u7981\u5165\u884c\u4e1a", result.get("tomorrow_ban", []), min(n, 12)),
    ]
    for title, items, limit in sections:
        print(f"  {title}:")
        if not items:
            print("    \uff08\u6682\u65e0\uff09")
            continue
        for i, m in enumerate(items[:limit], 1):
            print(
                f"    {i:>2d}. {m['name'][:12]:<13s}"
                f"[{m.get('stage', '-'):<6s}] "
                f"\u4e24\u5468{m['cum10']:+5.1f}% B\u5468{m['cumB']:+5.1f}% "
                f"\u524d20%={m['top20_full']}/{m['window']} "
                f"\u91cf\u80fd{m['amt_ratio']:.2f}x  "
                f"{m.get('tomorrow_reason', '')}"
            )
    print("=" * 92)


def _print_leaders(strong, today, win_start, win_end, n_industries=12, per=5):
    print()
    print("=" * 92)
    print(f"  ★ 走强三级 · 龙头股（★主板可买 ○买不了; 两周={win_start}~{win_end}  今={today or '-'}）")
    print("-" * 92)
    shown = 0
    for m in strong:
        leaders = m.get("leaders") or []
        if not leaders:
            continue
        mb = [x for x in leaders if x["mb"]]
        tag = "" if mb else "  ⚠️无主板可买龙头"
        cells = []
        for item in leaders[:per]:
            mark = "★" if item["mb"] else "○"
            today_txt = f"{item['today']:+.0f}%" if item["today"] == item["today"] else "-"
            cells.append(f"{mark}{item['name'][:5]}(两周{item['chg2w']:+.0f}% 今{today_txt})")
        print(f"  【{m['name'][:10]}·{m['stage']}】{tag}")
        print("      " + "  ".join(cells))
        shown += 1
        if shown >= n_industries:
            break
    if shown == 0:
        print("  （成分桥为空或暂无可计算个股；可加 --no-leaders 只看行业轮动）")
    print("=" * 92)


def main():
    parser = argparse.ArgumentParser(description="东方财富三级行业两周轮动分析")
    parser.add_argument("--window", type=int, default=10, help="轮动窗口交易日数，默认10")
    parser.add_argument("--lookback", type=int, default=45, help="读取最近多少自然日日K，默认45")
    parser.add_argument("--top", type=int, default=18, help="各榜单最多输出多少个")
    parser.add_argument("--as-of", default=None, help="只分析到指定日期 YYYY-MM-DD")
    parser.add_argument("--no-leaders", action="store_true", help="不展示主板龙头")
    args = parser.parse_args()

    result = analyze(
        window=args.window,
        lookback=args.lookback,
        as_of=args.as_of,
        with_leaders=not args.no_leaders,
    )
    if "error" in result:
        print(result["error"])
        return 1

    print("=" * 92)
    print("  东方财富三级行业 · 两周轮动雷达")
    print("=" * 92)
    print(f"  窗口: {result['win_start']} ~ {result['win_end']}  ({result['window']}个交易日)  "
          f"参与三级行业: {result['l3_count']}/{result['l3_total']}")
    if result["l1_context"]:
        top_l1 = "、".join(f"{x['name']}{x['cum']:+.1f}%" for x in result["l1_context"][:6])
        bot_l1 = "、".join(f"{x['name']}{x['cum']:+.1f}%" for x in result["l1_context"][-4:])
        print(f"  一级强→弱: {top_l1}  ...  垫底: {bot_l1}")

    _print_tomorrow_plan(result, args.top)

    _print_section("① 持续主线（两周多数日前20%，主线级别）", result["persist"], args.top)
    _print_section("② 上升轮动 / 接力走强（后一周排名跃升，资金正在切入）", result["accel"], args.top)
    _print_section("③ 强势板块（累计涨幅居前、多数日收红，温和走强）", result["steady"], min(args.top, 12))
    _print_section("④ 退潮 / 走弱（前强后弱，减仓回避方向）", result["fade"], min(args.top, 12))

    print()
    print("=" * 92)
    print("  ★ 两周『轮动走强』综合总榜 TOP20  (供选股定方向)")
    print("-" * 92)
    strong = [m for m in result["top"] if m["stage"] in STRONG_STAGES]
    for i, m in enumerate(strong[:20], 1):
        print(f"  {i:>2d}. {m['name'][:12]:<13s}[{m['stage']:<6s}] "
              f"一级:{str(m['l1'])[:6]:<7s} 二级:{str(m['l2'])[:8]:<9s} "
              f"两周{m['cum10']:+5.1f}% B周{m['cumB']:+5.1f}% "
              f"前20%={m['top20_full']}/{m['window']}天 量能{m['amt_ratio']:.2f}x")
    print("=" * 92)

    if not args.no_leaders:
        _print_leaders(strong, result.get("stock_today"), result["win_start"], result["win_end"])

    print("  用法: 先看①②定主线和接力方向，再回到候选股表看同三级行业内的主板前排。")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
