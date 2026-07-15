#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新东方财富三层行业板块日K。

依赖：
  先运行 _fetch_em_board_hierarchy.py 写入 em_board_l1/l2/l3。

写入：
  em_board_daily(board_code, level, date, open, high, low, close,
                 volume, amount, amplitude, pctChg, change_amount, turn)
"""
import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import requests
import pandas as pd

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_connection, init_db, upsert_em_board_daily


KLINE_URLS = [
    "http://7.push2his.eastmoney.com/api/qt/stock/kline/get",
    "http://17.push2his.eastmoney.com/api/qt/stock/kline/get",
    "http://48.push2his.eastmoney.com/api/qt/stock/kline/get",
    "http://push2.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
]
REFERER = "https://quote.eastmoney.com/stockhotmap/"
QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
QUOTE_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f7,f8,f15,f16,f17,f18,f124"
H5_QUOTE_URL = "https://emdatah5.eastmoney.com/dc/ZJLX/getZDYLBData"


def _num(value):
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_levels(raw):
    out = []
    for part in str(raw).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        level = int(part)
        if level not in (1, 2, 3):
            raise ValueError("levels must be 1,2,3")
        out.append(level)
    return sorted(set(out))


def load_boards(levels):
    conn = get_connection(readonly=True)
    try:
        boards = []
        for level in levels:
            table = {1: "em_board_l1", 2: "em_board_l2", 3: "em_board_l3"}[level]
            rows = conn.execute(
                f"SELECT board_code, board_name FROM {table} ORDER BY board_code"
            ).fetchall()
            boards.extend((level, code, name) for code, name in rows)
        return boards
    finally:
        conn.close()


def _quote_date(value, fallback=None):
    try:
        stamp = int(float(value))
        if stamp > 0:
            return datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    except Exception:
        pass
    return fallback or datetime.now().strftime("%Y-%m-%d")


def _valid_quote(data):
    return _num(data.get("f2")) is not None and _num(data.get("f17")) is not None


def fetch_board_snapshots(boards, target_date=None, batch_size=80, timeout=12):
    """Fetch official Eastmoney board quote snapshots for the latest trade day."""
    by_code = {code: (level, name) for level, code, name in boards}
    rows = []
    skipped = {"empty": 0, "invalid": 0, "request_error": 0}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://data.eastmoney.com/bkzj/",
    }
    try:
        seen = set()
        total = None
        page_size = 100
        for page in range(1, 11):
            resp = requests.get(
                H5_QUOTE_URL,
                params={
                    "fields": QUOTE_FIELDS,
                    "pn": page,
                    "pz": page_size,
                    "fid": "f3",
                    "po": 1,
                    "fs": "m:90+t:2",
                    "ut": "",
                },
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emdatah5.eastmoney.com/dc/zjlx/block"},
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json().get("data") or {}
            total = payload.get("total") or total
            diff = payload.get("diff") or []
            if not diff:
                break
            for item in diff:
                code = item.get("f12")
                if code not in by_code or code in seen:
                    continue
                if not _valid_quote(item):
                    skipped["invalid"] += 1
                    continue
                seen.add(code)
                level, _name = by_code[code]
                row_date = _quote_date(item.get("f124"), fallback=target_date)
                if target_date and row_date != target_date:
                    row_date = target_date
                rows.append((
                    code,
                    level,
                    row_date,
                    _num(item.get("f17")),
                    _num(item.get("f15")),
                    _num(item.get("f16")),
                    _num(item.get("f2")),
                    _num(item.get("f5")),
                    _num(item.get("f6")),
                    _num(item.get("f7")),
                    _num(item.get("f3")),
                    _num(item.get("f4")),
                    _num(item.get("f8")),
                ))
            if len(seen) >= len(by_code) or (total and page * page_size >= int(total)):
                break
        if rows and len(rows) >= min(len(by_code), int(total or len(by_code))):
            return rows, skipped
    except Exception:
        pass

    codes = list(by_code)
    size = max(1, int(batch_size))
    for start in range(0, len(codes), size):
        part = codes[start:start + size]
        params = {
            "fltt": "2",
            "invt": "2",
            "secids": ",".join(f"90.{code}" for code in part),
            "fields": QUOTE_FIELDS,
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        try:
            resp = requests.get(QUOTE_URL, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            diff = (resp.json().get("data") or {}).get("diff") or []
        except Exception:
            skipped["request_error"] += len(part)
            continue
        if not diff:
            skipped["empty"] += len(part)
            continue
        for item in diff:
            code = item.get("f12")
            if code not in by_code:
                continue
            if not _valid_quote(item):
                skipped["invalid"] += 1
                continue
            level, _name = by_code[code]
            row_date = _quote_date(item.get("f124"), fallback=target_date)
            if target_date and row_date != target_date:
                row_date = target_date
            rows.append((
                code,
                level,
                row_date,
                _num(item.get("f17")),
                _num(item.get("f15")),
                _num(item.get("f16")),
                _num(item.get("f2")),
                _num(item.get("f5")),
                _num(item.get("f6")),
                _num(item.get("f7")),
                _num(item.get("f3")),
                _num(item.get("f4")),
                _num(item.get("f8")),
            ))
    return rows, skipped


def fetch_board_klines(board_code, level, days=20, end_date=None, full=False, timeout=12, retries=2):
    params = {
        "secid": f"90.{board_code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "end": (end_date or "20500101").replace("-", ""),
        "lmt": "10000" if full else str(max(int(days), 1)),
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": REFERER}
    last_error = None
    for url in KLINE_URLS:
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=timeout, proxies={"http": None, "https": None})
                resp.raise_for_status()
                data = resp.json().get("data") or {}
                klines = data.get("klines") or []
                rows = []
                for raw in klines:
                    parts = str(raw).split(",")
                    if len(parts) < 11:
                        continue
                    rows.append((
                        board_code,
                        level,
                        parts[0],
                        _num(parts[1]),
                        _num(parts[3]),
                        _num(parts[4]),
                        _num(parts[2]),
                        _num(parts[5]),
                        _num(parts[6]),
                        _num(parts[7]),
                        _num(parts[8]),
                        _num(parts[9]),
                        _num(parts[10]),
                    ))
                return rows
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(attempt * 0.6)
    raise last_error


def _recent_start_date(conn, days, full=False, end_date=None):
    if full:
        return "1990-01-01"
    params = []
    where = "WHERE code='sh.000001'"
    if end_date:
        where += " AND date <= ?"
        params.append(end_date)
    rows = conn.execute(
        f"SELECT DISTINCT date FROM kline_daily {where} ORDER BY date DESC LIMIT {max(int(days), 1)}",
        params,
    ).fetchall()
    if not rows:
        return None
    return min(str(row[0])[:10] for row in rows)


def aggregate_from_stock_klines(levels, days=20, full=False, end_date=None, missing_only=True):
    """官方 BK K 线不可用时，用东财三级成分股和个股日K聚合出板块日数据。"""
    conn = get_connection(readonly=True)
    try:
        start_date = _recent_start_date(conn, days, full=full, end_date=end_date)
        if not start_date:
            return []

        level_expr = {
            1: ("l1.board_code", "l1.board_name"),
            2: ("l2.board_code", "l2.board_name"),
            3: ("l3.board_code", "l3.board_name"),
        }
        rows = []
        for level in levels:
            code_expr, name_expr = level_expr[level]
            sql = f"""
                SELECT {code_expr} AS board_code,
                       k.date,
                       AVG(k.open) AS open_price,
                       AVG(k.high) AS high_price,
                       AVG(k.low) AS low_price,
                       AVG(k.close) AS close_price,
                       SUM(k.volume) AS volume,
                       SUM(k.amount) AS amount,
                       AVG(k.pctChg) AS pctChg,
                       AVG(k.turn) AS turn,
                       COUNT(*) AS stock_count
                FROM kline_daily k
                JOIN em_stock_board_l3 s ON k.code = s.code
                JOIN em_board_l3 l3 ON s.l3_id = l3.id
                JOIN em_board_l2 l2 ON l3.l2_id = l2.id
                JOIN em_board_l1 l1 ON l3.l1_id = l1.id
                WHERE k.date >= ?
                  AND k.code NOT LIKE 'sh.000%%'
                  AND k.code NOT LIKE 'sz.399%%'
            """
            params = [start_date]
            if end_date:
                sql += " AND k.date <= ?"
                params.append(end_date)
            sql += f" GROUP BY {code_expr}, k.date HAVING stock_count > 0"
            df = pd.read_sql_query(sql, conn, params=params)
            if df.empty:
                continue
            for _, row in df.iterrows():
                rows.append((
                    row["board_code"],
                    level,
                    str(row["date"])[:10],
                    _num(row["open_price"]),
                    _num(row["high_price"]),
                    _num(row["low_price"]),
                    _num(row["close_price"]),
                    _num(row["volume"]),
                    _num(row["amount"]),
                    None,
                    _num(row["pctChg"]),
                    None,
                    _num(row["turn"]),
                ))

        if missing_only and rows:
            codes = sorted({r[0] for r in rows})
            existing = set()
            for i in range(0, len(codes), 300):
                part = codes[i:i + 300]
                ph = ",".join(["?"] * len(part))
                sql = f"SELECT board_code, date FROM em_board_daily WHERE board_code IN ({ph}) AND date >= ?"
                params = part + [start_date]
                if end_date:
                    sql += " AND date <= ?"
                    params.append(end_date)
                existing.update((c, str(d)[:10]) for c, d in conn.execute(sql, params).fetchall())
            rows = [r for r in rows if (r[0], str(r[2])[:10]) not in existing]
        return rows
    finally:
        conn.close()


def flush(buffer):
    if not buffer:
        return 0
    n = upsert_em_board_daily(buffer)
    buffer.clear()
    return n


def main():
    parser = argparse.ArgumentParser(description="更新东方财富三层行业板块日K")
    parser.add_argument("--levels", default="1,2,3", help="更新层级，默认1,2,3")
    parser.add_argument("--days", type=int, default=20, help="最近多少根日K，默认20")
    parser.add_argument("--full", action="store_true", help="全量拉取")
    parser.add_argument("--end-date", default=None, help="截止日期 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="仅测试前N个板块")
    parser.add_argument("--sleep", type=float, default=0.03, help="每个板块间隔秒数")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--aggregate-only", action="store_true", help="跳过官方BK K线，直接用成分股日K聚合")
    parser.add_argument("--official-only", action="store_true", help="只用官方BK K线，失败不聚合兜底")
    parser.add_argument("--official-fail-stop", type=int, default=8, help="官方接口连续失败N个板块后切到聚合兜底")
    parser.add_argument("--snapshot-only", action="store_true", help="only write official Eastmoney quote snapshot for end-date")
    args = parser.parse_args()

    init_db()
    levels = _parse_levels(args.levels)
    boards = load_boards(levels)
    if args.limit:
        boards = boards[:args.limit]
    if not boards:
        print("em_board_l1/l2/l3 为空，请先运行：")
        print("  python 工具脚本/10_数据更新/_fetch_em_board_hierarchy.py")
        return 1

    print("=" * 86)
    print(f"  东方财富三层行业日K更新  层级:{','.join(map(str, levels))}  板块:{len(boards)}")
    print("=" * 86)

    ok = 0
    fail = []
    written = 0
    buffer = []
    switched_to_aggregate = args.aggregate_only

    if args.snapshot_only:
        snap_rows, skipped = fetch_board_snapshots(boards, target_date=args.end_date, timeout=args.timeout)
        written = upsert_em_board_daily(snap_rows) if snap_rows else 0
        dates = sorted({str(r[2])[:10] for r in snap_rows})
        print(f"  官方快照写入/更新: {written} 行  日期:{','.join(dates) if dates else '-'}  跳过:{skipped}")
        print("=" * 86)
        return 0 if written else 1

    if not args.aggregate_only:
        consecutive_fail = 0
        for idx, (level, code, name) in enumerate(boards, 1):
            try:
                rows = fetch_board_klines(
                    code,
                    level,
                    days=args.days,
                    end_date=args.end_date,
                    full=args.full,
                    timeout=args.timeout,
                    retries=args.retries,
                )
                if rows:
                    buffer.extend(rows)
                    ok += 1
                    consecutive_fail = 0
                else:
                    fail.append((code, name, "empty"))
                    consecutive_fail += 1
            except Exception as exc:
                fail.append((code, name, type(exc).__name__))
                consecutive_fail += 1

            if len(buffer) >= 5000:
                written += flush(buffer)
            if idx % 50 == 0 or idx == len(boards):
                print(f"  官方进度 {idx:>4d}/{len(boards)}  成功{ok}  失败{len(fail)}  已写{written + len(buffer)}")
            if (not args.official_only) and consecutive_fail >= args.official_fail_stop:
                print(f"  官方BK K线连续失败 {consecutive_fail} 个板块，切换到成分股聚合兜底")
                switched_to_aggregate = True
                break
            if args.sleep > 0:
                time.sleep(args.sleep)

        written += flush(buffer)

    aggregate_attempted = False
    if switched_to_aggregate and not args.official_only:
        snap_rows, skipped = fetch_board_snapshots(boards, target_date=args.end_date, timeout=args.timeout)
        if snap_rows:
            snap_written = upsert_em_board_daily(snap_rows)
            written += snap_written
            snap_dates = sorted({str(r[2])[:10] for r in snap_rows})
            print(f"  官方快照兜底写入/更新: {snap_written} 行  日期:{snap_dates[0]}~{snap_dates[-1]}  跳过:{skipped}")
        else:
            print(f"  官方快照兜底无数据  跳过:{skipped}")
        aggregate_attempted = True
        agg_rows = aggregate_from_stock_klines(
            levels,
            days=args.days,
            full=args.full,
            end_date=args.end_date,
            missing_only=True,
        )
        agg_written = 0
        for i in range(0, len(agg_rows), 5000):
            agg_written += upsert_em_board_daily(agg_rows[i:i + 5000])
        written += agg_written
        if agg_rows:
            agg_boards = len({r[0] for r in agg_rows})
            agg_dates = sorted({str(r[2])[:10] for r in agg_rows})
            print(f"  聚合兜底写入/更新: {agg_written} 行  板块:{agg_boards}  日期:{agg_dates[0]}~{agg_dates[-1]}")
        else:
            print("  聚合兜底无新增行（可能 em_board_daily 已有对应日期）")

    print("-" * 86)
    print(f"  写入/更新 em_board_daily: {written} 行  成功板块:{ok}  失败:{len(fail)}")
    if fail:
        print("  失败样例:")
        for code, name, reason in fail[:12]:
            print(f"    {code} {name} ({reason})")
    print("=" * 86)
    return 0 if (ok or written or aggregate_attempted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
