#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 评分历史查询脚本。

用法：
    # 1. 查某只 ETF 的评分时间序列
    python3 工具脚本/60_候选ETF/etf_score_query.py --code 510880

    # 2. 查最近一次（默认今天）全市场快照，按 grade 分组打印
    python3 工具脚本/60_候选ETF/etf_score_query.py --latest

    # 3. 查"连续 N 次快照都低于阈值"的 ETF（用于半年池淘汰复盘）
    python3 工具脚本/60_候选ETF/etf_score_query.py --low-streak 3 --threshold 72

    # 4. 查指定日期范围的快照（CSV 导出）
    python3 工具脚本/60_候选ETF/etf_score_query.py --start 2026-01-01 --end 2026-05-26 --out 评分历史.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))

import pandas as pd

import db_cache  # type: ignore


def cmd_code(code: str) -> None:
    df = db_cache.read_etf_scores(code=code)
    if df.empty:
        print(f"  无 {code} 的评分历史")
        return
    df = df.sort_values("date", ascending=False)
    show = df[["date", "mode", "category", "grade", "total_score",
               "safety_score", "return_score", "income_score", "execution_score",
               "action", "reason"]]
    print(f"\n  {code} {df.iloc[0]['name']}  评分历史共 {len(df)} 条：")
    print(show.to_string(index=False))


def cmd_latest(date: str | None) -> None:
    target = date or db_cache.get_latest_etf_score_date()
    if target is None:
        print("  库内尚无评分快照")
        return
    target = str(target)[:10]
    df = db_cache.read_etf_scores(start_date=target, end_date=target)
    if df.empty:
        print(f"  {target} 无快照")
        return
    print(f"\n  快照日期：{target}   共 {len(df)} 只ETF")
    print(f"  Grade 分布：{df['grade'].value_counts().to_dict()}")
    print(f"  Mode：{df['mode'].iloc[0]}\n")
    for cat in ("CORE_DIVIDEND", "CORE_BROAD", "DEFENSIVE", "SATELLITE"):
        part = df[df["category"] == cat]
        if part.empty:
            continue
        part = part.sort_values("total_score", ascending=False)
        show = part[["code", "name", "grade", "total_score", "action", "ma_state", "reason"]].head(10)
        print(f"  -- {cat}  ({len(part)} 只) --")
        print(show.to_string(index=False))
        print()


def cmd_low_streak(streak: int, threshold: float, days_window: int | None) -> None:
    """找出最近 streak 次快照中，total_score 都 < threshold 的 ETF。"""
    sql = """
        SELECT code, name, category, date, total_score, grade
        FROM etf_score_history
        ORDER BY code, date DESC
    """
    conn = db_cache.get_connection()
    df = pd.read_sql_query(sql, conn)
    conn.close()
    if df.empty:
        print("  库内无快照")
        return

    results: list[dict] = []
    for code, grp in df.groupby("code"):
        grp = grp.sort_values("date", ascending=False)
        if days_window:
            from datetime import datetime, timedelta
            cutoff = (datetime.today() - timedelta(days=days_window)).strftime("%Y-%m-%d")
            grp = grp[grp["date"].astype(str) >= cutoff]
        recent = grp.head(streak)
        if len(recent) < streak:
            continue  # 历史不够
        if (recent["total_score"] < threshold).all():
            results.append({
                "code": code,
                "name": recent.iloc[0]["name"],
                "category": recent.iloc[0]["category"],
                "recent_dates": ",".join(str(d)[:10] for d in recent["date"]),
                "scores": ",".join(f"{s:.0f}" for s in recent["total_score"]),
                "last_grade": recent.iloc[0]["grade"],
            })

    if not results:
        print(f"  无 ETF 连续 {streak} 次快照都低于 {threshold} 分")
        return
    out = pd.DataFrame(results).sort_values(["category", "code"])
    print(f"\n  连续 {streak} 次快照 total_score < {threshold} 的 ETF（共 {len(out)} 只）：")
    print(out.to_string(index=False))


def cmd_range(start: str, end: str | None, out_csv: str | None) -> None:
    df = db_cache.read_etf_scores(start_date=start, end_date=end)
    if df.empty:
        print(f"  {start} ~ {end or 'now'} 无数据")
        return
    print(f"  区间 {start} ~ {end or 'now'}  共 {len(df)} 条")
    if out_csv:
        out_path = Path(out_csv).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"  已导出: {out_path}")
    else:
        print(df.head(20).to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 评分历史查询")
    ap.add_argument("--code", help="按 ETF 代码查时间序列")
    ap.add_argument("--latest", action="store_true", help="查最近一次全市场快照")
    ap.add_argument("--date", help="指定快照日期（YYYY-MM-DD），配合 --latest 使用")
    ap.add_argument("--low-streak", type=int, default=0,
                    help="找出最近 N 次快照都低于阈值的 ETF（半年池淘汰复盘）")
    ap.add_argument("--threshold", type=float, default=72,
                    help="--low-streak 的分数阈值，默认 72")
    ap.add_argument("--window-days", type=int, default=200,
                    help="--low-streak 只看最近 N 天内的快照，默认 200 天")
    ap.add_argument("--start", help="区间查询起始日期")
    ap.add_argument("--end", help="区间查询结束日期")
    ap.add_argument("--out", help="区间查询导出 CSV 路径")
    args = ap.parse_args()

    if args.code:
        cmd_code(args.code)
    elif args.latest or args.date:
        cmd_latest(args.date)
    elif args.low_streak > 0:
        cmd_low_streak(args.low_streak, args.threshold, args.window_days)
    elif args.start:
        cmd_range(args.start, args.end, args.out)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
