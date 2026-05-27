#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF基础池维护脚本。

V1目标：拉取全市场ETF列表，合并可用行情/净值/规模字段，并按长期ETF体系分类。
不写入数据库；如需保存结果，显式传入 --csv。
"""
from __future__ import annotations

import argparse
import re
import sys
import warnings
from pathlib import Path
from typing import Callable

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

import akshare as ak
import numpy as np
import pandas as pd


CATEGORY_PRIORITY = {
    "CORE_DIVIDEND": 1,
    "CORE_BROAD": 2,
    "DEFENSIVE": 3,
    "SATELLITE": 4,
    "WATCH": 9,
}

DIVIDEND_KEYWORDS = (
    "红利", "股息", "高股息", "低波", "央企", "价值", "银行", "公用", "电力"
)
DEFENSIVE_KEYWORDS = (
    "国债", "政金债", "地方债", "信用债", "公司债", "短债", "短融", "债券", "债ETF",
    "可转债", "货币", "现金", "日利", "添益", "快线", "保证金", "黄金", "金ETF", "白银"
)
BROAD_KEYWORDS = (
    "沪深300", "中证A500", "中证500", "中证1000", "中证2000", "中证800",
    "上证50", "上证180", "深证100", "创业板", "创业板50", "科创50",
    "科创100", "A50", "MSCI中国A50", "双创", "宽基", "国证2000"
)
QDII_KEYWORDS = (
    "QDII", "纳指", "纳斯达克", "标普", "道琼斯", "恒生", "港股", "中概",
    "日经", "德国", "法国", "印度", "东南亚", "沙特", "亚太", "海外", "全球"
)

THEME_KEYWORDS = {
    "红利低波": ("红利", "股息", "低波", "高股息", "央企"),
    "宽基": BROAD_KEYWORDS,
    "防守": DEFENSIVE_KEYWORDS,
    "跨境": QDII_KEYWORDS,
    "科技": ("半导体", "芯片", "集成电路", "人工智能", "AI", "机器人", "软件", "计算机", "通信", "电子"),
    "新能源": ("新能源", "光伏", "电池", "锂电", "储能", "电动车", "汽车"),
    "医药": ("医药", "医疗", "创新药", "生物", "疫苗", "中药"),
    "消费": ("消费", "食品", "酒", "家电", "农业", "养殖"),
    "金融地产": ("银行", "证券", "保险", "金融", "地产"),
    "周期资源": ("有色", "煤炭", "钢铁", "化工", "稀土", "能源", "油气", "原油"),
    "军工": ("军工", "国防", "航天", "航空"),
}


def _to_float(value) -> float:
    if value is None:
        return np.nan
    try:
        if pd.isna(value):
            return np.nan
    except Exception:
        pass
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "---", "nan", "None"):
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def _clean_code(value) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def code_to_market(code: str) -> str:
    code = _clean_code(code)
    if code.startswith(("50", "51", "52", "56", "58")):
        return "sh"
    if code.startswith(("15", "16", "18")):
        return "sz"
    return ""


def code_to_symbol(code: str) -> str:
    market = code_to_market(code)
    return f"{market}{_clean_code(code)}" if market else _clean_code(code)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def classify_etf(name: str, fund_type: str = "") -> str:
    text = f"{name or ''}{fund_type or ''}"
    if not text or "ETF" not in text.upper() and "交易型" not in text:
        return "WATCH"
    if "联接" in text or "连接" in text:
        return "WATCH"
    if _contains_any(text, DEFENSIVE_KEYWORDS):
        return "DEFENSIVE"
    if _contains_any(text, DIVIDEND_KEYWORDS):
        return "CORE_DIVIDEND"
    if _contains_any(text, BROAD_KEYWORDS):
        return "CORE_BROAD"
    return "SATELLITE"


def detect_theme(name: str, category: str) -> str:
    text = name or ""
    for theme, keywords in THEME_KEYWORDS.items():
        if _contains_any(text, keywords):
            return theme
    return {
        "CORE_DIVIDEND": "红利低波",
        "CORE_BROAD": "宽基",
        "DEFENSIVE": "防守",
        "SATELLITE": "行业主题",
    }.get(category, "观察")


def is_qdii(name: str) -> bool:
    return _contains_any(name or "", QDII_KEYWORDS)


def _safe_fetch(name: str, func: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    try:
        df = func()
        if isinstance(df, pd.DataFrame):
            return df.copy()
    except Exception as exc:
        print(f"  ! {name} 获取失败: {type(exc).__name__}: {exc}")
    return pd.DataFrame()


def fetch_sina_quote() -> pd.DataFrame:
    df = _safe_fetch("Sina ETF行情", lambda: ak.fund_etf_category_sina(symbol="ETF基金"))
    if df.empty:
        return pd.DataFrame(columns=["code"])
    out = pd.DataFrame()
    out["code"] = df["代码"].map(_clean_code)
    out["symbol"] = df["代码"].astype(str)
    out["name_sina"] = df["名称"].astype(str)
    out["current_price"] = df["最新价"].map(_to_float)
    out["pct_today"] = df["涨跌幅"].map(_to_float)
    out["amount_today"] = df["成交额"].map(_to_float)
    out["volume_today"] = df["成交量"].map(_to_float)
    out["bid"] = df["买入"].map(_to_float)
    out["ask"] = df["卖出"].map(_to_float)
    return out.drop_duplicates("code")


def fetch_nav_daily() -> pd.DataFrame:
    df = _safe_fetch("东方财富ETF净值", ak.fund_etf_fund_daily_em)
    if df.empty:
        df = _safe_fetch("同花顺ETF净值", ak.fund_etf_spot_ths)
    if df.empty:
        return pd.DataFrame(columns=["code"])

    out = pd.DataFrame()
    out["code"] = df["基金代码"].map(_clean_code)
    name_col = "基金简称" if "基金简称" in df.columns else "基金名称"
    out["name_nav"] = df[name_col].astype(str)
    out["fund_type"] = df["类型"].astype(str) if "类型" in df.columns else df.get("基金类型", "").astype(str)

    nav_cols = [col for col in df.columns if col.endswith("单位净值")]
    acc_cols = [col for col in df.columns if col.endswith("累计净值")]
    out["nav_value"] = df[nav_cols[0]].map(_to_float) if nav_cols else np.nan
    out["acc_nav_value"] = df[acc_cols[0]].map(_to_float) if acc_cols else np.nan
    out["nav_growth_pct"] = df["增长率"].map(_to_float) if "增长率" in df.columns else np.nan
    out["premium_discount_pct"] = df["折价率"].map(_to_float) if "折价率" in df.columns else np.nan
    if "市价" in df.columns:
        out["market_price_nav_source"] = df["市价"].map(_to_float)
    return out.drop_duplicates("code")


def fetch_scale() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for name, func in (
        ("上交所ETF份额", ak.fund_etf_scale_sse),
        ("深交所ETF份额", ak.fund_etf_scale_szse),
    ):
        df = _safe_fetch(name, func)
        if df.empty or "基金代码" not in df.columns:
            continue
        out = pd.DataFrame()
        out["code"] = df["基金代码"].map(_clean_code)
        out["scale_name"] = df.get("基金简称", "").astype(str)
        out["fund_share"] = df.get("基金份额", np.nan).map(_to_float)
        out["scale_date"] = df.get("统计日期", "")
        frames.append(out)
    if not frames:
        return pd.DataFrame(columns=["code"])
    scale = pd.concat(frames, ignore_index=True)
    scale = scale.sort_values(["code", "scale_date"]).drop_duplicates("code", keep="last")
    return scale


def build_etf_pool() -> pd.DataFrame:
    quote = fetch_sina_quote()
    nav = fetch_nav_daily()
    scale = fetch_scale()

    pool = quote.merge(nav, on="code", how="outer").merge(scale, on="code", how="left")
    if pool.empty:
        return pool

    pool["name"] = pool.get("name_nav", "").fillna("")
    if "name_sina" in pool.columns:
        pool["name"] = pool["name"].mask(pool["name"].eq(""), pool["name_sina"].fillna(""))
    pool["name"] = pool["name"].astype(str).str.strip()
    pool["symbol"] = pool["code"].map(code_to_symbol)
    pool["market"] = pool["code"].map(code_to_market)
    pool["current_price"] = pool["current_price"].fillna(pool.get("market_price_nav_source", np.nan))
    pool["estimated_size_yi"] = pool["fund_share"] * pool["current_price"] / 100000000
    pool["category"] = pool.apply(lambda row: classify_etf(row["name"], row.get("fund_type", "")), axis=1)
    pool["theme"] = pool.apply(lambda row: detect_theme(row["name"], row["category"]), axis=1)
    pool["is_qdii"] = pool["name"].map(is_qdii)
    pool["category_priority"] = pool["category"].map(CATEGORY_PRIORITY).fillna(9)
    pool["amount_today_yi"] = pool["amount_today"] / 100000000
    pool["data_issues"] = pool.apply(_data_issues, axis=1)
    pool = pool.sort_values(["category_priority", "amount_today"], ascending=[True, False])
    return pool.reset_index(drop=True)


def _data_issues(row: pd.Series) -> str:
    issues: list[str] = []
    if not row.get("name"):
        issues.append("缺名称")
    if pd.isna(row.get("current_price")):
        issues.append("缺市价")
    if pd.isna(row.get("amount_today")):
        issues.append("缺成交额")
    if pd.isna(row.get("premium_discount_pct")):
        issues.append("缺折溢价")
    if pd.isna(row.get("estimated_size_yi")):
        issues.append("缺规模")
    return ",".join(issues)


def _print_summary(df: pd.DataFrame, top: int) -> None:
    if df.empty:
        print("  未获取到ETF基础池。")
        return
    print("=" * 88)
    print("  ETF基础池 V1")
    print("=" * 88)
    print()
    print(f"  ETF数量: {len(df)}")
    print("  分类统计:")
    for category, count in df["category"].value_counts().items():
        print(f"    {category:<14s} {count:>4d}")
    print()

    cols = [
        "code", "name", "category", "theme", "current_price", "pct_today",
        "amount_today_yi", "premium_discount_pct", "estimated_size_yi", "data_issues",
    ]
    show = df.loc[:, [col for col in cols if col in df.columns]].head(top).copy()
    for col in ("current_price", "pct_today", "amount_today_yi", "premium_discount_pct", "estimated_size_yi"):
        if col in show.columns:
            show[col] = show[col].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    print(show.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF基础池维护：拉取列表、合并字段、自动分类")
    parser.add_argument("--category", choices=sorted(CATEGORY_PRIORITY), help="只显示某一类ETF")
    parser.add_argument("--theme", help="按主题关键字过滤")
    parser.add_argument("--top", type=int, default=30, help="终端显示前N只，默认30")
    parser.add_argument("--csv", help="可选：保存CSV到指定路径")
    args = parser.parse_args()

    df = build_etf_pool()
    if args.category and not df.empty:
        df = df[df["category"] == args.category]
    if args.theme and not df.empty:
        pattern = re.escape(args.theme)
        df = df[df["name"].str.contains(pattern, na=False) | df["theme"].str.contains(pattern, na=False)]

    _print_summary(df, args.top)
    if args.csv and not df.empty:
        out_path = Path(args.csv).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  已保存: {out_path}")


if __name__ == "__main__":
    main()