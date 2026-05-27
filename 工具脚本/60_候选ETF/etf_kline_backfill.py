#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 日K 一次性回填脚本（MySQL etf_kline_daily 表）。

用法：
    python3 工具脚本/60_候选ETF/etf_kline_backfill.py            # 回填 etf_meta.csv 白名单
    python3 工具脚本/60_候选ETF/etf_kline_backfill.py --all      # 回填整个 ETF 基础池（耗时长）
    python3 工具脚本/60_候选ETF/etf_kline_backfill.py --codes 510880,159915

逻辑：调用 etf_screener.fetch_hist(code, days=1200)，它内部会增量补到今天并写回库。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
_SELF_DIR = Path(__file__).resolve().parent
if str(_SELF_DIR) not in sys.path:
    sys.path.insert(0, str(_SELF_DIR))

import pandas as pd

import db_cache  # type: ignore
import etf_screener  # type: ignore


def _load_whitelist_codes() -> list[str]:
    meta_path = _SELF_DIR / "etf_meta.csv"
    if not meta_path.exists():
        return []
    df = pd.read_csv(meta_path, dtype={"code": str})
    return [str(c).zfill(6) for c in df["code"].tolist() if str(c).strip()]


def _load_all_codes() -> list[str]:
    from etf_pool import build_etf_pool
    pool = build_etf_pool()
    if pool is None or pool.empty or "code" not in pool.columns:
        return []
    return [str(c).zfill(6) for c in pool["code"].tolist() if str(c).strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 日K 回填脚本")
    ap.add_argument("--all", action="store_true", help="回填全市场 ETF 基础池（慢）")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔的 ETF 代码列表")
    ap.add_argument("--days", type=int, default=1200, help="回填多少自然日（默认 1200）")
    ap.add_argument("--sleep", type=float, default=0.4, help="每只之间 sleep 秒数，防限流")
    args = ap.parse_args()

    if args.codes.strip():
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    elif args.all:
        print("[+] 加载 ETF 基础池…")
        codes = _load_all_codes()
    else:
        print("[+] 加载 etf_meta.csv 白名单…")
        codes = _load_whitelist_codes()

    codes = sorted(set(codes))
    if not codes:
        print("[!] 没有可处理的代码")
        return 1

    total = len(codes)
    print(f"[+] 待回填 {total} 只 ETF，目标天数 {args.days}\n")

    ok, fail, skipped = 0, 0, 0
    t_start = time.time()
    for i, code in enumerate(codes, 1):
        before = db_cache.get_etf_row_count(code)
        last = db_cache.get_etf_last_date(code)
        try:
            df = etf_screener.fetch_hist(code, days=args.days, use_cache=True)
        except Exception as exc:
            fail += 1
            print(f"  [{i:>3}/{total}] {code}  失败: {type(exc).__name__}: {exc}")
            continue
        after = db_cache.get_etf_row_count(code)
        delta = after - before
        if df is None or df.empty:
            skipped += 1
            print(f"  [{i:>3}/{total}] {code}  空数据  (库内 {after} 行)")
        else:
            ok += 1
            print(f"  [{i:>3}/{total}] {code}  +{delta:>4} 行，库内 {after} 行，"
                  f"末日 {db_cache.get_etf_last_date(code)} (原 {last})")
        if args.sleep > 0 and i < total:
            time.sleep(args.sleep)

    elapsed = time.time() - t_start
    print(f"\n[√] 完成：成功 {ok}，跳过 {skipped}，失败 {fail}，"
          f"总库内 ETF 数 {len(db_cache.get_all_etf_codes())}，耗时 {elapsed:.1f}s")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
