#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
腾讯行情当日快照入库

用途：新浪日K接口临时超时/限流时，用腾讯实时行情的当日 OHLC 快照
补齐 kline_daily 的最新交易日数据；股票与三大指数必须同日，供M5行业超额强度判断。

用法：
  python _update_today_snapshot_qq.py
"""
import argparse
import re
import sys
import warnings
from datetime import datetime, time as dt_time
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
from db_cache import get_connection, init_db


DROP_STATUS = {"D", "U"}
MAIN_INDEX_CODES = (
    "sh.000001",  # 上证指数
    "sh.000016",  # 上证50
    "sh.000300",  # 沪深300
    "sz.399001",  # 深证成指
    "sz.399006",  # 创业板指
)


def _to_float(value, default=0.0):
    try:
        if value in (None, "", "--"):
            return default
        return float(value)
    except Exception:
        return default


def _symbol(code):
    return code.replace(".", "")


def _bs_code(symbol):
    return f"{symbol[:2]}.{symbol[2:]}"


def _quote_date(raw_time):
    if not raw_time or len(raw_time) < 8:
        return datetime.now().strftime("%Y-%m-%d")
    return f"{raw_time[:4]}-{raw_time[4:6]}-{raw_time[6:8]}"


def _amount_yuan(items):
    if len(items) > 35 and "/" in items[35]:
        parts = items[35].split("/")
        if len(parts) >= 3:
            return _to_float(parts[2], 0.0)
    if len(items) > 37:
        return _to_float(items[37], 0.0) * 10000
    return 0.0


def get_db_quote_codes(conn):
    rows = conn.execute("""
        SELECT DISTINCT code
        FROM kline_daily
        WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'
        ORDER BY code
    """).fetchall()
    stock_codes = [row[0] for row in rows]
    return stock_codes + [code for code in MAIN_INDEX_CODES if code not in stock_codes]


def fetch_snapshots(codes, batch_size=80, timeout=10):
    headers = {"User-Agent": "Mozilla/5.0"}
    snapshots = []
    skipped = {"drop_status": 0, "no_trade": 0, "invalid": 0, "request_error": 0}
    symbols = [_symbol(code) for code in codes]

    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        try:
            resp = requests.get(
                "http://qt.gtimg.cn/q=" + ",".join(batch),
                headers=headers,
                timeout=timeout,
            )
            resp.encoding = "gbk"
        except Exception:
            skipped["request_error"] += len(batch)
            continue

        for line in resp.text.strip().split("\n"):
            match = re.match(r'v_([a-z]{2}\d{6})="(.*)";', line.strip())
            if not match:
                continue
            symbol, payload = match.groups()
            if not payload:
                skipped["invalid"] += 1
                continue
            items = payload.split("~")
            if len(items) < 41:
                skipped["invalid"] += 1
                continue

            status = items[40].strip()
            if status in DROP_STATUS:
                skipped["drop_status"] += 1
                continue

            open_value = _to_float(items[5])
            high_value = _to_float(items[33])
            low_value = _to_float(items[34])
            close_value = _to_float(items[3])
            # 腾讯A股大多数市场 items[36] 按“手”返回，但科创板 sh68xxxx
            # 按“股”返回；kline_daily 统一按股存储。
            raw_volume = _to_float(items[36])
            volume = raw_volume if symbol.startswith("sh68") else raw_volume * 100
            if min(open_value, high_value, low_value, close_value) <= 0 or volume <= 0:
                skipped["no_trade"] += 1
                continue

            snapshots.append({
                "code": _bs_code(symbol),
                "date": _quote_date(items[30].strip()),
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": volume,
                "amount": _amount_yuan(items),
                "turn": _to_float(items[38]),
                "pctChg": _to_float(items[32]),
                "name": items[1].strip(),
            })

    return snapshots, skipped


def upsert_snapshots(rows):
    if not rows:
        return 0
    conn = get_connection()
    values = [(
        row["code"], row["date"], row["open"], row["high"], row["low"], row["close"],
        row["volume"], row["amount"], row["turn"], row["pctChg"],
    ) for row in rows]
    conn.executemany("""
        INSERT INTO kline_daily (code, date, open, high, low, close, volume, amount, turn, pctChg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
            volume=VALUES(volume), amount=VALUES(amount), turn=VALUES(turn), pctChg=VALUES(pctChg)
    """, values)
    conn.commit()
    conn.close()
    return len(values)


def main():
    parser = argparse.ArgumentParser(description="用腾讯当日行情快照补齐 kline_daily 最新交易日")
    parser.add_argument("--batch-size", type=int, default=80, help="腾讯批量请求大小")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并统计，不写库")
    parser.add_argument("--indices-only", action="store_true", help="只处理三大指数，用于快速核对指数收盘快照")
    parser.add_argument("--allow-intraday", action="store_true", help="显式允许交易日15:10前写入盘中快照")
    parser.add_argument("--allow-partial", action="store_true", help="显式允许请求批次失败时写入不完整快照")
    args = parser.parse_args()

    now = datetime.now()
    if (
        not args.dry_run
        and not args.allow_intraday
        and now.weekday() < 5
        and now.time() < dt_time(15, 10)
    ):
        print("拒绝写库：交易日15:10前仍可能是盘中快照。")
        print("如确需盘中写入，请显式加 --allow-intraday；收盘数据库更新请在15:10后运行。")
        return 2

    init_db()
    conn = get_connection()
    codes = list(MAIN_INDEX_CODES) if args.indices_only else get_db_quote_codes(conn)
    conn.close()

    print("=" * 80)
    print(f"  腾讯当日快照入库  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
    print(f"  待抓取代码: {len(codes)}  模式: {'演练' if args.dry_run else '写库'}")

    rows, skipped = fetch_snapshots(codes, batch_size=args.batch_size)
    dates = sorted({row["date"] for row in rows})
    print(f"  有效快照: {len(rows)}  日期: {dates}")
    print(f"  跳过: {skipped}")

    if not args.dry_run and skipped["request_error"] and not args.allow_partial:
        print("拒绝写库：存在请求失败批次，避免用不完整快照覆盖数据库。")
        print("确认接受部分更新时才可显式加 --allow-partial。")
        return 2

    if args.dry_run:
        written = 0
    else:
        written = upsert_snapshots(rows)
    print(f"  写入/更新: {written} 行")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
