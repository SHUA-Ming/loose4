#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""早盘实时筛选：昨收结构底座 + 腾讯盘中快照。

流程只读，不写日线库：
1. 主板股票近7个交易日存在涨幅>=4%、量比>=1.2的大阳线；
2. 实时涨幅0.5%~4%、站上用实时价重算的MA5；
3. 套S2八分制、行业动量、梯队和X6/X7/X8；
4. market_mode仍负责最终开仓闸门，本脚本不越权授权。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_TOOL_ROOT = Path(__file__).resolve().parents[1]
for _folder in (_TOOL_ROOT / "00_公共核心", _TOOL_ROOT / "10_数据更新", _TOOL_ROOT / "20_市场环境"):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

from project_paths import ensure_tool_paths

ensure_tool_paths()
from chip_cost import analyze_chip_cost
from db_cache import get_connection
from market_mode import get_mode_params
from tmp_rt import fetch_realtime


def _is_mainboard(code: str) -> bool:
    digits = str(code).split(".")[-1]
    return digits.startswith("00") or digits.startswith("60")


def _elapsed_fraction(now: datetime) -> float:
    current = now.time()
    morning_open = clock_time(9, 30)
    morning_close = clock_time(11, 30)
    afternoon_open = clock_time(13, 0)
    afternoon_close = clock_time(15, 0)
    if current <= morning_open:
        minutes = 1
    elif current <= morning_close:
        minutes = (now - now.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60
    elif current < afternoon_open:
        minutes = 120
    elif current <= afternoon_close:
        minutes = 120 + (now - now.replace(hour=13, minute=0, second=0, microsecond=0)).total_seconds() / 60
    else:
        minutes = 240
    return max(0.03, min(float(minutes) / 240.0, 1.0))


def _load_data():
    conn = get_connection(readonly=True)
    latest = conn.execute(
        "SELECT MAX(date) FROM kline_daily WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'"
    ).fetchone()[0]
    sql = """
        SELECT * FROM kline_daily
        WHERE date >= (
            SELECT DISTINCT date FROM kline_daily WHERE code='sh.000001'
            ORDER BY date DESC LIMIT 1 OFFSET 120
        )
        ORDER BY code,date
    """
    frame = pd.read_sql(sql, conn._conn)
    for col in ("open", "high", "low", "close", "volume", "amount", "turn", "pctChg"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame[frame["code"].map(_is_mainboard)].dropna(subset=["close", "volume"])

    industry = pd.read_sql(
        """
        SELECT s.code,l3.board_name AS industry
        FROM em_stock_board_l3 s JOIN em_board_l3 l3 ON s.l3_id=l3.id
        """,
        conn._conn,
    )
    industry_map = dict(zip(industry["code"], industry["industry"]))
    board = pd.read_sql(
        """
        SELECT l3.board_name AS industry,d.date,d.pctChg
        FROM em_board_daily d JOIN em_board_l3 l3 ON d.board_code=l3.board_code
        WHERE d.level=3 ORDER BY d.board_code,d.date
        """,
        conn._conn,
    )
    conn.close()
    board["pctChg"] = pd.to_numeric(board["pctChg"], errors="coerce")
    momentum = {}
    for name, grp in board.groupby("industry"):
        values = grp.sort_values("date")["pctChg"].dropna().values
        if len(values) >= 5:
            momentum[name] = float(np.mean(values[-5:]))
    ranked = sorted(momentum, key=momentum.get)
    sector_rank = {name: i / max(len(ranked) - 1, 1) for i, name in enumerate(ranked)}
    return str(latest), frame, industry_map, sector_rank


def _industry_stage(industry: str, sector_rank: float, rising: set[str], falling: set[str]) -> str:
    if not industry:
        return "—"
    if industry in rising:
        return "持续主线" if sector_rank >= 0.8 else "上升轮动"
    if sector_rank >= 0.85:
        return "强势板块"
    if industry in falling:
        return "退潮"
    return "普通轮动"


def _prefilter(frame: pd.DataFrame, industry_map: dict, sector_rank: dict, mode: dict):
    rotation = mode.get("sector_rotation") or {}
    rising = {str(x.get("industry") or "") for x in rotation.get("rising", [])}
    falling = {str(x.get("industry") or "") for x in rotation.get("falling", [])}
    candidates = []
    stock_chg5 = {}
    groups = {}
    for code, grp in frame.groupby("code"):
        grp = grp.sort_values("date").reset_index(drop=True)
        groups[code] = grp
        if len(grp) >= 6:
            stock_chg5[code] = (float(grp.iloc[-1]["close"]) / float(grp.iloc[-6]["close"]) - 1) * 100

    sector_members = {}
    for code, change in stock_chg5.items():
        ind = industry_map.get(code, "")
        if ind:
            sector_members.setdefault(ind, []).append((code, change))
    tier_info = {}
    for ind, members in sector_members.items():
        ordered = sorted(members, key=lambda item: item[1], reverse=True)
        for index, (code, change) in enumerate(ordered):
            rank_pct = index / max(len(ordered) - 1, 1)
            tier = "龙头" if rank_pct <= 0.15 else "跟风" if rank_pct <= 0.50 else "补涨"
            tier_info[code] = (rank_pct, tier, change)

    for code, grp in groups.items():
        if len(grp) < 60:
            continue
        closes = grp["close"].values.astype(float)
        opens = grp["open"].values.astype(float)
        volumes = grp["volume"].values.astype(float)
        amounts = grp["amount"].values.astype(float)
        turns = grp["turn"].values.astype(float)
        if np.nanmean(amounts[-20:]) / 10000 < 1000:
            continue
        big_index = None
        big_ratio = 0.0
        for index in range(len(grp) - 1, max(0, len(grp) - 8), -1):
            if index < 20:
                break
            prior_avg = float(np.nanmean(volumes[index - 20:index]))
            day_pct = (closes[index] / closes[index - 1] - 1) * 100
            ratio = volumes[index] / prior_avg if prior_avg > 0 else 0.0
            if day_pct >= 4.0 and closes[index] > opens[index] and ratio >= 1.2:
                big_index, big_ratio = index, ratio
                break
        if big_index is None:
            continue
        days_after = len(grp) - 1 - big_index
        if days_after > 0:
            post_pcts = grp["pctChg"].values[big_index + 1:].astype(float)
            if np.any(post_pcts < -3.0):
                continue
        ind = industry_map.get(code, "")
        rank = float(sector_rank.get(ind, 0.5))
        stage = _industry_stage(ind, rank, rising, falling)
        tier_rank, tier, change5 = tier_info.get(code, (0.5, "—", stock_chg5.get(code, 0.0)))
        candidates.append({
            "code": code,
            "group": grp,
            "industry": ind,
            "sector_rank": rank,
            "stage": stage,
            "tier_rank": float(tier_rank),
            "tier": tier,
            "chg5": float(change5),
            "big_index": big_index,
            "big_date": str(grp.iloc[big_index]["date"]),
            "big_open": float(opens[big_index]),
            "big_close": float(closes[big_index]),
            "big_pct": float((closes[big_index] / closes[big_index - 1] - 1) * 100),
            "big_vol_ratio": float(big_ratio),
            "avg_volume20": float(np.nanmean(volumes[-20:])),
        })
    return candidates


def _fetch_quotes(codes):
    rows = []
    for start in range(0, len(codes), 10):
        rows.extend(fetch_realtime(codes[start:start + 10], batch_size=10, timeout=10))
        time.sleep(0.04)
    return {row["code"]: row for row in rows}


def _evaluate(prefiltered, quotes, now, mode):
    fraction = _elapsed_fraction(now)
    results = []
    for item in prefiltered:
        quote = quotes.get(item["code"])
        if not quote:
            continue
        price = float(quote["price"])
        pct = float(quote["pct"])
        name = str(quote.get("name") or "")
        if "ST" in name.upper() or price < 3 or price > 200:
            continue
        if not (0.5 <= pct <= 4.0):
            continue
        grp = item["group"]
        closes = grp["close"].values.astype(float)
        ma5 = float(np.mean(np.r_[closes[-4:], price]))
        ma10 = float(np.mean(np.r_[closes[-9:], price]))
        ma20 = float(np.mean(np.r_[closes[-19:], price]))
        ma60 = float(np.mean(np.r_[closes[-59:], price]))
        if price <= ma5 or price <= ma60:
            continue
        projected_ratio = float(quote["volume"]) / max(item["avg_volume20"] * fraction, 1.0)
        day_range = float(quote["high"]) - float(quote["low"])
        close_pos = (price - float(quote["low"])) / day_range if day_range > 0 else 0.5
        x6 = projected_ratio >= 1.8 and pct < 1.0 and close_pos < 0.55
        x7 = projected_ratio < 0.75
        if x6 or x7:
            continue

        post = grp.iloc[item["big_index"] + 1:]
        post_volume = float(post["volume"].mean()) if not post.empty else item["avg_volume20"]
        vol_shrink = post_volume / max(float(grp.iloc[item["big_index"]]["volume"]), 1.0)
        price_hold = price / item["big_close"]
        score = 0
        score += 2 if vol_shrink <= 0.5 else 1 if vol_shrink <= 0.7 else 0
        score += 2 if price_hold >= 0.99 else 1 if price_hold >= 0.97 else 0
        score += 2 if item["sector_rank"] >= 0.7 else 1 if item["sector_rank"] >= 0.5 else 0
        score += 2 if ma5 > ma10 > ma20 else 1 if ma5 > ma10 else 0
        grade = "A" if score >= 7 else "B" if score >= 6 else "C"
        if grade != "A":
            continue

        chip_frame = grp[["date", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]].copy()
        chip = analyze_chip_cost(chip_frame, current_price=price)
        resistance = chip.get("resistance_zone") if chip else None
        x8 = bool(resistance and float(resistance[0]) <= price * 1.03)
        if x8:
            continue

        entry_low = item["big_close"] * 0.98
        entry_high = item["big_close"] * 1.02
        if pct > 3.0 or price > entry_high * 1.03:
            status = "放弃"
        elif price > entry_high:
            status = "等回踩"
        elif price < entry_low:
            status = "等确认"
        else:
            status = "可买"
        if mode.get("position_modifier", 1.0) <= 0:
            status = "仅观察"
        results.append({
            **{key: value for key, value in item.items() if key != "group"},
            "name": name,
            "price": price,
            "pct": pct,
            "open": float(quote["open"]),
            "high": float(quote["high"]),
            "low": float(quote["low"]),
            "turn": float(quote.get("turn") or 0),
            "outer_inner": float(quote.get("outer_inner") or 0),
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "projected_volume_ratio": projected_ratio,
            "close_position": close_pos,
            "vol_shrink": vol_shrink,
            "price_hold": price_hold,
            "score": score,
            "grade": grade,
            "strategy": "S2",
            "status": status,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "hard_stop": item["big_open"],
            "soft_stop": price * 0.985,
            "target1": price * 1.0225,
            "target2": price * 1.04,
            "resistance": list(resistance) if resistance else None,
        })
    stage_priority = {"持续主线": 0, "上升轮动": 1, "强势板块": 2, "普通轮动": 3, "退潮": 4, "—": 5}
    return sorted(results, key=lambda row: (
        0 if row["status"] == "可买" else 1,
        stage_priority.get(row["stage"], 9),
        0 if row["tier"] == "龙头" else 1,
        -row["score"],
        -row["projected_volume_ratio"],
    ))


def main():
    parser = argparse.ArgumentParser(description="早盘全市场实时大阳线筛选")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    now = datetime.now()
    mode = get_mode_params()
    latest, frame, industry_map, sector_rank = _load_data()
    prefiltered = _prefilter(frame, industry_map, sector_rank, mode)
    quotes = _fetch_quotes([item["code"] for item in prefiltered])
    results = _evaluate(prefiltered, quotes, now, mode)
    payload = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "daily_as_of": latest,
        "mode": mode["mode"],
        "cycle_phase": mode["cycle_phase"],
        "position_modifier": mode["position_modifier"],
        "prefiltered": len(prefiltered),
        "quotes": len(quotes),
        "qualified": len(results),
        "results": results[: max(0, args.top)],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("=" * 140)
    print(f"  早盘实时筛选 {payload['time']}  日线底座:{latest}  {mode['mode']}/{mode['cycle_phase']} pos_mod={mode['position_modifier']}")
    print(f"  近7日放量大阳预筛:{len(prefiltered)}  实时报价:{len(quotes)}  A级通过X6/X7/X8:{len(results)}")
    print("=" * 140)
    print(f"{'#':>2} {'代码':<11} {'股票':<9} {'现价':>8} {'涨幅':>7} {'量速':>6} {'评分':>5} {'行业':<13} {'阶段':<6} {'梯队':<4} {'状态':<6} {'买入区'}")
    for index, row in enumerate(results[: args.top], 1):
        print(
            f"{index:>2} {row['code']:<11} {row['name'][:8]:<9} {row['price']:>8.2f} {row['pct']:>+6.2f}% "
            f"{row['projected_volume_ratio']:>5.2f}x {row['score']}/8A {row['industry'][:12]:<13} {row['stage']:<6} "
            f"{row['tier']:<4} {row['status']:<6} {row['entry_low']:.2f}~{row['entry_high']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
