#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新东方财富三层行业板块和个股三级绑定。

数据源：东方财富大盘星图 /stockhotmap/api/getquotebasedata

写入表：
  - em_board_l1
  - em_board_l2
  - em_board_l3
  - em_stock_board_l3
"""
import argparse
import os
import sys
import time
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore")
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import requests

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import (
    get_connection,
    get_em_board_id_map,
    init_db,
    replace_em_stock_board_l3,
    upsert_em_board_l1,
    upsert_em_board_l2,
    upsert_em_board_l3,
)


BASE_URL = "https://quote.eastmoney.com/stockhotmap/api/getquotebasedata"
REFERER = "https://quote.eastmoney.com/stockhotmap/"


def fetch_base_data(timeout=30, retries=3):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": REFERER}
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                BASE_URL,
                params={"hash": ""},
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("re"):
                raise RuntimeError(data.get("message") or "eastmoney returned re=false")
            for key in ("baseinfo", "bk1", "bk2", "bk3"):
                if not isinstance(data.get(key), list):
                    raise RuntimeError(f"missing {key}")
            return data
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(f"  东方财富大盘星图接口第{attempt}次失败，重试 ({type(exc).__name__})")
                time.sleep(attempt)
    raise last_error


def parse_board(raw):
    parts = str(raw).split("|")
    if len(parts) < 3:
        return None
    return {
        "name": parts[0].strip(),
        "market": parts[1].strip(),
        "code": parts[2].strip(),
    }


def parse_stock(raw):
    parts = str(raw).split("|", 6)
    if len(parts) < 7:
        return None
    try:
        l1_idx = int(parts[0])
        l2_idx = int(parts[1])
        l3_idx = int(parts[2])
    except ValueError:
        return None
    raw_code = parts[5].strip()
    code = normalize_stock_code(parts[4].strip(), raw_code)
    if not code:
        return None
    return {
        "l1_idx": l1_idx,
        "l2_idx": l2_idx,
        "l3_idx": l3_idx,
        "name": parts[3].strip(),
        "raw_market": parts[4].strip(),
        "raw_code": raw_code,
        "code": code,
        "labels": parts[6].strip()[:128],
    }


def normalize_stock_code(raw_market, raw_code):
    code = "".join(ch for ch in str(raw_code) if ch.isdigit())
    if len(code) != 6:
        return ""
    if raw_market == "1" or code.startswith("6"):
        return f"sh.{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz.{code}"
    # 北交所新旧代码段：4/8以及2024年启用的920xxx。
    # 上海B股900xxx的 raw_market=1，已在上方优先归入上海。
    if code.startswith(("4", "8", "9")):
        return f"bj.{code}"
    return f"{raw_market}.{code}"


def most_common_parent(parent_counter):
    if not parent_counter:
        return None
    return parent_counter.most_common(1)[0][0]


def build_payload(data, today):
    boards = {
        1: [parse_board(x) for x in data["bk1"]],
        2: [parse_board(x) for x in data["bk2"]],
        3: [parse_board(x) for x in data["bk3"]],
    }
    boards = {
        level: [x for x in rows if x and x["code"]]
        for level, rows in boards.items()
    }
    stocks = [parse_stock(x) for x in data["baseinfo"]]
    stocks = [x for x in stocks if x]

    l2_parent_votes = defaultdict(Counter)
    l3_parent_votes = defaultdict(Counter)
    for stock in stocks:
        l2_parent_votes[stock["l2_idx"]][stock["l1_idx"]] += 1
        l3_parent_votes[stock["l3_idx"]][(stock["l1_idx"], stock["l2_idx"])] += 1

    l1_rows = [
        (board["code"], board["name"], board["market"], idx, today)
        for idx, board in enumerate(boards[1])
    ]

    l2_links = []
    for idx, board in enumerate(boards[2]):
        parent_idx = most_common_parent(l2_parent_votes.get(idx))
        if parent_idx is None or parent_idx >= len(boards[1]):
            continue
        l2_links.append((idx, board, boards[1][parent_idx]))

    l3_links = []
    for idx, board in enumerate(boards[3]):
        parents = most_common_parent(l3_parent_votes.get(idx))
        if not parents:
            continue
        l1_idx, l2_idx = parents
        if l1_idx >= len(boards[1]) or l2_idx >= len(boards[2]):
            continue
        l3_links.append((idx, board, boards[1][l1_idx], boards[2][l2_idx]))

    return boards, stocks, l1_rows, l2_links, l3_links


def write_payload(l1_rows, l2_links, l3_links, stocks, today):
    l1_count = upsert_em_board_l1(l1_rows)
    l1_id = get_em_board_id_map(1)

    l2_rows = []
    for idx, board, l1_board in l2_links:
        parent_id = l1_id.get(l1_board["code"])
        if parent_id:
            l2_rows.append((parent_id, board["code"], board["name"], board["market"], idx, today))
    l2_count = upsert_em_board_l2(l2_rows)
    l2_id = get_em_board_id_map(2)

    l3_rows = []
    for idx, board, l1_board, l2_board in l3_links:
        parent_l1_id = l1_id.get(l1_board["code"])
        parent_l2_id = l2_id.get(l2_board["code"])
        if parent_l1_id and parent_l2_id:
            l3_rows.append((
                parent_l1_id, parent_l2_id, board["code"], board["name"], board["market"], idx, today
            ))
    l3_count = upsert_em_board_l3(l3_rows)
    l3_id = get_em_board_id_map(3)

    l3_code_by_index = {
        idx: board["code"]
        for idx, board, _l1, _l2 in l3_links
    }
    stock_rows = []
    for stock in stocks:
        l3_code = l3_code_by_index.get(stock["l3_idx"])
        board_id = l3_id.get(l3_code)
        if not board_id:
            continue
        stock_rows.append((
            stock["code"],
            stock["name"],
            stock["raw_code"],
            stock["raw_market"],
            board_id,
            stock["labels"],
            today,
        ))
    stock_count = replace_em_stock_board_l3(stock_rows)

    return {
        "l1": l1_count,
        "l2": l2_count,
        "l3": l3_count,
        "stocks": stock_count,
    }


def print_sample():
    conn = get_connection(readonly=True)
    try:
        rows = conn.execute("""
            SELECT s.code, s.code_name,
                   l1.board_name AS l1_name,
                   l2.board_name AS l2_name,
                   l3.board_name AS l3_name,
                   l3.board_code AS l3_code
            FROM em_stock_board_l3 s
            JOIN em_board_l3 l3 ON s.l3_id = l3.id
            JOIN em_board_l2 l2 ON l3.l2_id = l2.id
            JOIN em_board_l1 l1 ON l3.l1_id = l1.id
            ORDER BY s.code
            LIMIT 10
        """).fetchall()
        print("  样例绑定:")
        for row in rows:
            print(f"    {row[0]} {row[1]} -> {row[2]} / {row[3]} / {row[4]} ({row[5]})")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="更新东方财富三层行业板块和个股三级绑定")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并统计，不写库")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    print("=" * 80)
    print(f"  东方财富三层行业板块更新  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)

    data = fetch_base_data(timeout=args.timeout, retries=args.retries)
    boards, stocks, l1_rows, l2_links, l3_links = build_payload(data, today)
    print(f"  接口 hash: {data.get('hash')}")
    print(f"  一级: {len(boards[1])}  二级: {len(boards[2])}  三级: {len(boards[3])}  个股绑定: {len(stocks)}")
    print(f"  可建立父子关系: 二级 {len(l2_links)}/{len(boards[2])}  三级 {len(l3_links)}/{len(boards[3])}")

    if args.dry_run:
        print("  模式: dry-run，未写库")
        print("=" * 80)
        return 0

    counts = write_payload(l1_rows, l2_links, l3_links, stocks, today)
    print(
        f"  写入/更新: 一级 {counts['l1']}  二级 {counts['l2']}  "
        f"三级 {counts['l3']}  个股绑定 {counts['stocks']}"
    )
    print_sample()
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
