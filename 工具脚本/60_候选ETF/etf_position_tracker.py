#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF持仓/建仓跟踪器 V1。

用途：
1. 初始化ETF持仓CSV模板。
2. 合并持仓、当前价格、评分结果，输出补仓/暂停/再平衡提醒。
3. 根据评分CSV生成候选ETF首批买入计划。

说明：持仓CSV由用户维护，不自动写入交易记录，避免长线ETF和短波交易日志混在一起。
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date
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

import numpy as np
import pandas as pd

from etf_pool import build_etf_pool


TEMPLATE_COLUMNS = [
    "date", "code", "name", "category", "target_weight", "shares", "avg_cost",
    "completed_batches", "max_batches", "status", "remark",
]

BATCH_RATIOS = [0.30, 0.30, 0.20, 0.20]
CATEGORY_TARGETS = {
    "CORE_DIVIDEND": 0.40,
    "CORE_BROAD": 0.30,
    "DEFENSIVE": 0.15,
    "SATELLITE": 0.15,
}
DEFAULT_SINGLE_TARGETS = {
    "CORE_DIVIDEND": 0.15,
    "CORE_BROAD": 0.15,
    "DEFENSIVE": 0.10,
    "SATELLITE": 0.05,
}
SINGLE_CAPS = {
    "CORE_DIVIDEND": 0.20,
    "CORE_BROAD": 0.20,
    "DEFENSIVE": 0.15,
    "SATELLITE": 0.08,
}


def clean_code(value) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "---", "nan", "None"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def parse_weight(value) -> float:
    number = to_float(value, default=0.0)
    if number > 1:
        return number / 100
    return max(0.0, number)


def fmt_money(value: float) -> str:
    if pd.isna(value):
        return ""
    return f"{value:,.0f}"


def fmt_pct(value: float, digits: int = 1) -> str:
    if pd.isna(value):
        return ""
    return f"{value * 100:+.{digits}f}%"


def fmt_weight(value: float) -> str:
    if pd.isna(value):
        return ""
    return f"{value * 100:.1f}%"


def init_template(csv_path: Path, force: bool, with_sample: bool) -> None:
    if csv_path.exists() and not force:
        print(f"  文件已存在: {csv_path}")
        print("  如需覆盖，添加 --force。")
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if with_sample:
        template = pd.DataFrame([
            {
                "date": date.today().isoformat(),
                "code": "510880",
                "name": "红利ETF华泰柏瑞",
                "category": "CORE_DIVIDEND",
                "target_weight": "15%",
                "shares": 0,
                "avg_cost": 0,
                "completed_batches": 0,
                "max_batches": 4,
                "status": "watch",
                "remark": "样例行，可删除",
            }
        ], columns=TEMPLATE_COLUMNS)
    else:
        template = pd.DataFrame(columns=TEMPLATE_COLUMNS)
    template.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"  已创建ETF持仓模板: {csv_path}")


def load_positions(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"未找到持仓CSV: {csv_path}")
    positions = pd.read_csv(csv_path, dtype={"code": str})
    for column in TEMPLATE_COLUMNS:
        if column not in positions.columns:
            positions[column] = "" if column in ("date", "code", "name", "category", "status", "remark") else 0
    positions["code"] = positions["code"].map(clean_code)
    positions = positions[positions["code"] != ""].copy()
    positions["target_weight_norm"] = positions["target_weight"].map(parse_weight)
    positions["shares"] = positions["shares"].map(to_float)
    positions["avg_cost"] = positions["avg_cost"].map(to_float)
    positions["completed_batches"] = positions["completed_batches"].map(lambda value: int(to_float(value, 0)))
    positions["max_batches"] = positions["max_batches"].map(lambda value: int(to_float(value, 4)) or 4)
    return positions


def load_scores(csv_path: Path | None) -> pd.DataFrame:
    if csv_path is None:
        return pd.DataFrame(columns=["code"])
    if not csv_path.exists():
        raise FileNotFoundError(f"未找到评分CSV: {csv_path}")
    scores = pd.read_csv(csv_path, dtype={"code": str})
    if "code" not in scores.columns:
        raise ValueError("评分CSV缺少 code 字段")
    scores["code"] = scores["code"].map(clean_code)
    keep_columns = [
        "code", "category", "theme", "total_score", "grade", "action", "reason",
        "ma_state", "max_drawdown_3y", "return_1y", "avg_amount_20d", "price_vs_ma20_pct",
    ]
    for column in keep_columns:
        if column not in scores.columns:
            scores[column] = np.nan
    return scores[keep_columns].drop_duplicates("code", keep="first")


def current_pool() -> pd.DataFrame:
    pool = build_etf_pool()
    if pool.empty:
        return pd.DataFrame(columns=["code"])
    keep_columns = [
        "code", "name", "category", "theme", "current_price", "pct_today", "amount_today_yi",
        "estimated_size_yi", "data_issues",
    ]
    for column in keep_columns:
        if column not in pool.columns:
            pool[column] = np.nan
    return pool[keep_columns].drop_duplicates("code", keep="first")


def next_batch_ratio(completed_batches: int, max_batches: int) -> float:
    if completed_batches >= max_batches:
        return 0.0
    batch_index = min(completed_batches, len(BATCH_RATIOS) - 1)
    return BATCH_RATIOS[batch_index]


def tracker_action(row: pd.Series, gap_threshold: float) -> str:
    grade = str(row.get("grade", "")).upper()
    score_action = str(row.get("action", ""))
    current_weight = row.get("current_weight", 0.0)
    target_weight = row.get("target_weight_norm", 0.0)
    gap_value = row.get("gap_value", 0.0)
    completed_batches = int(row.get("completed_batches", 0))
    max_batches = int(row.get("max_batches", 4))

    if grade == "D" or "淘汰" in score_action:
        return "暂停补仓/准备替换"
    if current_weight > target_weight + 0.05:
        return "超目标5%+，季度再平衡减仓"
    if grade == "C" or "观察" in score_action:
        return "持有观察，不补仓"
    if "等待" in score_action:
        return "等待回踩，不追"
    if gap_value > gap_threshold and completed_batches < max_batches:
        return f"可补第{completed_batches + 1}批"
    if completed_batches >= max_batches:
        return "已完成计划，持有复核"
    return "持有，等下一触发点"


def build_report(positions: pd.DataFrame, scores: pd.DataFrame, cash: float, portfolio_value: float | None) -> pd.DataFrame:
    pool = current_pool()
    merged = positions.merge(pool, on="code", how="left", suffixes=("", "_pool"))
    if not scores.empty:
        merged = merged.merge(scores, on="code", how="left", suffixes=("", "_score"))
    for column, default in {
        "grade": "",
        "total_score": np.nan,
        "action": "",
        "reason": "",
        "ma_state": "",
        "max_drawdown_3y": np.nan,
        "return_1y": np.nan,
        "avg_amount_20d": np.nan,
        "price_vs_ma20_pct": np.nan,
    }.items():
        if column not in merged.columns:
            merged[column] = default

    merged["name_final"] = merged["name"].fillna("")
    if "name_pool" in merged.columns:
        merged["name_final"] = merged["name_final"].mask(merged["name_final"].eq(""), merged["name_pool"].fillna(""))
    if "category_score" in merged.columns:
        merged["category_final"] = merged["category"].fillna("").mask(merged["category"].fillna("").eq(""), merged["category_score"].fillna(""))
    else:
        merged["category_final"] = merged["category"].fillna("")
    if "category_pool" in merged.columns:
        merged["category_final"] = merged["category_final"].mask(merged["category_final"].eq(""), merged["category_pool"].fillna(""))

    merged["current_price"] = merged["current_price"].map(lambda value: to_float(value, np.nan))
    merged["market_value"] = merged["shares"] * merged["current_price"]
    merged["cost_value"] = merged["shares"] * merged["avg_cost"]
    merged["pnl_pct"] = np.where(merged["cost_value"] > 0, merged["market_value"] / merged["cost_value"] - 1, np.nan)
    total_market_value = float(merged["market_value"].fillna(0).sum())
    total_value = portfolio_value if portfolio_value is not None else total_market_value + cash
    if total_value <= 0:
        total_value = total_market_value + cash
    merged["portfolio_value"] = total_value
    merged["current_weight"] = np.where(total_value > 0, merged["market_value"] / total_value, 0.0)
    merged["target_value"] = merged["target_weight_norm"] * total_value
    merged["gap_value"] = merged["target_value"] - merged["market_value"].fillna(0)
    merged["next_batch_ratio"] = merged.apply(lambda row: next_batch_ratio(int(row["completed_batches"]), int(row["max_batches"])), axis=1)
    merged["next_batch_value"] = np.where(
        merged["gap_value"] > 0,
        np.minimum(merged["target_value"] * merged["next_batch_ratio"], merged["gap_value"]),
        0,
    )
    gap_threshold = max(total_value * 0.01, 1000)
    merged["tracker_action"] = merged.apply(lambda row: tracker_action(row, gap_threshold), axis=1)
    return merged


def print_report(report: pd.DataFrame, cash: float) -> None:
    print("=" * 104)
    print("  ETF持仓/建仓跟踪器 V1")
    print("=" * 104)
    if report.empty:
        print("  持仓表为空。")
        return
    portfolio_value = float(report["portfolio_value"].iloc[0])
    market_value = float(report["market_value"].fillna(0).sum())
    print(f"  ETF市值: {fmt_money(market_value)}  现金: {fmt_money(cash)}  组合口径: {fmt_money(portfolio_value)}")
    print()

    display = report[[
        "code", "name_final", "category_final", "target_weight_norm", "current_weight",
        "market_value", "pnl_pct", "grade", "total_score", "action", "completed_batches",
        "next_batch_value", "tracker_action", "reason",
    ]].copy()
    display = display.rename(columns={
        "name_final": "name",
        "category_final": "category",
        "target_weight_norm": "target",
        "current_weight": "weight",
        "market_value": "value",
        "pnl_pct": "pnl",
        "total_score": "score",
        "completed_batches": "batches",
        "next_batch_value": "next_buy",
        "tracker_action": "tracker",
    })
    display["target"] = display["target"].map(fmt_weight)
    display["weight"] = display["weight"].map(fmt_weight)
    display["value"] = display["value"].map(fmt_money)
    display["pnl"] = display["pnl"].map(fmt_pct)
    display["score"] = display["score"].map(lambda value: "" if pd.isna(value) else f"{value:.0f}")
    display["next_buy"] = display["next_buy"].map(fmt_money)
    print(display.to_string(index=False))

    category = report.groupby("category_final")["market_value"].sum().sort_values(ascending=False)
    print()
    print("  -- 分类占比 --")
    for category_name, value in category.items():
        actual_weight = value / portfolio_value if portfolio_value else 0
        target_weight = CATEGORY_TARGETS.get(category_name, np.nan)
        target_text = fmt_weight(target_weight) if pd.notna(target_weight) else ""
        print(f"  {category_name:<14s} 当前 {fmt_weight(actual_weight):>7s}  目标 {target_text:>7s}")


def read_score_candidates(csv_path: Path) -> pd.DataFrame:
    scores = load_scores(csv_path)
    if scores.empty:
        return scores
    scores["total_score"] = scores["total_score"].map(lambda value: to_float(value, np.nan))
    scores["avg_amount_20d"] = scores["avg_amount_20d"].map(lambda value: to_float(value, np.nan))
    return scores


def build_buy_plan(scores: pd.DataFrame, portfolio_value: float, cash: float, category: str | None, top: int) -> pd.DataFrame:
    candidates = scores.copy()
    if category:
        candidates = candidates[candidates["category"] == category]
    candidates = candidates[~candidates["action"].fillna("").str.contains("淘汰|观察", regex=True)]
    candidates = candidates[candidates["grade"].fillna("").isin(["A", "B"])]
    if candidates.empty:
        return candidates
    candidates = candidates.sort_values(["grade", "total_score", "avg_amount_20d"], ascending=[True, False, False]).head(top).copy()
    candidates["single_target_weight"] = candidates["category"].map(DEFAULT_SINGLE_TARGETS).fillna(0.05)
    candidates["single_cap"] = candidates["category"].map(SINGLE_CAPS).fillna(0.08)
    candidates["first_batch_value"] = portfolio_value * candidates["single_target_weight"] * BATCH_RATIOS[0]
    if cash > 0 and candidates["first_batch_value"].sum() > cash:
        scale = cash / candidates["first_batch_value"].sum()
        candidates["first_batch_value"] = candidates["first_batch_value"] * scale
    return candidates


def print_buy_plan(plan: pd.DataFrame, portfolio_value: float, cash: float) -> None:
    print("=" * 104)
    print("  ETF候选首批买入计划 V1")
    print("=" * 104)
    print(f"  组合口径: {fmt_money(portfolio_value)}  可用现金: {fmt_money(cash)}")
    print()
    if plan.empty:
        print("  当前没有满足 A/B 且非观察/淘汰的候选。")
        return
    display = plan[[
        "code", "category", "theme", "total_score", "grade", "action", "single_target_weight",
        "first_batch_value", "reason",
    ]].copy()
    display = display.rename(columns={
        "total_score": "score",
        "single_target_weight": "target",
        "first_batch_value": "first_buy",
    })
    display["score"] = display["score"].map(lambda value: "" if pd.isna(value) else f"{value:.0f}")
    display["target"] = display["target"].map(fmt_weight)
    display["first_buy"] = display["first_buy"].map(fmt_money)
    print(display.to_string(index=False))
    print()
    print("  执行纪律: A级才允许按首批计划买；B级只小仓或等回踩；出现等待回踩就不追价。")


def command_report(args: argparse.Namespace) -> None:
    positions = load_positions(Path(args.positions).expanduser())
    scores = load_scores(Path(args.scores).expanduser() if args.scores else None)
    report = build_report(
        positions=positions,
        scores=scores,
        cash=args.cash,
        portfolio_value=args.portfolio_value,
    )
    print_report(report, args.cash)


def command_buy_plan(args: argparse.Namespace) -> None:
    scores = read_score_candidates(Path(args.scores).expanduser())
    portfolio_value = args.portfolio_value
    if portfolio_value <= 0:
        raise ValueError("buy-plan 必须传入 --portfolio-value")
    plan = build_buy_plan(
        scores=scores,
        portfolio_value=portfolio_value,
        cash=args.cash,
        category=args.category,
        top=args.top,
    )
    print_buy_plan(plan, portfolio_value, args.cash)


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF持仓/建仓跟踪器")
    subparsers = parser.add_subparsers(dest="cmd")

    init_parser = subparsers.add_parser("init", help="初始化ETF持仓CSV模板")
    init_parser.add_argument("--csv", default="每日复盘/ETF持仓.csv", help="持仓CSV路径")
    init_parser.add_argument("--force", action="store_true", help="覆盖已存在文件")
    init_parser.add_argument("--sample", action="store_true", help="写入一行样例数据")
    init_parser.set_defaults(func=lambda args: init_template(Path(args.csv).expanduser(), args.force, args.sample))

    report_parser = subparsers.add_parser("report", help="输出ETF持仓补仓/再平衡报告")
    report_parser.add_argument("--positions", default="每日复盘/ETF持仓.csv", help="持仓CSV路径")
    report_parser.add_argument("--scores", help="评分CSV路径，建议由 etf_screener.py --csv 生成")
    report_parser.add_argument("--cash", type=float, default=0.0, help="ETF账户可用现金")
    report_parser.add_argument("--portfolio-value", type=float, help="ETF账户总资产口径；不传则用持仓市值+现金")
    report_parser.set_defaults(func=command_report)

    plan_parser = subparsers.add_parser("buy-plan", help="根据评分CSV生成候选ETF首批买入计划")
    plan_parser.add_argument("--scores", required=True, help="评分CSV路径，需由 etf_screener.py --csv 生成")
    plan_parser.add_argument("--portfolio-value", type=float, required=True, help="ETF账户总资产口径")
    plan_parser.add_argument("--cash", type=float, default=0.0, help="ETF账户可用现金；>0时按现金缩放首批金额")
    plan_parser.add_argument("--category", choices=sorted(CATEGORY_TARGETS), help="只输出某一类候选")
    plan_parser.add_argument("--top", type=int, default=8, help="输出前N只候选")
    plan_parser.set_defaults(func=command_buy_plan)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()