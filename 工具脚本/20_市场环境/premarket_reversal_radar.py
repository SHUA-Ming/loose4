#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科技超跌反弹早识别雷达。

目标不是预测底部，而是把一类可验证的事件拆成四级信号：
  R0 弹簧：高景气科技篮子已经充分去杠杆；
  R1 点火：隔夜科技资产转强，且利率/油价/波动率至少一项配合；
  R2 竞价：核心三股至少两只同步高开；
  R3 扩散：开盘后科技篮子有足够上涨宽度，且核心股承接正常。

雷达与 market_mode 的趋势通道并行，不改变 M5、MA10、MA20 等原有闸门。
R3 只代表“反弹交易进入评估”，不代表趋势反转或自动买入。

用法：
  python 工具脚本/20_市场环境/premarket_reversal_radar.py
  python 工具脚本/20_市场环境/premarket_reversal_radar.py --backtest-days 10 --as-of 2026-08-03 --sensitivity
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths

ensure_tool_paths()
from db_cache import get_connection
from tmp_rt import fetch_realtime


INDEX_CODES = ("sh.000001", "sz.399001", "sz.399006")
CORE_CODES = ("sz.300308", "sz.300502", "sz.300394")

# 只放已有反转观察表中基本面相对清晰、能够代表光模块/PCB/半导体的锚。
# 小票题材股不进入宽度统计，避免一轮微盘脉冲把科技反弹误报为行业共振。
WATCH_CODES = (
    # 光模块 / CPO
    "sz.300308", "sz.300502", "sz.300394", "sz.002281", "sz.300548",
    "sz.300620", "sh.688313", "sh.688498", "sh.603083", "sz.000988",
    # AI PCB
    "sz.002463", "sh.688183", "sz.300476", "sz.002916", "sh.600183",
    "sz.001389",
    # 半导体核心与材料确认器
    "sz.002371", "sh.688012", "sh.688981", "sh.688041", "sh.688256",
    "sz.300666", "sz.002409",
)

YAHOO_DAILY_SYMBOLS = {
    "nasdaq": "^IXIC",
    "sox": "^SOX",
    "tnx": "^TNX",
    "brent": "BZ=F",
    "vix": "^VIX",
}


@dataclass(frozen=True)
class RadarParams:
    # R0：阈值采用“中位数 + 宽度”，避免被一两只暴跌股绑架。
    setup_ret20_pct: float = -15.0
    setup_ret10_pct: float = -10.0
    setup_below_ma20_ratio: float = 0.65
    setup_dist_ma20_pct: float = -7.0
    setup_min_score: int = 5

    # R1：科技方向必须自己转强；宏观变量只能加分，不能单独触发。
    overnight_nasdaq_pct: float = 1.0
    overnight_sox_pct: float = 1.5
    overnight_tnx_bp: float = -4.0
    overnight_vix_pct: float = -5.0
    overnight_brent_pct: float = -2.5
    overnight_brent_prior5_pct: float = 3.0
    overnight_min_score: float = 2.5

    # R2：两只核心同步是硬条件；1.5%兼顾辨识度与不过度追高。
    auction_core_gap_pct: float = 1.5
    auction_core_required: int = 2
    auction_index_gap_pct: float = 0.2
    auction_style_gap_pct: float = 0.5
    auction_hot_median_gap_pct: float = 6.0
    auction_min_score: int = 3

    # R3：09:40确认。宽度是第一硬证据，核心承接是第二硬证据。
    confirm_time: str = "09:40"
    confirm_breadth_ratio: float = 0.70
    confirm_breadth_floor: float = 0.60
    confirm_median_pct: float = 1.0
    confirm_core_above_open: int = 2
    confirm_style_spread_pct: float = 0.8
    confirm_volume_speed: float = 1.10
    confirm_required_components: int = 3
    min_coverage_ratio: float = 0.70

    # 只用于回放标签，不参与信号生成。
    target_theme_close_pct: float = 2.0
    target_close_breadth: float = 0.70
    target_cyb_close_pct: float = 1.5


DEFAULT_PARAMS = RadarParams()


def bs_to_yahoo(code: str) -> str:
    raw = str(code).strip().lower()
    digits = raw.split(".")[-1]
    if raw.startswith("sh."):
        return f"{digits}.SS"
    if raw.startswith("sz."):
        return f"{digits}.SZ"
    raise ValueError(f"不支持的A股代码: {code}")


def _date_floor(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _period_seconds(day: dt.date) -> int:
    return int(dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc).timestamp())


def fetch_yahoo_chart(
    symbol: str,
    start: dt.date,
    end: dt.date,
    interval: str,
    session: Optional[requests.Session] = None,
    retries: int = 3,
) -> pd.DataFrame:
    """读取Yahoo chart数据；end为包含式日期。失败抛异常，不静默造数据。"""
    client = session or requests.Session()
    encoded = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
    params = {
        "period1": _period_seconds(start),
        "period2": _period_seconds(end + dt.timedelta(days=2)),
        "interval": interval,
        "events": "history",
        "includePrePost": "false",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    error = None
    for attempt in range(retries):
        try:
            response = client.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart") or {}
            if chart.get("error"):
                raise RuntimeError(str(chart["error"]))
            result = (chart.get("result") or [None])[0]
            if not result:
                raise RuntimeError("Yahoo返回空result")
            stamps = result.get("timestamp") or []
            quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            if not stamps:
                return pd.DataFrame(columns=["datetime", "date", "open", "high", "low", "close", "volume"])
            tz_name = (result.get("meta") or {}).get("exchangeTimezoneName") or "UTC"
            try:
                exchange_tz = ZoneInfo(tz_name)
            except Exception:
                exchange_tz = dt.timezone.utc
            rows = []
            for i, stamp in enumerate(stamps):
                moment = dt.datetime.fromtimestamp(int(stamp), tz=dt.timezone.utc).astimezone(exchange_tz)
                row = {"datetime": moment, "date": moment.date()}
                for col in ("open", "high", "low", "close", "volume"):
                    values = quote_rows.get(col) or []
                    row[col] = values[i] if i < len(values) else None
                rows.append(row)
            frame = pd.DataFrame(rows)
            for col in ("open", "high", "low", "close", "volume"):
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
            frame = frame.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
            return frame
        except Exception as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Yahoo行情失败 {symbol}: {error}")


def fetch_yahoo_many(
    symbols: Mapping[str, str],
    start: dt.date,
    end: dt.date,
    interval: str,
    workers: int = 6,
) -> Dict[str, pd.DataFrame]:
    """有限并发读取，减少两周回放的网络等待。"""
    result: Dict[str, pd.DataFrame] = {}

    def one(item):
        key, symbol = item
        return key, fetch_yahoo_chart(symbol, start, end, interval)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, item) for item in symbols.items()]
        for future in concurrent.futures.as_completed(futures):
            key, frame = future.result()
            result[key] = frame
    return result


def load_local_daily(codes: Sequence[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    """纯查询；严格使用readonly连接，禁止在分析流程建表。"""
    if not codes:
        return pd.DataFrame()
    conn = get_connection(readonly=True)
    placeholders = ",".join(["?"] * len(codes))
    cursor = conn.execute(
        f"SELECT code,date,open,high,low,close,volume,amount,pctChg "
        f"FROM kline_daily WHERE code IN ({placeholders}) AND date>=? AND date<=? "
        f"ORDER BY date,code",
        list(codes) + [start.isoformat(), end.isoformat()],
    )
    rows = cursor.fetchall()
    conn.close()
    frame = pd.DataFrame(
        rows,
        columns=["code", "date", "open", "high", "low", "close", "volume", "amount", "pctChg"],
    )
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    for col in ("open", "high", "low", "close", "volume", "amount", "pctChg"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def trading_dates(daily: pd.DataFrame, end: Optional[dt.date] = None) -> List[dt.date]:
    subset = daily[daily["code"].eq("sz.399006")]
    dates = sorted(set(subset["date"]))
    if end:
        dates = [day for day in dates if day <= end]
    return dates


def _safe_median(values: Iterable[float]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None and np.isfinite(float(v))]
    return float(np.median(clean)) if clean else None


def compute_setup_features(daily: pd.DataFrame, signal_date: dt.date) -> Dict[str, object]:
    """只使用signal_date之前的数据，防止回放偷看当天。"""
    metrics = []
    for code in WATCH_CODES:
        grp = daily[(daily["code"] == code) & (daily["date"] < signal_date)].sort_values("date")
        if len(grp) < 21:
            continue
        closes = grp["close"].astype(float).values
        last = float(closes[-1])
        ma20 = float(np.mean(closes[-20:]))
        ret10 = (last / float(closes[-11]) - 1) * 100
        ret20 = (last / float(closes[-21]) - 1) * 100
        metrics.append({
            "code": code,
            "ret10": ret10,
            "ret20": ret20,
            "dist_ma20": (last / ma20 - 1) * 100 if ma20 else 0.0,
            "below_ma20": last < ma20,
            "last_pct": float(grp.iloc[-1]["pctChg"]),
            "as_of": grp.iloc[-1]["date"],
        })
    coverage = len(metrics) / len(WATCH_CODES)
    if not metrics:
        return {"coverage": coverage, "available": 0}
    return {
        "coverage": coverage,
        "available": len(metrics),
        "as_of": max(item["as_of"] for item in metrics),
        "median_ret10": _safe_median(item["ret10"] for item in metrics),
        "median_ret20": _safe_median(item["ret20"] for item in metrics),
        "median_dist_ma20": _safe_median(item["dist_ma20"] for item in metrics),
        "below_ma20_ratio": float(np.mean([item["below_ma20"] for item in metrics])),
        "prior_day_median_pct": _safe_median(item["last_pct"] for item in metrics),
    }


def evaluate_setup(features: Mapping[str, object], params: RadarParams) -> Tuple[bool, int, List[str]]:
    if float(features.get("coverage") or 0) < params.min_coverage_ratio:
        return False, 0, ["历史覆盖不足"]
    ret20 = float(features.get("median_ret20") or 0)
    ret10 = float(features.get("median_ret10") or 0)
    below = float(features.get("below_ma20_ratio") or 0)
    dist = float(features.get("median_dist_ma20") or 0)
    prior = float(features.get("prior_day_median_pct") or 0)
    score = 0
    reasons = []
    if ret20 <= params.setup_ret20_pct:
        score += 2
        reasons.append(f"20日中位{ret20:+.1f}%")
        if ret20 <= params.setup_ret20_pct - 7:
            score += 1
    if ret10 <= params.setup_ret10_pct:
        score += 2
        reasons.append(f"10日中位{ret10:+.1f}%")
        if ret10 <= params.setup_ret10_pct - 5:
            score += 1
    if below >= params.setup_below_ma20_ratio:
        score += 2
        reasons.append(f"MA20下方{below:.0%}")
        if below >= 0.80:
            score += 1
    if dist <= params.setup_dist_ma20_pct:
        score += 1
        reasons.append(f"距MA20中位{dist:+.1f}%")
        if dist <= params.setup_dist_ma20_pct - 5:
            score += 1
    if prior <= -2.0:
        score += 1
        reasons.append(f"前日中位{prior:+.1f}%")
    drawdown_gate = ret20 <= params.setup_ret20_pct or ret10 <= params.setup_ret10_pct
    breadth_gate = below >= params.setup_below_ma20_ratio
    passed = drawdown_gate and breadth_gate and score >= params.setup_min_score
    return passed, score, reasons


def _daily_return_map(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    out["pct"] = out["close"].pct_change() * 100
    out["change"] = out["close"].diff()
    out["ret5"] = out["close"].pct_change(5) * 100
    return out


def compute_overnight_features(
    external_daily: Mapping[str, pd.DataFrame], signal_date: dt.date
) -> Dict[str, object]:
    rows = {}
    for key, frame in external_daily.items():
        mapped = _daily_return_map(frame)
        eligible = mapped[mapped["date"] < signal_date]
        if eligible.empty:
            continue
        rows[key] = eligible.iloc[-1]
    if "nasdaq" not in rows or "sox" not in rows:
        return {"available": False}
    us_date = rows["nasdaq"]["date"]
    return {
        "available": True,
        "us_date": us_date,
        "nasdaq_pct": float(rows["nasdaq"]["pct"]),
        "sox_pct": float(rows["sox"]["pct"]),
        # 当前Yahoo ^TNX直接以百分比收益率报价（4.686即4.686%），
        # 0.01个百分点等于1bp，因此日差需要乘100。
        "tnx_bp": float(rows["tnx"]["change"] * 100) if "tnx" in rows else None,
        "vix_pct": float(rows["vix"]["pct"]) if "vix" in rows else None,
        "brent_pct": float(rows["brent"]["pct"]) if "brent" in rows else None,
        "brent_prior5_pct": float(rows["brent"]["ret5"]) if "brent" in rows else None,
    }


def evaluate_overnight(features: Mapping[str, object], params: RadarParams) -> Tuple[bool, float, List[str]]:
    if not features.get("available"):
        return False, 0.0, ["隔夜数据缺失"]
    nasdaq = float(features.get("nasdaq_pct") or 0)
    sox = float(features.get("sox_pct") or 0)
    tnx = features.get("tnx_bp")
    vix = features.get("vix_pct")
    brent = features.get("brent_pct")
    brent5 = features.get("brent_prior5_pct")
    score = 0.0
    reasons = []
    tech_trigger = False
    if nasdaq >= params.overnight_nasdaq_pct:
        score += 1.0
        tech_trigger = True
        reasons.append(f"纳指{nasdaq:+.1f}%")
        if nasdaq >= 2.0:
            score += 0.5
    if sox >= params.overnight_sox_pct:
        score += 1.0
        tech_trigger = True
        reasons.append(f"费半{sox:+.1f}%")
        if sox >= 3.0:
            score += 0.5
    if tnx is not None and float(tnx) <= params.overnight_tnx_bp:
        score += 1.0
        reasons.append(f"美债{float(tnx):+.1f}bp")
        if float(tnx) <= -7.0:
            score += 0.5
    if vix is not None and float(vix) <= params.overnight_vix_pct:
        score += 0.5
        reasons.append(f"VIX{float(vix):+.1f}%")
        if float(vix) <= -10.0:
            score += 0.5
    # 油价只有在此前已上涨、当前回落时才是“通胀冲击逆转”；需求衰退型下跌不加分。
    if (
        brent is not None and brent5 is not None
        and float(brent) <= params.overnight_brent_pct
        and float(brent5) >= params.overnight_brent_prior5_pct
    ):
        score += 1.0
        reasons.append(f"油价冲击逆转{float(brent):+.1f}%/前5日{float(brent5):+.1f}%")
        if float(brent) <= -4.0:
            score += 0.5
    if nasdaq <= -1.0:
        score -= 1.0
    if sox <= -2.0:
        score -= 1.0
    if tnx is not None and float(tnx) >= 8.0:
        score -= 1.0
    passed = tech_trigger and score >= params.overnight_min_score
    return passed, score, reasons


def _row_on(daily: pd.DataFrame, code: str, day: dt.date) -> Optional[pd.Series]:
    hit = daily[(daily["code"] == code) & (daily["date"] == day)]
    return None if hit.empty else hit.iloc[-1]


def _previous_close(daily: pd.DataFrame, code: str, day: dt.date) -> Optional[float]:
    hit = daily[(daily["code"] == code) & (daily["date"] < day)].sort_values("date")
    return None if hit.empty else float(hit.iloc[-1]["close"])


def compute_auction_features(daily: pd.DataFrame, signal_date: dt.date) -> Dict[str, object]:
    core_gaps = []
    for code in CORE_CODES:
        row = _row_on(daily, code, signal_date)
        prev = _previous_close(daily, code, signal_date)
        if row is not None and prev and float(row["open"]) > 0:
            core_gaps.append((float(row["open"]) / prev - 1) * 100)
    index_gaps = {}
    for code in INDEX_CODES:
        row = _row_on(daily, code, signal_date)
        prev = _previous_close(daily, code, signal_date)
        if row is not None and prev:
            index_gaps[code] = (float(row["open"]) / prev - 1) * 100
    cyb_gap = index_gaps.get("sz.399006")
    sh_gap = index_gaps.get("sh.000001")
    return {
        "core_gaps": core_gaps,
        "core_coverage": len(core_gaps) / len(CORE_CODES),
        "core_median_gap": _safe_median(core_gaps),
        "cyb_gap": cyb_gap,
        "sh_gap": sh_gap,
        "style_gap": (cyb_gap - sh_gap) if cyb_gap is not None and sh_gap is not None else None,
    }


def evaluate_auction(features: Mapping[str, object], params: RadarParams) -> Tuple[bool, int, List[str]]:
    gaps = [float(v) for v in features.get("core_gaps", [])]
    if len(gaps) < params.auction_core_required:
        return False, 0, ["核心竞价覆盖不足"]
    count = sum(v >= params.auction_core_gap_pct for v in gaps)
    median_gap = float(features.get("core_median_gap") or 0)
    cyb_gap = features.get("cyb_gap")
    style_gap = features.get("style_gap")
    score = 0
    reasons = []
    if count >= params.auction_core_required:
        score += 2
        reasons.append(f"核心{count}/{len(gaps)}只高开≥{params.auction_core_gap_pct:g}%")
    if median_gap >= params.auction_core_gap_pct:
        score += 1
        reasons.append(f"核心高开中位{median_gap:+.1f}%")
    if cyb_gap is not None and float(cyb_gap) >= params.auction_index_gap_pct:
        score += 1
        reasons.append(f"创业板高开{float(cyb_gap):+.1f}%")
    if style_gap is not None and float(style_gap) >= params.auction_style_gap_pct:
        score += 1
        reasons.append(f"成长/上证竞价差{float(style_gap):+.1f}pct")
    if median_gap >= params.auction_hot_median_gap_pct:
        score -= 1
        reasons.append("核心竞价过热，降分不追")
    passed = count >= params.auction_core_required and score >= params.auction_min_score
    return passed, score, reasons


def _cutoff_time(text: str) -> dt.time:
    return dt.datetime.strptime(text, "%H:%M").time()


def _intraday_until(frame: pd.DataFrame, day: dt.date, confirm_time: str) -> pd.DataFrame:
    cutoff = _cutoff_time(confirm_time)
    hit = frame[frame["date"] == day].copy()
    if hit.empty:
        return hit
    # Yahoo 5分钟K的时间戳是bar起点；09:35 bar在09:40结束，因此起点必须早于cutoff。
    return hit[hit["datetime"].map(lambda value: value.timetz().replace(tzinfo=None) < cutoff)]


def compute_intraday_features(
    daily: pd.DataFrame,
    intraday: Mapping[str, pd.DataFrame],
    signal_date: dt.date,
    confirm_time: str,
) -> Dict[str, object]:
    observations = []
    volume_speeds = []
    for code in WATCH_CODES:
        frame = intraday.get(code)
        prev = _previous_close(daily, code, signal_date)
        if frame is None or frame.empty or not prev:
            continue
        bars = _intraday_until(frame, signal_date, confirm_time)
        if bars.empty:
            continue
        current = float(bars.iloc[-1]["close"])
        open_value = float(bars.iloc[0]["open"])
        observations.append({
            "code": code,
            "pct": (current / prev - 1) * 100,
            "above_open": current >= open_value,
            "current": current,
            "open": open_value,
        })
        prior_volumes = []
        for prior_day in sorted(set(frame[frame["date"] < signal_date]["date"]))[-5:]:
            prior_bars = _intraday_until(frame, prior_day, confirm_time)
            if not prior_bars.empty:
                prior_volumes.append(float(prior_bars["volume"].fillna(0).sum()))
        current_volume = float(bars["volume"].fillna(0).sum())
        baseline = float(np.median(prior_volumes)) if prior_volumes else 0.0
        if current_volume > 0 and baseline > 0:
            volume_speeds.append(current_volume / baseline)

    core_map = {item["code"]: item for item in observations if item["code"] in CORE_CODES}
    breadth = float(np.mean([item["pct"] > 0 for item in observations])) if observations else None
    above_open_ratio = float(np.mean([item["above_open"] for item in observations])) if observations else None
    median_pct = _safe_median(item["pct"] for item in observations)
    core_above_open = sum(bool(item["above_open"]) for item in core_map.values())

    index_pct = {}
    for code in ("sh.000001", "sz.399006"):
        frame = intraday.get(code)
        prev = _previous_close(daily, code, signal_date)
        if frame is None or frame.empty or not prev:
            continue
        bars = _intraday_until(frame, signal_date, confirm_time)
        if not bars.empty:
            index_pct[code] = (float(bars.iloc[-1]["close"]) / prev - 1) * 100
    style_spread = None
    if "sz.399006" in index_pct and "sh.000001" in index_pct:
        style_spread = index_pct["sz.399006"] - index_pct["sh.000001"]
    return {
        "observations": observations,
        "coverage": len(observations) / len(WATCH_CODES),
        "breadth": breadth,
        "above_open_ratio": above_open_ratio,
        "median_pct": median_pct,
        "core_above_open": core_above_open,
        "core_coverage": len(core_map) / len(CORE_CODES),
        "style_spread": style_spread,
        "cyb_pct": index_pct.get("sz.399006"),
        "sh_pct": index_pct.get("sh.000001"),
        "volume_speed": _safe_median(volume_speeds),
    }


def evaluate_intraday(features: Mapping[str, object], params: RadarParams) -> Tuple[bool, int, List[str]]:
    coverage = float(features.get("coverage") or 0)
    if coverage < params.min_coverage_ratio:
        return False, 0, [f"盘中覆盖仅{coverage:.0%}"]
    breadth = float(features.get("breadth") or 0)
    median_pct = float(features.get("median_pct") or 0)
    core_above = int(features.get("core_above_open") or 0)
    style = features.get("style_spread")
    volume_speed = features.get("volume_speed")
    components = []
    reasons = []
    components.append(breadth >= params.confirm_breadth_ratio)
    if components[-1]:
        reasons.append(f"上涨宽度{breadth:.0%}")
    components.append(median_pct >= params.confirm_median_pct)
    if components[-1]:
        reasons.append(f"篮子中位{median_pct:+.1f}%")
    components.append(core_above >= params.confirm_core_above_open)
    if components[-1]:
        reasons.append(f"核心{core_above}只站开盘价")
    components.append(style is not None and float(style) >= params.confirm_style_spread_pct)
    if components[-1]:
        reasons.append(f"成长/上证强弱差{float(style):+.1f}pct")
    # 成交速度缺失不判失败；有数据时才作为第五个确认组件。
    if volume_speed is not None:
        components.append(float(volume_speed) >= params.confirm_volume_speed)
        if components[-1]:
            reasons.append(f"同时间成交速度{float(volume_speed):.2f}x")
    score = sum(bool(value) for value in components)
    passed = (
        breadth >= params.confirm_breadth_floor
        and core_above >= params.confirm_core_above_open
        and score >= params.confirm_required_components
    )
    return passed, score, reasons


def compute_outcome(
    daily: pd.DataFrame,
    intraday: Mapping[str, pd.DataFrame],
    signal_date: dt.date,
    confirm_time: str,
    params: RadarParams,
) -> Dict[str, object]:
    day_pcts = []
    close_from_confirm = []
    d1_from_confirm = []
    mfe = []
    mae = []
    dates = trading_dates(daily)
    next_dates = [day for day in dates if day > signal_date]
    next_date = next_dates[0] if next_dates else None
    for code in WATCH_CODES:
        row = _row_on(daily, code, signal_date)
        prev = _previous_close(daily, code, signal_date)
        frame = intraday.get(code)
        if row is None or not prev or frame is None or frame.empty:
            continue
        bars = _intraday_until(frame, signal_date, confirm_time)
        day_bars = frame[frame["date"] == signal_date].sort_values("datetime")
        if bars.empty or day_bars.empty:
            continue
        confirm = float(bars.iloc[-1]["close"])
        day_close = float(row["close"])
        day_pcts.append((day_close / prev - 1) * 100)
        close_from_confirm.append((day_close / confirm - 1) * 100)
        later = day_bars[day_bars["datetime"] >= bars.iloc[-1]["datetime"]]
        if not later.empty:
            mfe.append((float(later["high"].max()) / confirm - 1) * 100)
            mae.append((float(later["low"].min()) / confirm - 1) * 100)
        if next_date:
            next_row = _row_on(daily, code, next_date)
            if next_row is not None:
                d1_from_confirm.append((float(next_row["close"]) / confirm - 1) * 100)
    close_breadth = float(np.mean([value > 0 for value in day_pcts])) if day_pcts else 0.0
    theme_close = _safe_median(day_pcts)
    cyb = _row_on(daily, "sz.399006", signal_date)
    cyb_pct = float(cyb["pctChg"]) if cyb is not None else None
    target = bool(
        theme_close is not None
        and theme_close >= params.target_theme_close_pct
        and close_breadth >= params.target_close_breadth
        and cyb_pct is not None
        and cyb_pct >= params.target_cyb_close_pct
    )
    return {
        "target": target,
        "theme_close_pct": theme_close,
        "close_breadth": close_breadth,
        "cyb_close_pct": cyb_pct,
        "confirm_to_close_pct": _safe_median(close_from_confirm),
        "confirm_to_d1_pct": _safe_median(d1_from_confirm),
        "mfe_pct": _safe_median(mfe),
        "mae_pct": _safe_median(mae),
    }


def evaluate_feature_row(row: Mapping[str, object], params: RadarParams) -> Dict[str, object]:
    r0, s0, reasons0 = evaluate_setup(row["setup"], params)
    r1, s1, reasons1 = evaluate_overnight(row["overnight"], params)
    r2, s2, reasons2 = evaluate_auction(row["auction"], params)
    r3, s3, reasons3 = evaluate_intraday(row["intraday"], params)
    signal = r0 and r1 and r2 and r3
    median_gap = row["auction"].get("core_median_gap")
    auction_overheated = (
        median_gap is not None
        and float(median_gap) >= params.auction_hot_median_gap_pct
    )
    # “识别到反弹”与“此时还能否参与”严格分离。高开过热仍记为事件命中，
    # 但不可执行，避免7月31日式早盘高潮后回落。
    actionable = signal and not auction_overheated
    return {
        "r0": r0, "r1": r1, "r2": r2, "r3": r3,
        "signal": signal, "actionable": actionable,
        "auction_overheated": auction_overheated,
        "score0": s0, "score1": s1, "score2": s2, "score3": s3,
        "reasons0": reasons0, "reasons1": reasons1,
        "reasons2": reasons2, "reasons3": reasons3,
    }


def _prepare_backtest_data(
    days: int,
    as_of: Optional[dt.date],
    confirm_time: str,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame], Dict[str, pd.DataFrame], List[dt.date]]:
    end = as_of or dt.date.today()
    history_start = end - dt.timedelta(days=100)
    all_codes = tuple(dict.fromkeys((*WATCH_CODES, *INDEX_CODES)))
    daily = load_local_daily(all_codes, history_start, end + dt.timedelta(days=2))
    dates = trading_dates(daily, end=end)
    if len(dates) < days:
        raise RuntimeError(f"A股历史交易日不足：需要{days}，实际{len(dates)}")
    test_dates = dates[-days:]
    network_start = test_dates[0] - dt.timedelta(days=12)
    external = fetch_yahoo_many(YAHOO_DAILY_SYMBOLS, network_start, end, "1d", workers=5)
    intraday_symbols = {code: bs_to_yahoo(code) for code in all_codes}
    intraday = fetch_yahoo_many(intraday_symbols, network_start, end, "5m", workers=6)
    return daily, external, intraday, test_dates


def build_feature_rows(
    daily: pd.DataFrame,
    external: Mapping[str, pd.DataFrame],
    intraday: Mapping[str, pd.DataFrame],
    dates: Sequence[dt.date],
    params: RadarParams,
) -> List[Dict[str, object]]:
    rows = []
    for day in dates:
        row = {
            "date": day,
            "setup": compute_setup_features(daily, day),
            "overnight": compute_overnight_features(external, day),
            "auction": compute_auction_features(daily, day),
            "intraday": compute_intraday_features(daily, intraday, day, params.confirm_time),
        }
        row["outcome"] = compute_outcome(daily, intraday, day, params.confirm_time, params)
        rows.append(row)
    return rows


def summarize_rows(rows: Sequence[Mapping[str, object]], params: RadarParams) -> Dict[str, object]:
    evaluated = []
    for row in rows:
        verdict = evaluate_feature_row(row, params)
        evaluated.append((row, verdict))
    signals = [item for item in evaluated if item[1]["signal"]]
    actionable = [item for item in evaluated if item[1]["actionable"]]
    targets = [item for item in evaluated if item[0]["outcome"]["target"]]
    true_positive = [item for item in signals if item[0]["outcome"]["target"]]
    precision = len(true_positive) / len(signals) if signals else None
    recall = len(true_positive) / len(targets) if targets else None
    return {
        "evaluated": evaluated,
        "signal_count": len(signals),
        "actionable_count": len(actionable),
        "target_count": len(targets),
        "true_positive": len(true_positive),
        "false_positive": len(signals) - len(true_positive),
        "missed": len(targets) - len(true_positive),
        "precision": precision,
        "recall": recall,
        "signal_dates": [item[0]["date"] for item in signals],
        "target_dates": [item[0]["date"] for item in targets],
        "actionable_dates": [item[0]["date"] for item in actionable],
        "median_to_close": _safe_median(item[0]["outcome"]["confirm_to_close_pct"] for item in actionable),
        "median_to_d1": _safe_median(item[0]["outcome"]["confirm_to_d1_pct"] for item in actionable),
        "median_mae": _safe_median(item[0]["outcome"]["mae_pct"] for item in actionable),
        "median_mfe": _safe_median(item[0]["outcome"]["mfe_pct"] for item in actionable),
    }


def _fmt(value, suffix="", digits=1) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "缺失"
    return f"{float(value):.{digits}f}{suffix}"


def print_backtest(rows: Sequence[Mapping[str, object]], params: RadarParams) -> Dict[str, object]:
    summary = summarize_rows(rows, params)
    print("=" * 132)
    print(f"  科技超跌反弹雷达 · 两周回放  确认时点 {params.confirm_time}")
    print("=" * 132)
    print(
        f"  {'日期':<12s} {'R0':>3s} {'R1':>3s} {'R2':>3s} {'R3':>3s} {'信号':>5s} {'执行':>5s} "
        f"{'20日中位':>9s} {'隔夜分':>7s} {'核心高开中位':>11s} {(params.confirm_time + '宽度'):>10s} "
        f"{(params.confirm_time + '中位'):>10s} {'成长差':>8s} {'收盘中位':>9s} {'目标':>5s} {'确认→收盘':>10s}"
    )
    print("-" * 132)
    for row, verdict in summary["evaluated"]:
        setup = row["setup"]
        auction = row["auction"]
        intra = row["intraday"]
        outcome = row["outcome"]
        mark = lambda value: "✓" if value else "·"
        print(
            f"  {row['date'].isoformat():<12s} {mark(verdict['r0']):>3s} {mark(verdict['r1']):>3s} "
            f"{mark(verdict['r2']):>3s} {mark(verdict['r3']):>3s} {('触发' if verdict['signal'] else '-'):>5s} "
            f"{('可评估' if verdict['actionable'] else '禁追' if verdict['signal'] else '-'):>5s} "
            f"{_fmt(setup.get('median_ret20'), '%'):>9s} {verdict['score1']:>7.1f} "
            f"{_fmt(auction.get('core_median_gap'), '%'):>11s} {_fmt((intra.get('breadth') or 0)*100, '%', 0):>10s} "
            f"{_fmt(intra.get('median_pct'), '%'):>10s} {_fmt(intra.get('style_spread'), 'pct'):>8s} "
            f"{_fmt(outcome.get('theme_close_pct'), '%'):>9s} {('是' if outcome['target'] else '否'):>5s} "
            f"{_fmt(outcome.get('confirm_to_close_pct'), '%'):>10s}"
        )
    print("-" * 132)
    precision = "缺失" if summary["precision"] is None else f"{summary['precision']:.0%}"
    recall = "缺失" if summary["recall"] is None else f"{summary['recall']:.0%}"
    print(
        f"  识别信号{summary['signal_count']}次 / 可执行评估{summary['actionable_count']}次 / "
        f"目标反弹{summary['target_count']}次 / 命中{summary['true_positive']}次 / "
        f"误报{summary['false_positive']}次 / 漏报{summary['missed']}次  精确率{precision}  召回率{recall}"
    )
    print(
        f"  可执行信号后中位：确认→收盘 {_fmt(summary['median_to_close'], '%')}  "
        f"确认→D1收盘 {_fmt(summary['median_to_d1'], '%')}  "
        f"日内MFE {_fmt(summary['median_mfe'], '%')}  MAE {_fmt(summary['median_mae'], '%')}"
    )
    print(f"  注：历史R3使用Yahoo 5分钟K在{params.confirm_time}回放；不是用收盘宽度冒充盘中确认。")
    print("=" * 132)
    return summary


def summarize_stage(
    rows: Sequence[Mapping[str, object]], params: RadarParams, stage: str
) -> Dict[str, int]:
    """单独评价一级闸门，避免全链路AND把参数影响遮住。"""
    evaluators = {
        "R0": lambda row: evaluate_setup(row["setup"], params)[0],
        "R1": lambda row: evaluate_overnight(row["overnight"], params)[0],
        "R2": lambda row: evaluate_auction(row["auction"], params)[0],
        "R3": lambda row: evaluate_intraday(row["intraday"], params)[0],
    }
    passed = [row for row in rows if evaluators[stage](row)]
    targets = [row for row in rows if row["outcome"]["target"]]
    true_positive = [row for row in passed if row["outcome"]["target"]]
    return {
        "passed": len(passed),
        "true_positive": len(true_positive),
        "false_positive": len(passed) - len(true_positive),
        "missed": len(targets) - len(true_positive),
    }


def print_gate_funnel(rows: Sequence[Mapping[str, object]], params: RadarParams) -> None:
    summary = summarize_rows(rows, params)
    targets = summary["target_count"]
    print()
    print("=" * 88)
    print("  各级闸门独立辨识力（不是交易收益；用来解释每一级到底过滤了什么）")
    print("=" * 88)
    print(f"  {'闸门':<8s} {'通过日':>7s} {'覆盖目标':>9s} {'同时误报':>9s} {'漏掉目标':>9s} {'定位'}")
    print("-" * 88)
    descriptions = {
        "R0": "状态变量：只判断弹簧是否压紧，不负责择时",
        "R1": "外部点火：过滤没有海外科技/贴现率配合的日子",
        "R2": "核心联动：过滤隔夜利好但A股核心不认可",
        "R3": "板块扩散：过滤只拉龙头、没有宽度和承接",
    }
    for stage in ("R0", "R1", "R2", "R3"):
        stage_summary = summarize_stage(rows, params, stage)
        print(
            f"  {stage:<8s} {stage_summary['passed']:>7d} "
            f"{stage_summary['true_positive']:>5d}/{targets:<3d} "
            f"{stage_summary['false_positive']:>9d} {stage_summary['missed']:>9d} "
            f"{descriptions[stage]}"
        )
    print("-" * 88)
    print(
        f"  全链路识别 {summary['signal_count']}日；其中目标{summary['true_positive']}日、"
        f"误报{summary['false_positive']}日；过热禁追后可执行{summary['actionable_count']}日。"
    )
    print("=" * 88)


def print_sensitivity(rows: Sequence[Mapping[str, object]], base: RadarParams) -> None:
    # “局部通过”评价参数本身的筛选作用；“全链识别”显示它放回串联雷达后的结果。
    variations = [
        ("R0·20日跌幅", "R0", "setup_ret20_pct", [-12.0, -15.0, -18.0, -20.0]),
        ("R0·10日跌幅", "R0", "setup_ret10_pct", [-7.0, -10.0, -12.0, -15.0]),
        ("R0·MA20下方宽度", "R0", "setup_below_ma20_ratio", [0.55, 0.65, 0.75, 0.85]),
        ("R0·距MA20", "R0", "setup_dist_ma20_pct", [-4.0, -7.0, -10.0, -13.0]),
        ("R0·最低分", "R0", "setup_min_score", [4, 5, 6, 7]),
        ("R1·纳指涨幅", "R1", "overnight_nasdaq_pct", [0.5, 1.0, 1.5, 2.0]),
        ("R1·费半涨幅", "R1", "overnight_sox_pct", [1.0, 1.5, 2.0, 2.5]),
        ("R1·美债bp", "R1", "overnight_tnx_bp", [-2.0, -4.0, -6.0, -8.0]),
        ("R1·隔夜最低分", "R1", "overnight_min_score", [2.0, 2.5, 3.0, 3.5]),
        ("R2·核心高开", "R2", "auction_core_gap_pct", [0.5, 1.0, 1.5, 2.0]),
        ("R2·核心数量", "R2", "auction_core_required", [1, 2, 3]),
        ("R2·指数高开", "R2", "auction_index_gap_pct", [0.0, 0.2, 0.5, 0.8]),
        ("R2·竞价风格差", "R2", "auction_style_gap_pct", [0.2, 0.5, 0.8, 1.2]),
        ("R2·最低分", "R2", "auction_min_score", [2, 3, 4, 5]),
        ("R3·宽度硬底", "R3", "confirm_breadth_floor", [0.50, 0.60, 0.70, 0.80]),
        ("R3·上涨宽度", "R3", "confirm_breadth_ratio", [0.60, 0.70, 0.80, 0.90]),
        ("R3·篮子中位", "R3", "confirm_median_pct", [0.5, 1.0, 1.5, 2.0]),
        ("R3·核心承接", "R3", "confirm_core_above_open", [1, 2, 3]),
        ("R3·成长强弱差", "R3", "confirm_style_spread_pct", [0.3, 0.8, 1.2, 1.8]),
        ("R3·确认组件", "R3", "confirm_required_components", [2, 3, 4]),
        ("R3·量速", "R3", "confirm_volume_speed", [0.9, 1.1, 1.3, 1.5]),
    ]
    print_gate_funnel(rows, base)
    print()
    print("=" * 112)
    print("  单参数敏感性（局部列拆开看闸门影响；小样本只看稳定区间，不按最高收益择参）")
    print("=" * 112)
    print(
        f"  {'参数':<22s} {'级别':>5s} {'取值':>8s} {'局部通过':>9s} {'局部目标':>9s} "
        f"{'局部误报':>9s} {'局部漏报':>9s} {'全链识别':>9s} {'可执行':>7s} {'识别日期'}"
    )
    print("-" * 112)
    for label, stage, field, values in variations:
        for value in values:
            params = dataclasses.replace(base, **{field: value})
            local = summarize_stage(rows, params, stage)
            full = summarize_rows(rows, params)
            value_text = f"{value:.2f}" if isinstance(value, float) else str(value)
            print(
                f"  {label:<22s} {stage:>5s} {value_text:>8s} {local['passed']:>9d} "
                f"{local['true_positive']:>9d} {local['false_positive']:>9d} {local['missed']:>9d} "
                f"{full['signal_count']:>9d} {full['actionable_count']:>7d} "
                f"{','.join(day.strftime('%m-%d') for day in full['signal_dates']) or '-'}"
            )
        print("-" * 112)

    print("  竞价过热线只改变‘能否执行’，不改变反弹事件识别：")
    for cap in (4.0, 5.0, 6.0, 8.0, 10.0, 13.0):
        params = dataclasses.replace(base, auction_hot_median_gap_pct=cap)
        full = summarize_rows(rows, params)
        print(
            f"    上限{cap:>4.1f}%  识别{full['signal_count']}日  可执行{full['actionable_count']}日  "
            f"确认→收盘{_fmt(full['median_to_close'], '%')}  "
            f"执行日期{','.join(day.strftime('%m-%d') for day in full['actionable_dates']) or '-'}"
        )
    print("=" * 112)


def print_confirm_time_sensitivity(
    daily: pd.DataFrame,
    external: Mapping[str, pd.DataFrame],
    intraday: Mapping[str, pd.DataFrame],
    dates: Sequence[dt.date],
    base: RadarParams,
) -> None:
    print()
    print("=" * 100)
    print("  R3确认时点敏感性（每个时点都重新截取当时可见的5分钟K，不使用其后数据）")
    print("=" * 100)
    print(
        f"  {'时点':>7s} {'R3独立通过':>11s} {'覆盖目标':>9s} {'R3误报':>8s} "
        f"{'全链识别':>9s} {'可执行':>7s} {'确认→收盘':>11s} {'识别日期'}"
    )
    print("-" * 100)
    for confirm_time in ("09:35", "09:40", "09:45", "09:50", "10:00", "10:30"):
        params = dataclasses.replace(base, confirm_time=confirm_time)
        rows = build_feature_rows(daily, external, intraday, dates, params)
        local = summarize_stage(rows, params, "R3")
        full = summarize_rows(rows, params)
        print(
            f"  {confirm_time:>7s} {local['passed']:>11d} {local['true_positive']:>9d} "
            f"{local['false_positive']:>8d} {full['signal_count']:>9d} {full['actionable_count']:>7d} "
            f"{_fmt(full['median_to_close'], '%'):>11s} "
            f"{','.join(day.strftime('%m-%d') for day in full['signal_dates']) or '-'}"
        )
    print("=" * 100)


def _latest_trade_date_before(daily: pd.DataFrame, day: dt.date) -> Optional[dt.date]:
    dates = trading_dates(daily, end=day - dt.timedelta(days=1))
    return dates[-1] if dates else None


def compute_live_intraday_features(rows: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    by_code = {str(row.get("code")): row for row in rows}
    observations = []
    for code in WATCH_CODES:
        row = by_code.get(code)
        if not row or not row.get("prev_close"):
            continue
        observations.append({
            "code": code,
            "pct": float(row.get("pct") or 0),
            "above_open": float(row.get("price") or 0) >= float(row.get("open") or 0),
        })
    core_above = sum(
        bool(item["above_open"]) for item in observations if item["code"] in CORE_CODES
    )
    sh = by_code.get("sh.000001")
    cyb = by_code.get("sz.399006")
    return {
        "coverage": len(observations) / len(WATCH_CODES),
        "breadth": float(np.mean([item["pct"] > 0 for item in observations])) if observations else None,
        "median_pct": _safe_median(item["pct"] for item in observations),
        "above_open_ratio": float(np.mean([item["above_open"] for item in observations])) if observations else None,
        "core_above_open": core_above,
        "core_coverage": len([item for item in observations if item["code"] in CORE_CODES]) / len(CORE_CODES),
        "style_spread": (
            float(cyb.get("pct") or 0) - float(sh.get("pct") or 0)
            if cyb and sh else None
        ),
        "cyb_pct": float(cyb.get("pct") or 0) if cyb else None,
        "sh_pct": float(sh.get("pct") or 0) if sh else None,
        "volume_speed": None,
    }


def compute_live_auction_features(rows: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    by_code = {str(row.get("code")): row for row in rows}
    gaps = []
    for code in CORE_CODES:
        row = by_code.get(code)
        prev = float(row.get("prev_close") or 0) if row else 0
        if row and prev > 0:
            gaps.append((float(row.get("open") or 0) / prev - 1) * 100)
    index_gaps = {}
    for code in INDEX_CODES:
        row = by_code.get(code)
        prev = float(row.get("prev_close") or 0) if row else 0
        if row and prev > 0:
            index_gaps[code] = (float(row.get("open") or 0) / prev - 1) * 100
    cyb_gap = index_gaps.get("sz.399006")
    sh_gap = index_gaps.get("sh.000001")
    return {
        "core_gaps": gaps,
        "core_coverage": len(gaps) / len(CORE_CODES),
        "core_median_gap": _safe_median(gaps),
        "cyb_gap": cyb_gap,
        "sh_gap": sh_gap,
        "style_gap": (cyb_gap - sh_gap) if cyb_gap is not None and sh_gap is not None else None,
    }


def run_live(params: RadarParams) -> int:
    today = dt.date.today()
    start = today - dt.timedelta(days=100)
    all_codes = tuple(dict.fromkeys((*WATCH_CODES, *INDEX_CODES)))
    daily = load_local_daily(all_codes, start, today)
    if daily.empty:
        print("本地K线为空，无法计算R0。")
        return 2
    prior_date = _latest_trade_date_before(daily, today)
    if prior_date is None:
        print("缺少前一交易日数据。")
        return 2
    external_start = today - dt.timedelta(days=14)
    external = fetch_yahoo_many(YAHOO_DAILY_SYMBOLS, external_start, today, "1d", workers=5)
    setup = compute_setup_features(daily, today)
    overnight = compute_overnight_features(external, today)
    quotes = fetch_realtime(all_codes, batch_size=10)
    auction = compute_live_auction_features(quotes)
    intraday = compute_live_intraday_features(quotes)
    row = {"setup": setup, "overnight": overnight, "auction": auction, "intraday": intraday}
    verdict = evaluate_feature_row(row, params)

    now = dt.datetime.now()
    before_open = now.time() < dt.time(9, 25)
    before_confirm = now.time() < _cutoff_time(params.confirm_time)
    if before_open:
        verdict["r2"] = verdict["r3"] = verdict["signal"] = verdict["actionable"] = False
    elif before_confirm:
        verdict["r3"] = verdict["signal"] = verdict["actionable"] = False

    level = "无信号"
    if verdict["r0"] and verdict["r1"]:
        level = "🟡 R1黄色预警"
    if verdict["r0"] and verdict["r1"] and verdict["r2"]:
        level = "🟠 R2竞价预备"
    if verdict["signal"] and verdict["auction_overheated"]:
        level = "🔴 R3扩散确认，但竞价过热：禁止追高"
    elif verdict["actionable"]:
        level = "🔴 R3扩散确认（仅进入交易评估）"

    print("=" * 96)
    print(f"  科技超跌反弹早识别雷达  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 96)
    print(f"  当前级别：{level}")
    print(f"  R0弹簧：{'通过' if verdict['r0'] else '未通过'}  {verdict['score0']}分  {'；'.join(verdict['reasons0']) or '-'}")
    print(f"  R1点火：{'通过' if verdict['r1'] else '未通过'}  {verdict['score1']:.1f}分  {'；'.join(verdict['reasons1']) or '-'}")
    print(f"  R2竞价：{'通过' if verdict['r2'] else '未通过'}  {verdict['score2']}分  {'；'.join(verdict['reasons2']) or '-'}")
    print(f"  R3扩散：{'通过' if verdict['r3'] else '未通过'}  {verdict['score3']}项  {'；'.join(verdict['reasons3']) or '-'}")
    print(
        f"  实时宽度 {_fmt((intraday.get('breadth') or 0)*100, '%', 0)}  "
        f"篮子中位 {_fmt(intraday.get('median_pct'), '%')}  "
        f"核心站开盘价 {intraday.get('core_above_open', 0)}/3  "
        f"成长/上证差 {_fmt(intraday.get('style_spread'), 'pct')}"
    )
    print("  风控：雷达不覆盖M5/退潮闸门，不自动下单；核心跌回开盘价或宽度低于60%即失效。")
    print("=" * 96)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="科技超跌反弹早识别雷达")
    parser.add_argument("--backtest-days", type=int, default=0, help="回放最近N个交易日；0=实时雷达")
    parser.add_argument("--as-of", default=None, help="回放截止日 YYYY-MM-DD")
    parser.add_argument("--confirm-time", default=DEFAULT_PARAMS.confirm_time, help="R3确认时点，默认09:40")
    parser.add_argument("--sensitivity", action="store_true", help="回放后输出单参数敏感性")
    args = parser.parse_args()
    params = dataclasses.replace(DEFAULT_PARAMS, confirm_time=args.confirm_time)
    if args.backtest_days > 0:
        as_of = _date_floor(args.as_of) if args.as_of else None
        daily, external, intraday, dates = _prepare_backtest_data(
            args.backtest_days, as_of, params.confirm_time
        )
        rows = build_feature_rows(daily, external, intraday, dates, params)
        print_backtest(rows, params)
        if args.sensitivity:
            print_sensitivity(rows, params)
            print_confirm_time_sensitivity(daily, external, intraday, dates, params)
        return 0
    return run_live(params)


if __name__ == "__main__":
    raise SystemExit(main())
