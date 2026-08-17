#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池重整脚本

功能：
  1. 用腾讯行情识别当前沪深主板/创业板/科创板有效股票列表
  2. 删除库内已退市/无效代码的 K 线、行业映射、概念成员关系
  3. 补充最近新上市但库内缺失的股票 K 线（含创业板/科创板新股）

默认只演练不改库；确认清单后加 --apply 执行。

用法：
  python _refresh_stock_universe.py
  python _refresh_stock_universe.py --apply
  python _refresh_stock_universe.py --apply --new-days 500
"""
import argparse
import re
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")

import requests

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_connection, init_db, upsert_kline_batch
from sina_kline import fetch_kline


INDEX_CODES = {"sh.000001", "sz.399001", "sz.399006"}
DROP_STATUS = {"D", "U"}  # D=退市/摘牌，U=未上市或无效；S=停牌，保留


def _bs_code(symbol):
    return f"{symbol[:2]}.{symbol[2:]}"


def _iter_market_symbols():
    # 沪市主板 60xxxx
    for num in range(600000, 606000):
        yield f"sh{num:06d}"
    # 科创板 688xxx / 689xxx
    for num in range(688000, 690000):
        yield f"sh{num:06d}"
    # 深市主板/中小板 00xxxx
    for num in range(1, 4000):
        yield f"sz{num:06d}"
    # 创业板 30xxxx（覆盖完整六位代码空间；不能只枚举300/301段）
    for num in range(300000, 310000):
        yield f"sz{num:06d}"


def fetch_tencent_universe(batch_size=80, timeout=10):
    """返回当前沪深主板/创业板/科创板有效股票 dict: code -> meta。"""
    symbols = list(_iter_market_symbols())
    universe = {}
    status_counts = {}
    headers = {"User-Agent": "Mozilla/5.0"}

    failed_batches = 0
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        resp = None
        for _attempt in range(2):  # 一次重试，降低偶发丢批（丢批会把有效股误判为退市）
            try:
                resp = requests.get(
                    "http://qt.gtimg.cn/q=" + ",".join(batch),
                    headers=headers,
                    timeout=timeout,
                )
                resp.encoding = "gbk"
                break
            except Exception:
                resp = None
                time.sleep(0.5)
        if resp is None:
            failed_batches += 1
            continue

        for line in resp.text.strip().split("\n"):
            match = re.match(r'v_([a-z]{2}\d{6})="(.*)";', line.strip())
            if not match:
                continue
            symbol, payload = match.groups()
            if not payload:
                continue
            items = payload.split("~")
            if len(items) < 41 or not items[1].strip():
                continue

            status = items[40].strip() or "normal"
            status_counts[status] = status_counts.get(status, 0) + 1
            if status in DROP_STATUS:
                continue

            code = _bs_code(symbol)
            if not (
                code.startswith("sh.60") or code.startswith("sh.68")
                or code.startswith("sz.00") or code.startswith("sz.30")
            ):
                continue
            universe[code] = {
                "name": items[1].strip(),
                "status": status,
                "price": items[3].strip(),
                "quote_time": items[30].strip(),
            }

    if failed_batches:
        print(f"  ⚠️ 腾讯抓取有 {failed_batches} 个批次失败（每批~{batch_size}只），universe 可能不完整")
    status_counts["_failed_batches"] = failed_batches
    return universe, status_counts


def get_db_stock_codes(conn):
    rows = conn.execute("""
        SELECT DISTINCT code
        FROM kline_daily
        WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'
        ORDER BY code
    """).fetchall()
    return {row[0] for row in rows}


def _chunks(items, size=300):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _count_rows(conn, table, code_col, codes):
    total = 0
    for chunk in _chunks(codes):
        placeholders = ",".join(["%s"] * len(chunk))
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {code_col} IN ({placeholders})",
            chunk,
        ).fetchone()
        total += row[0] if row else 0
    return total


def delete_stale_codes(conn, codes, apply=False):
    if not codes:
        return {}
    tables = [
        ("kline_daily", "code"),
        ("stock_industry", "code"),
    ]
    deleted = {}
    for table, code_col in tables:
        row_count = _count_rows(conn, table, code_col, codes)
        deleted[table] = row_count
        if not apply or row_count == 0:
            continue
        for chunk in _chunks(codes):
            placeholders = ",".join(["%s"] * len(chunk))
            conn.execute(
                f"DELETE FROM {table} WHERE {code_col} IN ({placeholders})",
                chunk,
            )
        conn.commit()
    return deleted


def upsert_new_stock_names(conn, new_codes, universe, apply=False):
    if not new_codes or not apply:
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    rows = [(code, universe[code]["name"], today) for code in new_codes]
    conn.executemany("""
        INSERT INTO stock_industry (code, code_name, update_date)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            code_name=VALUES(code_name),
            update_date=VALUES(update_date)
    """, rows)
    conn.commit()
    return len(rows)


def fetch_new_klines(new_codes, universe, days=500, sleep_seconds=0.2, apply=False):
    summary = {"ok": 0, "empty": 0, "failed": 0, "rows": 0}
    if not new_codes:
        return summary
    if not apply:
        return summary

    for idx, code in enumerate(new_codes, 1):
        name = universe[code]["name"]
        print(f"  [{idx:>2d}/{len(new_codes)}] 补新股 {code} {name}", end="")
        try:
            df = fetch_kline(code, days=days)
            if df is None or df.empty:
                summary["empty"] += 1
                print("  K线空")
            else:
                n = upsert_kline_batch(code, df)
                summary["ok"] += 1
                summary["rows"] += n
                print(f"  K线{n}条 {df['date'].min()}~{df['date'].max()}")
        except Exception as exc:
            summary["failed"] += 1
            print(f"  失败({type(exc).__name__})")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return summary


def main():
    parser = argparse.ArgumentParser(description="重整沪深主板/创业板/科创板股票池")
    parser.add_argument("--apply", action="store_true", help="实际写库；默认只演练")
    parser.add_argument("--new-days", type=int, default=500, help="新补股票拉取最近多少个交易日")
    parser.add_argument("--sleep", type=float, default=0.2, help="补新股时每只股票暂停秒数")
    parser.add_argument("--batch-size", type=int, default=80, help="腾讯行情批量请求大小")
    parser.add_argument("--skip-new-kline", action="store_true", help="只写入新股名称，不补K线")
    args = parser.parse_args()

    init_db()
    print("=" * 80)
    print(f"  股票池重整  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
    print(f"  模式: {'写库执行' if args.apply else '只读演练'}")

    universe, status_counts = fetch_tencent_universe(batch_size=args.batch_size)
    conn = get_connection()
    db_codes = get_db_stock_codes(conn)

    current_codes = set(universe)
    stale_codes = sorted(db_codes - current_codes)
    new_codes = sorted(current_codes - db_codes)

    print(f"  腾讯状态统计: {status_counts}")
    print(f"  当前有效(主板/创业板/科创板): {len(current_codes)}  库内: {len(db_codes)}")
    print(f"  待删除退市/无效: {len(stale_codes)}")
    if stale_codes:
        print("    " + ", ".join(stale_codes[:30]))
    print(f"  待补新上市/缺失: {len(new_codes)}")
    if new_codes:
        preview = [f"{code} {universe[code]['name']}" for code in new_codes[:30]]
        print("    " + ", ".join(preview))

    # ═══ 删除安全护栏（2026-07-02 事故：腾讯丢批→189只有效股被误判退市删除）═══
    # 腾讯抓取残缺时，正常股会缺席 universe 而被当成退市。删除前做双重体检：
    #   1) universe 明显偏小 → 抓取不完整
    #   2) 待删除数量异常偏多 → 极可能是丢批而非真退市
    # 命中任一则本轮跳过删除（新股补充照常，只增不删是安全的）。
    UNIVERSE_FLOOR = 4800   # 沪深主板+创业板+科创板正常 >5000
    STALE_CEILING = 50      # 真实单日退市/摘牌远小于此
    delete_blocked = False
    block_reason = ""
    if status_counts.get("_failed_batches"):
        delete_blocked = True
        block_reason = f"腾讯有 {status_counts['_failed_batches']} 个批次抓取失败"
    elif len(current_codes) < UNIVERSE_FLOOR:
        delete_blocked = True
        block_reason = f"universe 仅 {len(current_codes)} (<{UNIVERSE_FLOOR})，疑似抓取不完整"
    elif len(stale_codes) > STALE_CEILING:
        delete_blocked = True
        block_reason = f"待删除 {len(stale_codes)} 只 (>{STALE_CEILING})，异常偏多"

    if delete_blocked and stale_codes:
        print(f"  🛑 已拦截删除：{block_reason}。本轮不删任何代码，只补新股。")
        print(f"     如确认这 {len(stale_codes)} 只确为退市，人工核对后再单独执行删除。")
        delete_counts = {}
    else:
        delete_counts = delete_stale_codes(conn, stale_codes, apply=args.apply)
        if delete_counts:
            action = "已删除" if args.apply else "将删除"
            for table, count in delete_counts.items():
                print(f"  {action} {table}: {count} 行")

    name_rows = upsert_new_stock_names(conn, new_codes, universe, apply=args.apply)
    if new_codes:
        if args.apply:
            print(f"  已写入/更新新股名称: {name_rows} 行")
        else:
            print(f"  将写入/更新新股名称: {len(new_codes)} 行")

    conn.close()

    if args.skip_new_kline:
        kline_summary = {"ok": 0, "empty": 0, "failed": 0, "rows": 0}
        print("  跳过新股K线补充")
    else:
        kline_summary = fetch_new_klines(
            new_codes,
            universe,
            days=args.new_days,
            sleep_seconds=args.sleep,
            apply=args.apply,
        )

    print("=" * 80)
    print(
        f"  完成: 删除代码 {len(stale_codes)} | 新增代码 {len(new_codes)} | "
        f"新股K线成功 {kline_summary['ok']} 只/{kline_summary['rows']} 行 | "
        f"空 {kline_summary['empty']} | 失败 {kline_summary['failed']}"
    )
    if not args.apply:
        print("  当前为演练模式；确认无误后加 --apply 执行。")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
