#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF稳健收益评分器 V1。

流程：
1. 调用 etf_pool.py 构建基础池。
2. 拉取ETF日K，计算收益、波动、回撤、趋势、成交额。
3. 按 ETF筛选规则.md 输出 A/B/C/D 候选。

V1刻意不依赖数据库；规模、费率、跟踪误差、估值分位等字段缺失时先用保守代理分。
"""
from __future__ import annotations

import argparse
import math
import sys
import warnings
from datetime import datetime, timedelta
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

import akshare as ak
import numpy as np
import pandas as pd

from etf_pool import build_etf_pool, code_to_symbol

# 数据库缓存（MySQL etf_kline_daily 表）。导入失败时降级为纯接口模式，不影响 V1 行为。
try:
    import db_cache as _db_cache  # type: ignore
    _DB_AVAILABLE = True
except Exception as _exc:  # pragma: no cover - DB 不可用时的兜底
    _db_cache = None
    _DB_AVAILABLE = False
    print(f"  ! db_cache 不可用，ETF 行情走接口直拉模式: {type(_exc).__name__}: {_exc}")


GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}
GRADE_BY_ORDER = {value: key for key, value in GRADE_ORDER.items()}

# 分类专属评分权重模板（安全性/收益效率/现金流与估值/执行质量 = 100）。
CATEGORY_WEIGHTS = {
    "CORE_DIVIDEND": {"safety": 45, "returns": 25, "income": 25, "execution": 5},
    "CORE_BROAD":    {"safety": 35, "returns": 40, "income": 15, "execution": 10},
    "DEFENSIVE":     {"safety": 55, "returns": 15, "income": 20, "execution": 10},
    "SATELLITE":     {"safety": 25, "returns": 45, "income": 20, "execution": 10},
}
# A 级阈值随市场模式动态调整（越底越敢买，越顶越谨慎）。
A_THRESHOLD_BY_MODE = {"M1": 78, "M2": 80, "M3": 82, "M4": 82, "M5": 85}
# 默认 A 级阈值，以及 BCD 间距（与 A 保持 -10/-20/-…）。
_DEFAULT_A = 82

_META_CACHE: pd.DataFrame | None = None
METRIC_DEFAULTS = {
    "hist_rows": 0,
    "first_date": "",
    "last_date": "",
    "last_close": np.nan,
    "annual_return_3y": np.nan,
    "return_1y": np.nan,
    "return_6m": np.nan,
    "return_3m": np.nan,
    "return_20d": np.nan,
    "volatility_1y": np.nan,
    "max_drawdown_3y": np.nan,
    "max_drawdown_1y": np.nan,
    "current_drawdown": np.nan,
    "return_dd_ratio_1y": np.nan,
    "ma20": np.nan,
    "ma60": np.nan,
    "ma120": np.nan,
    "ma250": np.nan,
    "ma_state": "无数据",
    "price_vs_ma20_pct": np.nan,
    "pct_from_250_high": np.nan,
    "avg_amount_20d": np.nan,
    "avg_amount_60d": np.nan,
    "amount_stability": np.nan,
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


def _safe_div(num: float, den: float) -> float:
    if den is None or pd.isna(den) or den == 0:
        return np.nan
    return num / den


def load_meta() -> pd.DataFrame:
    """读取 ETF 白名单 meta（优先 MySQL etf_meta 表 → 退回 etf_meta.csv → 空表）。"""
    global _META_CACHE
    if _META_CACHE is not None:
        return _META_CACHE

    # 优先库
    if _DB_AVAILABLE:
        try:
            df = _db_cache.read_etf_meta()
            if df is not None and not df.empty:
                df["code"] = df["code"].astype(str).str.zfill(6)
                for col in ("fund_size_yi", "fee_rate", "tracking_error_60d", "valuation_pct_5y",
                            "dividend_yield", "payout_ratio"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                _META_CACHE = df
                return _META_CACHE
        except Exception as exc:
            print(f"  ! 读取 etf_meta 表失败，转用 CSV: {type(exc).__name__}: {exc}")

    # CSV 兜底
    meta_path = Path(__file__).resolve().parent / "etf_meta.csv"
    if not meta_path.exists():
        _META_CACHE = pd.DataFrame()
        return _META_CACHE
    try:
        df = pd.read_csv(meta_path, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        for col in ("fund_size_yi", "fee_rate", "tracking_error_60d", "valuation_pct_5y",
                    "dividend_yield", "payout_ratio"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        _META_CACHE = df
    except Exception as exc:
        print(f"  ! 读取 etf_meta.csv 失败: {type(exc).__name__}: {exc}")
        _META_CACHE = pd.DataFrame()
    return _META_CACHE


def _a_threshold(mode: str) -> int:
    return A_THRESHOLD_BY_MODE.get(str(mode).upper(), _DEFAULT_A)


def _ret(close: pd.Series, days: int) -> float:
    if len(close) <= days:
        return np.nan
    base = close.iloc[-days - 1]
    if base == 0 or pd.isna(base):
        return np.nan
    return close.iloc[-1] / base - 1


def _max_drawdown(close: pd.Series) -> float:
    if close.empty:
        return np.nan
    peak = close.cummax()
    drawdown = close / peak - 1
    return float(drawdown.min())


def _fetch_hist_remote(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """从远端接口拉取指定区间的 ETF 日K（东财优先，新浪兜底）。返回规范化列。"""
    last_error = None
    try:
        df = ak.fund_etf_hist_em(
            symbol=str(code).zfill(6),
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
    except Exception as exc:
        last_error = exc
        df = pd.DataFrame()
    if df.empty:
        try:
            df = ak.fund_etf_hist_sina(symbol=code_to_symbol(str(code).zfill(6)))
        except Exception as exc:
            if last_error is not None:
                raise last_error
            raise exc
    if df.empty:
        return df
    rename = {
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turn",
    }
    df = df.rename(columns=rename)
    for col in ("open", "close", "high", "low", "volume", "amount", "pct_chg", "turn"):
        if col in df.columns:
            df[col] = df[col].map(_to_float)
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    if "pct_chg" not in df.columns:
        df["pct_chg"] = df["close"].pct_change() * 100
    # 规范 date 列为字符串 YYYY-MM-DD
    df["date"] = df["date"].astype(str).str[:10]
    return df


def fetch_hist(code: str, days: int, use_cache: bool = True) -> pd.DataFrame:
    """获取 ETF 最近 days 日K。

    缓存策略（use_cache=True 且 db_cache 可用时）：
      1. 先查库；若库内最新日期 < 今日(自然日)，仅从该日次日补到今日；
      2. 远端新数据回写库；
      3. 最终从库读取 days*1.8 + 45 自然日的尾部，截取最后 days 行返回。
    缓存关闭或 DB 不可用时退化为一次性远端拉取。
    """
    calendar_days = int(days * 1.8) + 45
    today = datetime.today().date()
    full_start = (datetime.today() - timedelta(days=calendar_days)).strftime("%Y%m%d")
    today_str = today.strftime("%Y%m%d")

    if not (use_cache and _DB_AVAILABLE):
        df = _fetch_hist_remote(code, full_start, today_str)
        if df.empty:
            return df
        return df.tail(days).reset_index(drop=True)

    code6 = str(code).zfill(6)
    last_db = _db_cache.get_etf_last_date(code6)  # 'YYYY-MM-DD' 或 None
    # 决定增量起点
    if last_db is None:
        fetch_start = full_start
    else:
        last_db_date = datetime.strptime(str(last_db)[:10], "%Y-%m-%d").date()
        if last_db_date >= today:
            fetch_start = None  # 库已是最新，跳过远端
        else:
            fetch_start = (last_db_date + timedelta(days=1)).strftime("%Y%m%d")

    if fetch_start is not None:
        try:
            new_df = _fetch_hist_remote(code6, fetch_start, today_str)
        except Exception as exc:
            print(f"  ! {code6} 远端拉取失败，使用现有库数据: {type(exc).__name__}: {exc}")
            new_df = pd.DataFrame()
        if not new_df.empty:
            # 适配 db_cache 的列名（pctChg）
            write_df = new_df.rename(columns={"pct_chg": "pctChg"}).copy()
            try:
                _db_cache.upsert_etf_kline_batch(code6, write_df)
            except Exception as exc:
                print(f"  ! {code6} 写入库失败: {type(exc).__name__}: {exc}")

    # 从库读取尾部
    db_start = (today - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    df = _db_cache.read_etf_kline(code6, start_date=db_start)
    if df.empty:
        return df
    # 统一列名：screener 期望 pct_chg
    if "pctChg" in df.columns and "pct_chg" not in df.columns:
        df = df.rename(columns={"pctChg": "pct_chg"})
    df["date"] = df["date"].astype(str).str[:10]
    return df.tail(days).reset_index(drop=True)


def compute_metrics(hist: pd.DataFrame) -> dict[str, float | str]:
    if hist.empty or "close" not in hist.columns:
        return dict(METRIC_DEFAULTS)
    close = hist["close"].astype(float)
    amount = hist.get("amount", pd.Series(dtype=float)).astype(float)
    pct = close.pct_change().dropna()

    close_3y = close.tail(min(len(close), 756))
    close_1y = close.tail(min(len(close), 252))
    high_250 = close.tail(min(len(close), 250)).max()
    last = close.iloc[-1]
    rows_3y = len(close_3y)
    annual_return_3y = np.nan
    if rows_3y > 60 and close_3y.iloc[0] > 0:
        annual_return_3y = (last / close_3y.iloc[0]) ** (252 / rows_3y) - 1

    ma20 = close.tail(20).mean() if len(close) >= 20 else np.nan
    ma60 = close.tail(60).mean() if len(close) >= 60 else np.nan
    ma120 = close.tail(120).mean() if len(close) >= 120 else np.nan
    ma250 = close.tail(250).mean() if len(close) >= 250 else np.nan
    if all(pd.notna(value) for value in (ma60, ma120, ma250)) and last > ma60 > ma120 > ma250:
        ma_state = "多头"
    elif pd.notna(ma60) and last > ma60:
        ma_state = "站上MA60"
    elif pd.notna(ma250) and last < ma250:
        ma_state = "跌破MA250"
    else:
        ma_state = "震荡"

    max_dd_3y = _max_drawdown(close_3y)
    max_dd_1y = _max_drawdown(close_1y)
    ret_1y = _ret(close, 250)
    ret_6m = _ret(close, 120)
    ret_3m = _ret(close, 60)
    ret_20d = _ret(close, 20)
    return_dd_ratio_1y = _safe_div(ret_1y, abs(max_dd_1y))

    return {
        "hist_rows": len(hist),
        "first_date": str(hist["date"].iloc[0]),
        "last_date": str(hist["date"].iloc[-1]),
        "last_close": last,
        "annual_return_3y": annual_return_3y,
        "return_1y": ret_1y,
        "return_6m": ret_6m,
        "return_3m": ret_3m,
        "return_20d": ret_20d,
        "volatility_1y": float(pct.tail(252).std() * math.sqrt(252)) if len(pct) >= 20 else np.nan,
        "max_drawdown_3y": max_dd_3y,
        "max_drawdown_1y": max_dd_1y,
        "current_drawdown": last / close.cummax().iloc[-1] - 1,
        "return_dd_ratio_1y": return_dd_ratio_1y,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma250": ma250,
        "ma_state": ma_state,
        "price_vs_ma20_pct": _safe_div(last, ma20) - 1 if pd.notna(ma20) else np.nan,
        "pct_from_250_high": _safe_div(last, high_250) - 1 if pd.notna(high_250) else np.nan,
        "avg_amount_20d": amount.tail(20).mean() if len(amount) >= 20 else np.nan,
        "avg_amount_60d": amount.tail(60).mean() if len(amount) >= 60 else np.nan,
        "amount_stability": _safe_div(amount.tail(20).mean(), amount.tail(60).mean()) if len(amount) >= 60 else np.nan,
    }


def _rank_pct(df: pd.DataFrame, column: str, higher_better: bool = True) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return df.groupby("category")[column].rank(pct=True, ascending=higher_better)


def add_rank_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["rank_return_3y"] = _rank_pct(out, "annual_return_3y", True)
    out["rank_return_1y"] = _rank_pct(out, "return_1y", True)
    out["rank_return_6m"] = _rank_pct(out, "return_6m", True)
    out["rank_return_dd"] = _rank_pct(out, "return_dd_ratio_1y", True)
    out["rank_low_vol"] = _rank_pct(out, "volatility_1y", False)
    out["rank_amount"] = _rank_pct(out, "avg_amount_20d", True)
    return out


def _score_from_rank(rank_value: float, max_score: int) -> int:
    if pd.isna(rank_value):
        return 0
    return int(round(max(0.0, min(1.0, rank_value)) * max_score))


def _score_drawdown(max_drawdown: float) -> int:
    if pd.isna(max_drawdown):
        return 0
    dd = abs(max_drawdown)
    if dd <= 0.20:
        return 12
    if dd <= 0.30:
        return 8
    if dd <= 0.35:
        return 4
    return 0


def _score_recovery(current_drawdown: float) -> int:
    if pd.isna(current_drawdown):
        return 0
    dd = abs(current_drawdown)
    if dd <= 0.03:
        return 8
    if dd <= 0.08:
        return 6
    if dd <= 0.15:
        return 3
    return 1


def _score_diversification(category: str, theme: str) -> int:
    if category == "CORE_BROAD":
        return 6
    if category == "DEFENSIVE":
        return 6
    if category == "CORE_DIVIDEND":
        return 5
    if theme == "跨境":
        return 4
    return 3


def _score_trend(ma_state: str, last: float = np.nan, ma60: float = np.nan,
                 ma120: float = np.nan, ma250: float = np.nan) -> int:
    """趋势评分 0-10（在原始「收益」子分内）。

    优化：MA250 ±3% 黏合区不直接给 0；跌破 MA250 但 MA60 仍向上视为疑似筑底，保留 3 分。
    """
    if ma_state == "多头":
        # 黏合加分：MA60-MA120 距离 < 1.5% 只给 8；其余多头给 10。
        if pd.notna(ma60) and pd.notna(ma120) and ma60 > 0:
            if abs(ma60 - ma120) / ma60 < 0.015:
                return 8
        return 10
    if ma_state == "站上MA60":
        return 6
    if ma_state == "跌破MA250":
        if pd.notna(last) and pd.notna(ma250) and ma250 > 0:
            # MA250 ±3% 黏合区
            if abs(last - ma250) / ma250 <= 0.03:
                return 5
        if pd.notna(ma60) and pd.notna(ma120) and ma60 > ma120:
            return 3  # 疑似筑底
        return 0
    return 2


def _score_income(row: pd.Series) -> int:
    """现金流与估值 0-15（后续按分类权重缩放）。

    估值优先使用 meta 里的 valuation_pct_5y（近5年分位，0-100）；
    缺失时 fallback 到 pct_from_250_high。
    """
    category = row["category"]
    theme = row.get("theme", "")

    dividend_yield = row.get("dividend_yield", np.nan)
    payout_ratio = row.get("payout_ratio", np.nan)

    dividend = 0
    continuity = 0
    if category == "CORE_DIVIDEND":
        # 股息率越高得分越高，没有给默认 4。
        if pd.notna(dividend_yield):
            dividend = 6 if dividend_yield >= 5 else 4 if dividend_yield >= 3 else 2
        else:
            dividend = 4
        continuity = 2
    elif category == "DEFENSIVE":
        dividend = 2
        continuity = 1
    elif category == "CORE_BROAD":
        dividend = 1

    valuation_pct = row.get("valuation_pct_5y", np.nan)
    if pd.notna(valuation_pct):
        if valuation_pct <= 30:
            valuation = 4
        elif valuation_pct <= 60:
            valuation = 3
        elif valuation_pct <= 85:
            valuation = 1
        else:
            valuation = 0
    else:
        pct_from_high = row.get("pct_from_250_high", np.nan)
        if pd.isna(pct_from_high):
            valuation = 0
        elif pct_from_high <= -0.15:
            valuation = 4
        elif pct_from_high <= -0.05:
            valuation = 3
        elif pct_from_high <= 0.05:
            valuation = 2
        else:
            valuation = 1

    quality = 2 if category in ("CORE_DIVIDEND", "CORE_BROAD") else 1 if category == "DEFENSIVE" else 0
    if theme in ("科技", "新能源"):
        quality = max(quality, 1)

    raw = min(15, dividend + continuity + valuation + quality)

    # 红利陷阱保护：派息率>80% 且利润增速<0 时压到上限 6。
    # （利润增速暂未接入时，仅看派息率。）
    if pd.notna(payout_ratio) and payout_ratio >= 0.8:
        raw = min(raw, 6)
    return raw


def _score_execution(row: pd.Series) -> int:
    """执行质量 0-10；优先用 meta 中的规模、费率、跟踪误差。"""
    size = row.get("fund_size_yi", np.nan)
    if pd.isna(size):
        size = row.get("estimated_size_yi", np.nan)
    fee_rate = row.get("fee_rate", np.nan)
    track_err = row.get("tracking_error_60d", np.nan)
    premium = abs(row.get("premium_discount_pct", np.nan))

    size_score = 1 if pd.isna(size) else 3 if size >= 100 else 2 if size >= 20 else 0
    amount_score = _score_from_rank(row.get("rank_amount", np.nan), 3)
    if pd.isna(fee_rate):
        fee_score = 1
    else:
        fee_score = 2 if fee_rate <= 0.30 else 1 if fee_rate <= 0.60 else 0
    if pd.isna(track_err):
        track_score = 1
    else:
        track_score = 2 if track_err <= 0.30 else 1 if track_err <= 0.80 else 0
    if pd.isna(premium):
        premium_score = 1
    elif premium <= 0.30:
        premium_score = 2
    elif premium <= 1.00:
        premium_score = 1
    else:
        premium_score = 0
    return min(10, size_score + amount_score + fee_score + track_score + premium_score)


def _cap_grade(grade: str, cap: str) -> str:
    return GRADE_BY_ORDER[min(GRADE_ORDER[grade], GRADE_ORDER[cap])]


def _base_grade(score: float) -> str:
    if score >= 82:
        return "A"
    if score >= 72:
        return "B"
    if score >= 62:
        return "C"
    return "D"


def _hard_filter(row: pd.Series, strict_scale: bool = False) -> tuple[bool, str]:
    category = row["category"]
    is_core = category in ("CORE_DIVIDEND", "CORE_BROAD")
    is_defensive = category == "DEFENSIVE"
    name = str(row.get("name", ""))
    is_qdii = bool(row.get("is_qdii", False))

    # 品种专属阈值（对应规则 V1.1 F1-F8）。
    if is_qdii:
        min_rows, min_amount, min_size = 378, 20000000, 5
        max_dd, max_premium, max_track = 0.40, 1.00, 2.0
    elif is_defensive and ("债" in name or "国债" in name or "信用债" in name):
        min_rows, min_amount, min_size = 252, 30000000, 30
        max_dd, max_premium, max_track = 0.08, 0.15, 1.0
    elif is_defensive and ("黄金" in name or "白银" in name):
        min_rows, min_amount, min_size = 504, 30000000, 10
        max_dd, max_premium, max_track = 0.25, 0.50, 2.0
    elif is_core:
        min_rows, min_amount, min_size = 504, 50000000, 20
        max_dd, max_premium, max_track = 0.35, 0.30, 1.5
    else:
        min_rows, min_amount, min_size = 252, 30000000, 8
        max_dd, max_premium, max_track = 0.99, 0.30, 1.5  # SATELLITE 不强制回撤

    reasons: list[str] = []
    if row.get("hist_rows", 0) < min_rows:
        reasons.append(f"F1历史不足{min_rows}日")
    if pd.isna(row.get("avg_amount_20d")) or row.get("avg_amount_20d", 0) < min_amount:
        reasons.append(f"F3成交额不足{min_amount / 100000000:.2f}亿")
    if pd.notna(row.get("amount_stability")) and row.get("amount_stability") < 0.35:
        reasons.append("F4成交萎缩")

    # 规模：优先用 meta 里的 fund_size_yi，其次 estimated_size_yi。
    size_yi = row.get("fund_size_yi", np.nan)
    if pd.isna(size_yi):
        size_yi = row.get("estimated_size_yi", np.nan)
    if strict_scale and pd.isna(size_yi):
        reasons.append("F2缺规模")
    if pd.notna(size_yi) and size_yi < min_size:
        reasons.append(f"F2规模<{min_size}亿")

    # 费率、跟踪误差、溢价（仅在 meta 提供时才检查）。
    fee = row.get("fee_rate", np.nan)
    if pd.notna(fee):
        fee_cap = 1.20 if is_qdii else 0.30 if (is_defensive and "债" in name) else 0.60
        if fee > fee_cap:
            reasons.append(f"F5费率{fee:.2f}>{fee_cap:.2f}")
    track = row.get("tracking_error_60d", np.nan)
    if pd.notna(track) and track > max_track:
        reasons.append(f"F7跟踪{track:.2f}>{max_track:.2f}")
    premium = abs(row.get("premium_discount_pct", np.nan)) if pd.notna(row.get("premium_discount_pct")) else np.nan
    if pd.notna(premium) and premium > max_premium:
        reasons.append(f"F6溢价{premium:.2f}>{max_premium:.2f}")

    if is_core and pd.notna(row.get("max_drawdown_3y")) and abs(row.get("max_drawdown_3y")) > max_dd:
        reasons.append(f"F8核心回撤>{max_dd * 100:.0f}%")
    if is_defensive and pd.notna(row.get("max_drawdown_3y")) and abs(row.get("max_drawdown_3y")) > max_dd:
        reasons.append(f"F8防守回撤>{max_dd * 100:.0f}%")
    return not reasons, ";".join(reasons)


def score_row(row: pd.Series, strict_scale: bool = False, mode: str = "") -> dict[str, float | str | bool]:
    """以原始 40/35/15/10 子分计算后，按分类权重缩放合成 100 分。"""
    safety_raw = (
        _score_drawdown(row.get("max_drawdown_3y", np.nan))
        + _score_from_rank(row.get("rank_low_vol", np.nan), 8)
        + _score_recovery(row.get("current_drawdown", np.nan))
        + _score_diversification(row["category"], row.get("theme", ""))
        + 3  # 跟踪质量：在有 meta tracking_error_60d 时可以加分，暂为保守基础分。
    )
    returns_raw = (
        _score_from_rank(row.get("rank_return_3y", np.nan), 10)
        + _score_from_rank(row.get("rank_return_1y", np.nan), 6)
        + _score_from_rank(row.get("rank_return_6m", np.nan), 6)
        + _score_trend(
            row.get("ma_state", ""),
            row.get("last_close", np.nan),
            row.get("ma60", np.nan),
            row.get("ma120", np.nan),
            row.get("ma250", np.nan),
        )
        + _score_from_rank(row.get("rank_return_dd", np.nan), 6)
    )
    # 趋势上限改为 10，所以 returns_raw 上限 = 10+6+6+10+6 = 38，超出封顶 35。
    returns_raw = min(38, returns_raw)
    income_raw = _score_income(row)
    execution_raw = _score_execution(row)

    # 按分类权重模板线性缩放。
    weights = CATEGORY_WEIGHTS.get(row["category"], CATEGORY_WEIGHTS["CORE_BROAD"])
    safety = round(safety_raw / 40 * weights["safety"])
    returns = round(returns_raw / 38 * weights["returns"])
    income = round(income_raw / 15 * weights["income"])
    execution = round(execution_raw / 10 * weights["execution"])
    total = min(100, safety + returns + income + execution)

    # A 级阈值随市场模式动态调整。
    a_line = _a_threshold(mode)
    if total >= a_line:
        grade = "A"
    elif total >= a_line - 10:
        grade = "B"
    elif total >= a_line - 20:
        grade = "C"
    else:
        grade = "D"
    raw_grade = grade

    # 本类折算后的阈值，避免不同权重下「28分」进不去。
    if safety < weights["safety"] * 0.70:
        grade = _cap_grade(grade, "B")
    if execution < weights["execution"] * 0.60:
        grade = _cap_grade(grade, "C")
    if row["category"] == "CORE_DIVIDEND" and income < weights["income"] * 0.32:
        grade = _cap_grade(grade, "B")
    if row["category"] == "SATELLITE":
        grade = _cap_grade(grade, "B")

    hard_ok, hard_reason = _hard_filter(row, strict_scale=strict_scale)
    if not hard_ok:
        if "成交额不足" in hard_reason or "回撤>" in hard_reason or "费率" in hard_reason:
            grade = "D"
        else:
            grade = _cap_grade(grade, "C")

    action = _action(row, grade, hard_ok, hard_reason)
    return {
        "safety_score": safety,
        "return_score": returns,
        "income_score": income,
        "execution_score": execution,
        "total_score": total,
        "raw_grade": raw_grade,
        "grade": grade,
        "hard_filter_ok": hard_ok,
        "hard_filter_reason": hard_reason,
        "action": action,
        "reason": _reason(row, grade, hard_reason),
    }


def _action(row: pd.Series, grade: str, hard_ok: bool, hard_reason: str) -> str:
    if not hard_ok:
        if "历史不足" in hard_reason:
            return "观察"
        return "淘汰"
    category = row["category"]

    # 估值止盈（长线必备）：仅在 meta 提供估值分位时生效。
    valuation_pct = row.get("valuation_pct_5y", np.nan)
    if pd.notna(valuation_pct):
        if category == "CORE_BROAD" and valuation_pct >= 99:
            return "估值极升-减至50%"
        if category == "CORE_BROAD" and valuation_pct >= 95:
            return "估值过热-减至60%"
        if category == "CORE_DIVIDEND" and valuation_pct >= 90:
            return "红利过热-减至70%"

    overheat_limit = 0.08 if category in ("CORE_DIVIDEND", "CORE_BROAD", "DEFENSIVE") else 0.12
    price_vs_ma20 = row.get("price_vs_ma20_pct", np.nan)
    ret_20d = row.get("return_20d", np.nan)
    is_overheat = (pd.notna(price_vs_ma20) and price_vs_ma20 > overheat_limit) or (
        pd.notna(ret_20d) and ret_20d > (0.12 if category != "SATELLITE" else 0.20)
    )
    if grade == "A":
        return "等待回踩" if is_overheat else "可建首批"
    if grade == "B":
        return "小仓/等待"
    if grade == "C":
        return "观察"
    return "淘汰"


def _reason(row: pd.Series, grade: str, hard_reason: str) -> str:
    parts: list[str] = []
    if hard_reason:
        parts.append(hard_reason)
    parts.append(str(row.get("ma_state", "")))
    if pd.notna(row.get("max_drawdown_3y")):
        parts.append(f"回撤{row['max_drawdown_3y'] * 100:.1f}%")
    if pd.notna(row.get("return_1y")):
        parts.append(f"1年{row['return_1y'] * 100:+.1f}%")
    if row.get("category") == "SATELLITE" and grade in ("A", "B"):
        parts.append("增强仓管理")
    return " / ".join(part for part in parts if part)


def load_market_mode() -> tuple[str, float, str]:
    try:
        from market_mode import get_mode_params

        mode = get_mode_params()
        return mode.get("mode", "UNKNOWN"), float(mode.get("position_modifier", 1.0)), mode.get("cycle_phase", "未知")
    except Exception:
        return "UNKNOWN", 1.0, "未知"


def run_screen(args: argparse.Namespace) -> pd.DataFrame:
    pool = build_etf_pool()
    if pool.empty:
        return pool
    pool = pool[pool["category"] != "WATCH"].copy()
    if args.category:
        pool = pool[pool["category"] == args.category]
    if args.theme:
        pool = pool[pool["name"].str.contains(args.theme, na=False) | pool["theme"].str.contains(args.theme, na=False)]
    if args.min_amount > 0:
        pool = pool[(pool["amount_today"].fillna(0) >= args.min_amount) | pool["amount_today"].isna()]
    pool = pool.sort_values("amount_today", ascending=False, na_position="last")
    if args.limit and not args.all:
        pool = pool.head(args.limit)

    rows: list[dict] = []
    total = len(pool)
    for index, (_, etf) in enumerate(pool.iterrows(), 1):
        code = str(etf["code"]).zfill(6)
        if args.verbose or index == 1 or index % 20 == 0:
            print(f"  [{index}/{total}] {code} {etf['name']}")
        try:
            hist = fetch_hist(code, args.days)
            metrics = compute_metrics(hist)
        except Exception as exc:
            metrics = dict(METRIC_DEFAULTS)
            metrics["fetch_error"] = f"{type(exc).__name__}: {exc}"
        row = etf.to_dict()
        row.update(metrics)
        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    # 合并 etf_meta.csv 白名单（规模、费率、跟踪误差、估值分位、股息率、风格桶）。
    meta = load_meta()
    if not meta.empty:
        meta_cols = [c for c in meta.columns if c not in ("name",)]
        result = result.merge(meta[meta_cols], on="code", how="left")

    result = add_rank_columns(result)
    mode, _, _ = load_market_mode()
    score_rows = result.apply(lambda row: score_row(row, strict_scale=args.strict_scale, mode=mode), axis=1)
    scores = pd.DataFrame(list(score_rows), index=result.index)
    result = pd.concat([result, scores], axis=1)
    result = result.sort_values(["grade", "total_score"], ascending=[True, False])
    result["rank"] = result.groupby("category")["total_score"].rank(method="first", ascending=False).astype(int)
    return result.sort_values(["category", "rank"])


def _fmt_pct(value: float, digits: int = 1) -> str:
    if pd.isna(value):
        return ""
    return f"{value * 100:+.{digits}f}%"


def _fmt_num(value: float, digits: int = 1) -> str:
    if pd.isna(value):
        return ""
    return f"{value:.{digits}f}"


def print_report(df: pd.DataFrame, top_per_category: int, with_market_mode: bool) -> None:
    print("=" * 96)
    print("  ETF稳健收益评分器 V1")
    print("=" * 96)
    if with_market_mode:
        mode, pos_mod, cycle = load_market_mode()
        print(f"  市场模式: {mode}  情绪周期: {cycle}  仓位修正: x{pos_mod:g}")
    print()
    if df.empty:
        print("  无可输出结果。")
        return

    for category in ("CORE_DIVIDEND", "CORE_BROAD", "DEFENSIVE", "SATELLITE"):
        part = df[df["category"] == category].sort_values("rank").head(top_per_category)
        if part.empty:
            continue
        print(f"  -- {category} --")
        show = part[[
            "rank", "code", "name", "theme", "total_score", "grade", "action",
            "safety_score", "return_score", "income_score", "execution_score",
            "ma_state", "max_drawdown_3y", "return_1y", "avg_amount_20d", "reason",
        ]].copy()
        show["max_drawdown_3y"] = show["max_drawdown_3y"].map(_fmt_pct)
        show["return_1y"] = show["return_1y"].map(_fmt_pct)
        show["avg_amount_20d"] = show["avg_amount_20d"].map(lambda value: _fmt_num(value / 100000000, 2) if pd.notna(value) else "")
        for col in ("total_score", "safety_score", "return_score", "income_score", "execution_score"):
            show[col] = show[col].map(lambda value: _fmt_num(value, 0))
        print(show.to_string(index=False))
        print()


def _persist_scores(result: pd.DataFrame, mode: str) -> int:
    """把评分结果写入 MySQL etf_score_history 表。返回写入条数；DB 不可用时返回 0。

    所有行用同一个 run_date（今日）作为快照日期，便于查询"某日全市场快照"。
    各只 ETF 自己的 K 线末日通过 reason/ma_state 等字段间接体现。
    """
    if not _DB_AVAILABLE or result is None or result.empty:
        return 0
    run_date = datetime.today().strftime("%Y-%m-%d")
    rows: list[dict] = []
    for _, row in result.iterrows():
        rows.append({
            "code": str(row.get("code", "")).zfill(6),
            "date": run_date,
            "mode": mode,
            "category": row.get("category"),
            "theme": row.get("theme"),
            "name": row.get("name"),
            "total_score": row.get("total_score"),
            "safety_score": row.get("safety_score"),
            "return_score": row.get("return_score"),
            "income_score": row.get("income_score"),
            "execution_score": row.get("execution_score"),
            "grade": row.get("grade"),
            "raw_grade": row.get("raw_grade"),
            "action": row.get("action"),
            "hard_filter_ok": row.get("hard_filter_ok"),
            "hard_filter_reason": row.get("hard_filter_reason"),
            "reason": row.get("reason"),
            "ma_state": row.get("ma_state"),
            "return_1y": row.get("return_1y"),
            "max_drawdown_3y": row.get("max_drawdown_3y"),
            "avg_amount_20d": row.get("avg_amount_20d"),
            "valuation_pct_5y": row.get("valuation_pct_5y"),
        })
    try:
        return _db_cache.upsert_etf_score(rows)
    except Exception as exc:
        print(f"  ! 评分写入 etf_score_history 失败: {type(exc).__name__}: {exc}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="ETF稳健收益评分器：按长期ETF规则输出A/B/C/D候选")
    parser.add_argument("--category", choices=["CORE_DIVIDEND", "CORE_BROAD", "DEFENSIVE", "SATELLITE"], help="只扫描某一类")
    parser.add_argument("--theme", help="按名称或主题关键字过滤")
    parser.add_argument("--limit", type=int, default=160, help="按成交额取前N只扫描；默认160")
    parser.add_argument("--all", action="store_true", help="扫描过滤后的全部ETF，可能较慢")
    parser.add_argument("--days", type=int, default=780, help="历史K线交易日数量，默认780")
    parser.add_argument("--min-amount", type=float, default=0, help="扫描前最低当日成交额过滤，单位元")
    parser.add_argument("--top-per-category", type=int, default=8, help="每类显示前N只，默认8")
    parser.add_argument("--strict-scale", action="store_true", help="严格执行规模缺失进入观察/淘汰；默认对缺规模宽容")
    parser.add_argument("--with-market-mode", action="store_true", help="尝试读取 market_mode.py 并在报告中显示市场模式")
    parser.add_argument("--csv", help="可选：保存完整评分结果CSV")
    parser.add_argument("--verbose", action="store_true", help="显示每只ETF的扫描进度")
    parser.add_argument("--no-db", action="store_true", help="不写入 MySQL etf_score_history（默认写入）")
    args = parser.parse_args()

    result = run_screen(args)
    print_report(result, args.top_per_category, args.with_market_mode)
    if args.csv and not result.empty:
        out_path = Path(args.csv).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"  已保存: {out_path}")
    if not args.no_db and not result.empty:
        mode, _, _ = load_market_mode()
        n = _persist_scores(result, mode)
        if n:
            print(f"  已写入 etf_score_history: {n} 条 (mode={mode})")


if __name__ == "__main__":
    main()