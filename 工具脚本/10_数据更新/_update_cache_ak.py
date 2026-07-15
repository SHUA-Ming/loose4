#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用新浪日K更新 kline_daily。

保留的外部接口：
  - 新浪日K：由 00_公共核心/sina_kline.py 封装

本脚本只更新指数/个股K线，不再串联股票池或旧板块聚合。
需要维护股票池：运行 _refresh_stock_universe.py
需要维护东财三级行业：运行 _fetch_em_board_hierarchy.py 和 _fetch_em_board_daily.py
需要补当天快照：运行 _update_today_snapshot_qq.py
"""
import argparse
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_connection, init_db, upsert_kline_batch
from sina_kline import fetch_kline

INDEX_CODES = {"sh.000001", "sz.399001", "sz.399006"}


def ymd(value):
    return str(value)[:10] if value else ""


def load_last_dates(include_index=True):
    conn = get_connection(readonly=True)
    try:
        kline_rows = conn.execute(
            "SELECT code, MAX(date) FROM kline_daily GROUP BY code ORDER BY code"
        ).fetchall()
        pool_rows = conn.execute(
            "SELECT DISTINCT code FROM stock_industry ORDER BY code"
        ).fetchall()
    finally:
        conn.close()
    out = {row[0]: ymd(row[1]) for row in kline_rows}
    for row in pool_rows:
        out.setdefault(row[0], "")
    if not include_index:
        out = {code: date for code, date in out.items() if code not in INDEX_CODES}
    return out


def fetch_one(code, last_date, target_date, days):
    df = fetch_kline(code, days=days)
    if df is None or df.empty:
        return code, None, "empty"
    dates = df["date"].astype(str).str[:10]
    if last_date:
        df = df[dates > last_date]
        if df.empty:
            return code, None, "no_new"
        dates = df["date"].astype(str).str[:10]
    if target_date:
        df = df[dates <= target_date]
    if df.empty:
        return code, None, "no_new"
    return code, df, "ok"


def main():
    parser = argparse.ArgumentParser(description="用新浪日K更新 kline_daily")
    parser.add_argument("--end-date", default=None, help="最多补到 YYYY-MM-DD，默认今天")
    parser.add_argument("--days", type=int, default=12, help="每只代码向新浪请求最近多少根K线")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--skip-index", action="store_true", help="只更新个股，不更新三大指数")
    parser.add_argument("--limit", type=int, default=0, help="调试：只处理前N个需要更新的代码")
    args = parser.parse_args()

    init_db()
    target_date = ymd(args.end_date) or datetime.now().strftime("%Y-%m-%d")
    last_map = load_last_dates(include_index=not args.skip_index)
    targets = [(code, last) for code, last in last_map.items() if not last or last < target_date]
    if args.limit:
        targets = targets[:args.limit]

    print("=" * 80)
    print(f"  新浪日K更新  目标日期 {target_date}")
    print("=" * 80)
    print(f"  代码总数 {len(last_map)}，需要更新 {len(targets)}，workers={args.workers}")

    ok = 0
    empty = 0
    no_new = 0
    failed = 0
    rows_written = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(fetch_one, code, last, target_date, args.days): (code, last)
            for code, last in targets
        }
        for idx, future in enumerate(as_completed(futures), 1):
            code, _last = futures[future]
            try:
                code, df, status = future.result()
                if status == "ok":
                    rows_written += upsert_kline_batch(code, df)
                    ok += 1
                elif status == "empty":
                    empty += 1
                else:
                    no_new += 1
            except Exception:
                failed += 1
            if idx % 500 == 0 or idx == len(futures):
                print(
                    f"  进度 {idx}/{len(futures)} 成功{ok} 无新{no_new} 空{empty} 失败{failed} "
                    f"耗时{time.time()-t0:.0f}s"
                )

    print("=" * 80)
    print(f"  完成: 成功代码 {ok}，写入/更新 {rows_written} 行，无新 {no_new}，空 {empty}，失败 {failed}")
    print("  后续如需板块数据：python 工具脚本/10_数据更新/_fetch_em_board_daily.py --days 20")
    print("=" * 80)
    return 0 if failed < max(1, len(targets)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
