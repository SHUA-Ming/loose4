#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时盯盘守门员。

用途：
  1. 候选票买入前持续检查：市场总闸门、买入区间、追高线、单笔账户风险。
  2. 持仓卖出前持续检查：止盈、移动止盈、盘中熔断、D2/D3 到期、尾盘止损。

只输出触发动作，不自动下单。

示例：
  python 工具脚本/50_交易记录/realtime_guard.py buy 002015 --entry 22.54 23.14 --stop 22.31 --soft-stop 22.66 --strategy S4 --pos 1/16 --confidence 中 --invalidation "跌破MA5且概念转弱" --watch
  python 工具脚本/50_交易记录/realtime_guard.py sell --id 18 --watch
"""

import argparse
import datetime as dt
import subprocess
import sys
import time
import warnings
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy.*")
warnings.filterwarnings("ignore")

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))

from project_paths import ensure_tool_paths
ensure_tool_paths()

from db_cache import get_connection, get_open_trades, init_db
from market_mode import get_mode_params
from tmp_rt import fetch_realtime


MAIN_INDEX_CODES = ("sh.000001", "sz.399001", "sz.399006")


def normalize_code(code):
    raw = str(code).strip().lower()
    if raw.startswith(("sh.", "sz.")):
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        return raw
    return f"sh.{digits}" if digits.startswith("6") else f"sz.{digits}"


def money(value):
    if value is None or value == "":
        return "-"
    try:
        if value != value:  # NaN
            return "-"
    except Exception:
        pass
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value)


def pct(value):
    if value is None or value == "":
        return "-"
    try:
        if value != value:  # NaN
            return "-"
    except Exception:
        pass
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return str(value)


def parse_position(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return round(float(left) / float(right), 6)
        except Exception:
            return None
    try:
        number = float(text)
    except Exception:
        return None
    return round(number / 100, 6) if number > 1 else number


def is_blank(value):
    if value is None:
        return True
    try:
        if value != value:  # NaN
            return True
    except Exception:
        pass
    return str(value).strip() == ""


def clean_float(value, default=None):
    if is_blank(value):
        return default
    try:
        return float(value)
    except Exception:
        return default


def market_snapshot():
    try:
        mode = get_mode_params()
        return {
            "mode": mode.get("mode"),
            "phase": mode.get("cycle_phase"),
            "pos_mod": mode.get("position_modifier"),
            "warning": mode.get("cycle_warning"),
            "error": None,
        }
    except Exception as exc:
        return {"mode": None, "phase": None, "pos_mod": None, "warning": None, "error": str(exc)}


def hard_market_veto(mode):
    reasons = []
    if mode.get("mode") == "M5":
        reasons.append("当前市场模式 M5")
    if mode.get("phase") in ("退潮", "过热"):
        reasons.append(f"当前情绪周期 {mode.get('phase')}")
    if mode.get("pos_mod") == 0:
        reasons.append("position_modifier=0")
    if mode.get("error"):
        reasons.append(f"M档读取失败: {mode.get('error')}")
    return reasons


def fetch_snapshot(code):
    code = normalize_code(code)
    rows = fetch_realtime([code, *MAIN_INDEX_CODES])
    by_code = {row["code"]: row for row in rows}
    return by_code.get(code), {idx: by_code.get(idx) for idx in MAIN_INDEX_CODES}


def index_veto(index_rows):
    reasons = []
    for code, row in index_rows.items():
        if not row:
            continue
        if float(row.get("pct") or 0) <= -1.0:
            reasons.append(f"{row.get('name') or code} 跌幅 {pct(row.get('pct'))} 超过 -1%")
    return reasons


def range_position(row):
    high = float(row.get("high") or 0)
    low = float(row.get("low") or 0)
    price = float(row.get("price") or 0)
    if high <= low:
        return None
    return (price - low) / (high - low)


def now_ge(hhmm):
    hour, minute = [int(x) for x in str(hhmm).split(":", 1)]
    return dt.datetime.now().time() >= dt.time(hour, minute)


def load_trade_dates():
    conn = get_connection(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT date
            FROM kline_daily
            WHERE code='sh.000001'
            ORDER BY date
            """
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0])[:10] for row in rows if row and row[0]]


def trading_age(buy_date, trade_dates):
    if is_blank(buy_date) or not trade_dates:
        return None
    b = str(buy_date)[:10]
    start_idx = None
    for idx, day in enumerate(trade_dates):
        if day >= b:
            start_idx = idx
            break
    if start_idx is None:
        return None
    return max(0, len(trade_dates) - 1 - start_idx)


def infer_horizon(row):
    parts = []
    for col in ("expected_horizon", "plan_source", "mode", "remark", "buy_status"):
        if col in row and not is_blank(row[col]):
            parts.append(str(row[col]))
    text = " ".join(parts)
    if "D3" in text or "方案B" in text or "收盘选股" in text:
        return 3
    if "D2" in text or "方案A" in text or "方案C" in text or "盘后" in text or "尾盘" in text:
        return 2
    return None


def print_header(kind, row, mode, index_rows):
    print()
    print("=" * 96)
    print(f"{kind}  {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if row:
        pos = range_position(row)
        pos_text = "-" if pos is None else f"{pos * 100:.0f}%"
        print(
            f"{row['code']} {row['name']}  现价 {money(row.get('price'))}  涨跌 {pct(row.get('pct'))}  "
            f"日内 {money(row.get('low'))}~{money(row.get('high'))}  区间位置 {pos_text}  "
            f"外/内 {row.get('outer_inner', 0):.2f}  时间 {row.get('time') or '-'}"
        )
    print(f"市场环境：{mode.get('mode') or '-'} / {mode.get('phase') or '-'}  pos_mod={mode.get('pos_mod')}")
    idx_text = []
    for code, idx in index_rows.items():
        if idx:
            idx_text.append(f"{idx.get('name') or code}{pct(idx.get('pct'))}")
    if idx_text:
        print("指数快照：" + " ｜ ".join(idx_text))


def build_buy_record_command(args, row):
    cmd = [
        "python", "工具脚本/50_交易记录/trade_log.py", "buy",
        normalize_code(args.code), args.name or (row.get("name") if row else "-"),
        money(row.get("price") if row else args.entry[0]),
        "--strategy", args.strategy,
        "--entry", money(args.entry[0]), money(args.entry[1]),
        "--stop", money(args.stop),
        "--soft-stop", money(args.soft_stop),
        "--pos", args.pos,
        "--confidence", args.confidence,
        "--invalidation", args.invalidation,
        "--source", args.source,
        "--expected", args.expected,
        "--remark", "realtime_guard触发",
    ]
    if args.market_mode:
        cmd += ["--market-mode", args.market_mode]
    if args.grade:
        cmd += ["--grade", args.grade]
    if args.score is not None:
        cmd += ["--score", str(args.score)]
    if args.target is not None:
        cmd += ["--target", money(args.target)]
    if args.target2 is not None:
        cmd += ["--target2", money(args.target2)]
    if args.shares is not None:
        cmd += ["--shares", str(args.shares)]
    if args.amount is not None:
        cmd += ["--amount", money(args.amount)]
    return subprocess.list2cmdline([str(x) for x in cmd if x != ""])


def decide_buy(args, row, mode, index_rows):
    if not row:
        return "无法判断", ["未取到实时行情"], None

    price = float(row["price"])
    entry_low, entry_high = args.entry
    reasons = []
    veto = hard_market_veto(mode) + index_veto(index_rows)
    if veto:
        return "禁止买入", veto, None
    if price <= float(args.stop):
        return "放弃", [f"现价 {money(price)} 已触及/跌破硬止损 {money(args.stop)}，原计划失效"], None
    if price > entry_high * (1 + args.chase_pct / 100):
        return "追高放弃", [f"现价高于买入区上沿+{args.chase_pct:g}%：{money(entry_high * (1 + args.chase_pct / 100))}"], None
    if price > entry_high:
        return "等回踩", [f"现价 {money(price)} 高于买入区 {money(entry_low)}~{money(entry_high)}，不到区间不买"], None
    if price < entry_low:
        return "等确认", [f"现价 {money(price)} 低于买入区下沿 {money(entry_low)}，未站回区间"], None
    if float(row.get("pct") or 0) > args.max_pct:
        return "放弃", [f"当日涨幅 {pct(row.get('pct'))} 超过上限 {args.max_pct:g}%，不追涨"], None

    position = parse_position(args.pos)
    if position is None:
        return "禁止买入", ["仓位无法解析，不能计算单笔账户风险"], None
    risk_pct = max((price - float(args.stop)) / price, 0)
    account_risk = risk_pct * position
    if account_risk > args.max_account_risk / 100:
        return "禁止买入", [f"单笔账户风险 {account_risk * 100:.2f}% 超过上限 {args.max_account_risk:g}%"], None

    confirmations = []
    failures = []
    day_pos = range_position(row)
    if day_pos is not None and day_pos >= args.min_range_pos:
        confirmations.append(f"收在日内区间上半段({day_pos * 100:.0f}%)")
    else:
        failures.append("日内承接不足")
    if float(row.get("outer_inner") or 0) >= args.min_outer_inner:
        confirmations.append(f"外/内 {row.get('outer_inner', 0):.2f} >= {args.min_outer_inner:g}")
    else:
        failures.append("外/内偏弱")
    if float(row.get("price") or 0) >= float(row.get("open") or 0):
        confirmations.append("现价不低于开盘价")
    else:
        failures.append("现价低于开盘价")
    if float(row.get("pct") or 0) >= args.min_pct:
        confirmations.append(f"涨跌幅不弱于 {args.min_pct:g}%")
    else:
        failures.append("个股跌幅偏大")

    if len(confirmations) >= args.confirm:
        reasons.append(f"价格在买入区 {money(entry_low)}~{money(entry_high)}")
        reasons.extend(confirmations)
        reasons.append(f"预设账户风险 {account_risk * 100:.2f}% <= {args.max_account_risk:g}%")
        return "买入触发", reasons, build_buy_record_command(args, row)

    reasons.append(f"价格已在买入区，但确认项 {len(confirmations)}/{args.confirm} 不足")
    reasons.extend(confirmations)
    reasons.extend(failures)
    return "继续等待", reasons, None


def run_buy_once(args):
    if args.entry[0] > args.entry[1]:
        raise SystemExit("--entry LOW HIGH 顺序错误")
    row, index_rows = fetch_snapshot(args.code)
    mode = market_snapshot()
    if not args.market_mode and mode.get("mode"):
        args.market_mode = mode["mode"]
    print_header("买入盯盘", row, mode, index_rows)
    action, reasons, command = decide_buy(args, row, mode, index_rows)
    print(f"动作：{action}")
    print("原因：")
    for reason in reasons:
        print(f"  - {reason}")
    if command:
        print("成交后落库命令：")
        print(f"  {command}")
    return action


def load_open_trade(trade_id):
    init_db()
    df = get_open_trades()
    if df.empty:
        return None
    hit = df[df["id"].astype(str) == str(trade_id)]
    if hit.empty:
        return None
    return hit.iloc[0].to_dict()


def decide_sell(args, trade, row, mode, index_rows):
    if not row:
        return "无法判断", ["未取到实时行情"]

    price = float(row["price"])
    buy_price = float(trade.get("buy_price") or 0)
    if buy_price <= 0:
        return "无法判断", ["交易记录缺买入价"]

    pnl = (price - buy_price) / buy_price * 100
    strategy = str(trade.get("strategy") or "")
    trail_pct = args.trail_pct if args.trail_pct is not None else (3.0 if strategy == "S4" else 2.5)
    target1 = clean_float(trade.get("target_price"), buy_price * (1.06 if strategy == "S4" else 1.04))
    target2 = clean_float(trade.get("target2_price"), buy_price * (1.09 if strategy == "S4" else 1.065))
    stop = clean_float(trade.get("stop_price"))
    soft_stop = clean_float(trade.get("soft_stop"))
    day_high = float(row.get("high") or price)

    reasons = []
    if price <= buy_price * (1 - args.meltdown_pct / 100):
        return "立刻全清", [f"盘中-{args.meltdown_pct:g}%熔断触发，浮盈亏 {pnl:+.2f}%"]

    if target1 is not None and day_high >= target1 and price <= day_high * (1 - trail_pct / 100):
        return "移动止盈全清", [f"日高 {money(day_high)} 已过止盈1 {money(target1)}，现价从高点回落超过 {trail_pct:g}%"]

    if target2 is not None and price >= target2:
        return "强势止盈", [f"现价 {money(price)} 到达止盈2 {money(target2)}，至少卖出剩余大部分仓位"]

    if target1 is not None and price >= target1:
        part = "卖1/3" if strategy == "S4" else "卖50%"
        return part, [f"现价 {money(price)} 到达止盈1 {money(target1)}，先落袋"]

    trade_dates = load_trade_dates()
    age = trading_age(trade.get("buy_date"), trade_dates)
    horizon = infer_horizon(trade)
    if horizon is not None and age is not None and age >= horizon:
        if now_ge(args.exit_time):
            return "到期全清", [f"D{horizon}到期，已持有D{age}，当前已过 {args.exit_time}"]
        reasons.append(f"D{horizon}到期，已持有D{age}，{args.exit_time} 后必须清")

    market_risk = hard_market_veto(mode) + index_veto(index_rows)
    if market_risk and pnl < 0 and now_ge(args.exit_time):
        return "风险全清", [f"浮亏 {pnl:+.2f}%，且市场风险触发: {'; '.join(market_risk)}"]
    if market_risk:
        reasons.append("市场风险: " + "; ".join(market_risk))

    if stop is not None and price <= stop:
        if now_ge(args.close_check_time):
            return "止损全清", [f"尾盘仍低于硬止损 {money(stop)}，不抗单"]
        reasons.append(f"盘中低于硬止损 {money(stop)}，{args.close_check_time} 后若收不回则清")

    if soft_stop is not None and price <= soft_stop:
        if now_ge(args.close_check_time):
            return "软止损确认", [f"尾盘低于软止损 {money(soft_stop)}，按计划次日开盘/当前可成交窗口处理"]
        reasons.append(f"低于软止损 {money(soft_stop)}，等尾盘确认")

    if not reasons:
        reasons.append(f"未触发卖出条件，浮盈亏 {pnl:+.2f}%")
    return "继续持有", reasons


def build_sell_record_command(args, row, action):
    reason = action.replace(" ", "")
    cmd = [
        "python", "工具脚本/50_交易记录/trade_log.py", "sell",
        str(args.id), money(row.get("price")), "--reason", reason, "--rule", "1",
        "--remark", "realtime_guard触发",
    ]
    return subprocess.list2cmdline([str(x) for x in cmd])


def run_sell_once(args):
    trade = load_open_trade(args.id)
    if not trade:
        raise SystemExit(f"未找到未平仓交易 ID={args.id}")
    row, index_rows = fetch_snapshot(trade["code"])
    mode = market_snapshot()
    print_header(f"卖出盯盘 ID={args.id}", row, mode, index_rows)
    print(f"持仓计划：买入 {trade.get('buy_date')}@{money(trade.get('buy_price'))}  策略 {trade.get('strategy') or '-'}  硬止损 {money(trade.get('stop_price'))}  软止损 {money(trade.get('soft_stop'))}")
    action, reasons = decide_sell(args, trade, row, mode, index_rows)
    print(f"动作：{action}")
    print("原因：")
    for reason in reasons:
        print(f"  - {reason}")
    if action in ("立刻全清", "移动止盈全清", "强势止盈", "到期全清", "风险全清", "止损全清"):
        print("成交后落库命令：")
        print(f"  {build_sell_record_command(args, row, action)}")
    return action


def loop(args, runner):
    last_action = None
    while True:
        action = runner(args)
        if args.watch and action != last_action:
            last_action = action
        if not args.watch:
            return
        time.sleep(args.interval)


def add_loop_args(parser):
    parser.add_argument("--watch", action="store_true", help="持续盯盘；不加则只检查一次")
    parser.add_argument("--interval", type=int, default=20, help="持续盯盘轮询秒数，默认20")


def main():
    parser = argparse.ArgumentParser(description="实时盯盘守门员：触发买入/卖出动作，不自动下单")
    sub = parser.add_subparsers(dest="cmd", required=True)

    buy = sub.add_parser("buy", help="候选票买入触发监控")
    buy.add_argument("code", help="股票代码")
    buy.add_argument("--name", default="", help="股票名；不填则用实时行情名")
    buy.add_argument("--entry", nargs=2, type=float, required=True, metavar=("LOW", "HIGH"), help="买入区间")
    buy.add_argument("--stop", type=float, required=True, help="硬止损")
    buy.add_argument("--soft-stop", type=float, required=True, help="软止损")
    buy.add_argument("--strategy", required=True, choices=("S1", "S2", "S3", "S4"), help="策略")
    buy.add_argument("--pos", required=True, help="计划仓位，如 1/4、1/8、0.125")
    buy.add_argument("--confidence", required=True, help="置信度")
    buy.add_argument("--invalidation", required=True, help="逻辑失效/反证条件")
    buy.add_argument("--grade", default="", help="等级")
    buy.add_argument("--score", type=int, default=None, help="评分")
    buy.add_argument("--target", type=float, default=None, help="止盈1")
    buy.add_argument("--target2", type=float, default=None, help="止盈2")
    buy.add_argument("--shares", type=int, default=None, help="计划股数")
    buy.add_argument("--amount", type=float, default=None, help="计划金额")
    buy.add_argument("--source", default="realtime_guard", help="计划来源")
    buy.add_argument("--expected", default="D2", help="验证/持有周期，如 D2/D3")
    buy.add_argument("--market-mode", default="", help="买入计划对应M档；不填则取当前")
    buy.add_argument("--chase-pct", type=float, default=3.0, help="高于买入区上沿多少算追高，默认3%%")
    buy.add_argument("--max-pct", type=float, default=9.2, help="当日涨幅上限，默认9.2%%")
    buy.add_argument("--min-pct", type=float, default=-1.5, help="入场确认允许的最低当日涨跌幅，默认-1.5%%")
    buy.add_argument("--min-range-pos", type=float, default=0.50, help="日内区间位置确认，默认0.50")
    buy.add_argument("--min-outer-inner", type=float, default=1.00, help="外/内比确认，默认1.00")
    buy.add_argument("--confirm", type=int, default=2, help="需要满足的确认项数量，默认2")
    buy.add_argument("--max-account-risk", type=float, default=1.25, help="单笔账户风险上限百分比，默认1.25")
    add_loop_args(buy)
    buy.set_defaults(func=lambda args: loop(args, run_buy_once))

    sell = sub.add_parser("sell", help="持仓卖出触发监控")
    sell.add_argument("--id", required=True, help="trade_log 未平仓ID")
    sell.add_argument("--meltdown-pct", type=float, default=6.0, help="盘中熔断跌幅，默认6%%")
    sell.add_argument("--trail-pct", type=float, default=None, help="移动止盈回落幅度；默认S4=3%%，其他=2.5%%")
    sell.add_argument("--exit-time", default="14:40", help="D2/D3或风险仓尾盘清仓时间，默认14:40")
    sell.add_argument("--close-check-time", default="14:50", help="收盘止损确认时间，默认14:50")
    add_loop_args(sell)
    sell.set_defaults(func=lambda args: loop(args, run_sell_once))

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n已停止盯盘。")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
