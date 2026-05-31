#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯实时行情快照。

用途：股票点名分析、竞价确认、盘中持仓诊断时获取当前价/盘口/量能。

用法：
  python 工具脚本/10_数据更新/tmp_rt.py sh.600000 sz.000001
  python 工具脚本/10_数据更新/tmp_rt.py 600000 000001
  python 工具脚本/10_数据更新/tmp_rt.py --all --limit 20
"""
import argparse
import re
import sys
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
from db_cache import get_connection, init_db


def _symbol(code):
    code = str(code).strip().lower()
    if not code:
        return ""
    code = code.replace("sh.", "sh").replace("sz.", "sz")
    if re.fullmatch(r"\d{6}", code):
        return ("sh" if code.startswith("6") else "sz") + code
    if re.fullmatch(r"(sh|sz)\d{6}", code):
        return code
    return ""


def _bs_code(symbol):
    return f"{symbol[:2]}.{symbol[2:]}"


def _to_float(value, default=0.0):
    try:
        if value in (None, "", "--"):
            return default
        return float(value)
    except Exception:
        return default


def _amount_wan(items):
    if len(items) > 37:
        return _to_float(items[37], 0.0)
    return 0.0


def fetch_realtime(codes, batch_size=80, timeout=10):
    symbols = [_symbol(code) for code in codes]
    symbols = [symbol for symbol in symbols if symbol]
    rows = []
    headers = {"User-Agent": "Mozilla/5.0"}
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
            continue
        for line in resp.text.strip().split("\n"):
            match = re.match(r'v_([a-z]{2}\d{6})="(.*)";', line.strip())
            if not match:
                continue
            symbol, payload = match.groups()
            items = payload.split("~")
            if len(items) < 41:
                continue
            current = _to_float(items[3])
            prev_close = _to_float(items[4])
            if current <= 0 or prev_close <= 0:
                continue
            outer = _to_float(items[7])
            inner = _to_float(items[8])
            rows.append({
                "code": _bs_code(symbol),
                "name": items[1].strip(),
                "price": current,
                "pct": _to_float(items[32]),
                "open": _to_float(items[5]),
                "high": _to_float(items[33]),
                "low": _to_float(items[34]),
                "prev_close": prev_close,
                "volume": _to_float(items[36]) * 100,
                "volume_hand": _to_float(items[36]),
                "amount_wan": _amount_wan(items),
                "turn": _to_float(items[38]),
                "outer": outer,
                "inner": inner,
                "outer_inner": outer / max(inner, 1),
                "time": items[30].strip() if len(items) > 30 else "",
                "status": items[40].strip() if len(items) > 40 else "",
            })
    return rows


def _load_all_codes(limit=None):
    init_db()
    conn = get_connection()
    sql = """
        SELECT DISTINCT code
        FROM kline_daily
        WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'
        ORDER BY code
    """
    rows = [row[0] for row in conn.execute(sql).fetchall()]
    conn.close()
    return rows[:limit] if limit else rows


def main():
    parser = argparse.ArgumentParser(description="腾讯实时行情快照")
    parser.add_argument("codes", nargs="*", help="股票代码，如 sh.600000 / 600000")
    parser.add_argument("--all", action="store_true", help="抓取数据库内全部个股")
    parser.add_argument("--limit", type=int, default=None, help="配合 --all 限制抓取数量")
    parser.add_argument("--batch-size", type=int, default=80, help="腾讯批量请求大小")
    args = parser.parse_args()

    codes = _load_all_codes(args.limit) if args.all else args.codes
    if not codes:
        parser.error("请提供股票代码，或使用 --all")

    rows = fetch_realtime(codes, batch_size=args.batch_size)
    print("=" * 120)
    print(f"  腾讯实时行情  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  有效:{len(rows)}")
    print("=" * 120)
    print(f"{'代码':<12s} {'名称':<10s} {'现价':>8s} {'涨跌':>8s} {'开盘':>8s} {'最高':>8s} {'最低':>8s} {'成交额(万)':>12s} {'换手':>7s} {'外/内':>7s} {'时间':>14s}")
    print("-" * 120)
    for row in rows:
        print(
            f"{row['code']:<12s} {row['name'][:10]:<10s} {row['price']:>8.2f} {row['pct']:>+7.2f}% "
            f"{row['open']:>8.2f} {row['high']:>8.2f} {row['low']:>8.2f} "
            f"{row['amount_wan']:>12.1f} {row['turn']:>6.2f}% {row['outer_inner']:>7.2f} {row['time']:>14s}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())