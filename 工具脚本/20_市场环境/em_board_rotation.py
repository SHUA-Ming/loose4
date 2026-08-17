#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富三级行业 两周轮动分析。

统一板块口径：
  - em_board_l1/l2/l3            东方财富一级/二级/三级行业树
  - em_stock_board_l3            个股唯一绑定到三级行业
  - em_board_daily               三层行业指数日K

本脚本只读连接，不建表、不写库；可独立运行，也可被 market_mode/offline_screener 导入。
"""
import argparse
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
from db_cache import get_connection
import numpy as np
import pandas as pd


STRONG_STAGES = ("持续主线", "上升轮动", "强势板块", "新晋活跃")

TOMORROW_PRIORITY = "\u660e\u65e5\u4f18\u5148"
TOMORROW_DOWNGRADE = "\u964d\u6743\u884c\u4e1a"
TOMORROW_BAN = "\u7981\u5165\u884c\u4e1a"
TOMORROW_WATCH = "\u89c2\u5bdf\u5f85\u5b9a"
STAGE_PERSIST = "\u6301\u7eed\u4e3b\u7ebf"
STAGE_ACCEL = "\u4e0a\u5347\u8f6e\u52a8"
STAGE_STEADY = "\u5f3a\u52bf\u677f\u5757"
STAGE_FRESH = "\u65b0\u664b\u6d3b\u8dc3"
STAGE_FADE = "\u9000\u6f6e"
STAGE_NORMAL = "\u666e\u901a\u8f6e\u52a8"

PV_SHADOW_VERSION = "PV_SHADOW_V1"
PV_SHADOW_ACTIVATION = False  # 98日近似walk-forward未证明隔日增益，禁止进入正式排序/仓位
PV_MARKET_CODES = ("sh.000001", "sz.399001", "sz.399006")
PV_HISTORY_CALENDAR_DAYS = 120
PV_MIN_ACTIVE_MEMBERS = 5


def _compound(pcts):
    value = 1.0
    for pct in pcts:
        value *= 1 + (pct or 0) / 100
    return (value - 1) * 100


def _display_stage(stage):
    """Keep user-facing terminology inside the current six-stage vocabulary."""
    return STAGE_ACCEL if stage == STAGE_FRESH else stage


def _display_rotation_text(text):
    return str(text or "").replace(STAGE_FRESH, STAGE_ACCEL)


def _load_info(conn):
    l1_rows = conn.execute(
        "SELECT board_code, board_name FROM em_board_l1 ORDER BY board_code"
    ).fetchall()
    l3_rows = conn.execute("""
        SELECT l3.board_code, l3.board_name, l2.board_name, l1.board_name
        FROM em_board_l3 l3
        JOIN em_board_l2 l2 ON l3.l2_id = l2.id
        JOIN em_board_l1 l1 ON l3.l1_id = l1.id
        ORDER BY l3.board_code
    """).fetchall()
    info = {}
    for code, name in l1_rows:
        info[code] = {"name": name, "level": 1, "l1": name, "l2": ""}
    for code, name, l2, l1 in l3_rows:
        info[code] = {"name": name, "level": 3, "l1": l1, "l2": l2}
    return info, [r[0] for r in l3_rows], [r[0] for r in l1_rows]


def _load_daily(conn, codes, start_date, end_date=None):
    if not codes:
        return pd.DataFrame()
    frames = []
    for i in range(0, len(codes), 300):
        part = codes[i:i + 300]
        ph = ",".join(["?"] * len(part))
        sql = (
            f"SELECT board_code AS code,date,close,amount,pctChg "
            f"FROM em_board_daily WHERE board_code IN ({ph}) AND date >= ?"
        )
        params = part + [start_date]
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = df["date"].astype(str)
    for col in ("close", "amount", "pctChg"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_pv_board_daily(conn, codes, as_of, calendar_days=PV_HISTORY_CALENDAR_DAYS):
    """Load the independent price-volume window without touching formal metrics."""
    if not codes:
        return pd.DataFrame()
    anchor = datetime.strptime(str(as_of), "%Y-%m-%d")
    start_date = (anchor - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    frames = []
    for i in range(0, len(codes), 300):
        part = codes[i:i + 300]
        ph = ",".join(["?"] * len(part))
        df = pd.read_sql_query(
            f"SELECT board_code AS code,date,open,high,low,close,amount,pctChg,updated_at "
            f"FROM em_board_daily WHERE board_code IN ({ph}) "
            f"AND date >= ? AND date <= ?",
            conn,
            params=part + [start_date, as_of],
        )
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["date"] = df["date"].astype(str)
    for col in ("open", "high", "low", "close", "amount", "pctChg"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_pv_members_today(conn, as_of):
    """Load all L3 constituents for one date in one bulk query (no industry N+1)."""
    df = pd.read_sql_query(
        """
        SELECT l3.board_code AS code, s.code AS stock_code,
               k.close, k.amount, k.pctChg
        FROM em_stock_board_l3 s
        JOIN em_board_l3 l3 ON s.l3_id = l3.id
        JOIN kline_daily k FORCE INDEX (PRIMARY)
          ON k.code = s.code AND k.date = ?
        """,
        conn,
        params=[as_of],
    )
    if df.empty:
        return df
    for col in ("close", "amount", "pctChg"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_pv_market_today(conn, as_of):
    """Return the equal-weight return of three broad indices only when all are present."""
    ph = ",".join(["?"] * len(PV_MARKET_CODES))
    rows = conn.execute(
        f"SELECT code,pctChg FROM kline_daily WHERE code IN ({ph}) AND date = ?",
        list(PV_MARKET_CODES) + [as_of],
    ).fetchall()
    values = {
        str(code): float(pct)
        for code, pct in rows
        if pct is not None and np.isfinite(float(pct))
    }
    if any(code not in values for code in PV_MARKET_CODES):
        return None, len(values)
    return float(np.mean([values[code] for code in PV_MARKET_CODES])), len(values)


def _pv_band_score(value, bands):
    """Score an ordered list of ``(lower_bound, score)`` from high to low."""
    for lower_bound, score in bands:
        if value >= lower_bound:
            return score
    return bands[-1][1]


def _build_pv_features(board_df, member_df, market_pct, as_of):
    """Build six close-only shadow features; every baseline excludes the target day."""
    expected_last3 = sorted(
        date for date in board_df["date"].astype(str).unique() if date <= str(as_of)
    )[-3:]
    member_stats = {}
    if member_df is not None and not member_df.empty:
        active = member_df[
            member_df["close"].gt(0)
            & member_df["amount"].gt(0)
            & member_df["pctChg"].notna()
        ].copy()
        active["positive_contribution"] = active["pctChg"].clip(lower=0) * active["amount"]
        for code, grp in active.groupby("code"):
            active_count = int(len(grp))
            up_count = int(grp["pctChg"].gt(0).sum())
            positive = grp[grp["positive_contribution"].gt(0)]["positive_contribution"]
            positive_count = int(len(positive))
            concentration = None
            if positive_count >= 3 and float(positive.sum()) > 0:
                concentration = float(positive.nlargest(3).sum() / positive.sum())
            member_stats[str(code)] = {
                "active_count": active_count,
                "up_count": up_count,
                "breadth": float(up_count / active_count) if active_count else None,
                "positive_count": positive_count,
                "top3_concentration": concentration,
            }

    features = {}
    today_iso = datetime.now().strftime("%Y-%m-%d")
    for code, raw_grp in board_df.groupby("code"):
        grp = raw_grp.sort_values("date").copy()
        grp["prior20_amount"] = grp["amount"].shift(1).rolling(20, min_periods=20).mean()
        grp["day_amount_ratio20"] = grp["amount"] / grp["prior20_amount"]
        today_rows = grp[grp["date"].eq(str(as_of))]
        if today_rows.empty:
            continue
        today = today_rows.iloc[-1]
        today_pct = float(today["pctChg"]) if pd.notna(today["pctChg"]) else None
        notes = []
        component_scores = {}

        amount_ratio20 = None
        if pd.notna(today["day_amount_ratio20"]) and np.isfinite(float(today["day_amount_ratio20"])):
            amount_ratio20 = float(today["day_amount_ratio20"])
            if today_pct is None:
                notes.append("行业涨幅缺失")
            elif today_pct > 0:
                component_scores["price_volume"] = _pv_band_score(
                    amount_ratio20, ((1.50, 2), (1.20, 1), (0.80, 0), (-np.inf, -1))
                )
            elif today_pct < 0 and amount_ratio20 >= 1.20:
                component_scores["price_volume"] = -1
                notes.append("放量下跌")
            else:
                component_scores["price_volume"] = 0
        else:
            notes.append("20日量能基线不足")

        close_pos = None
        if pd.notna(today["high"]) and pd.notna(today["low"]) and pd.notna(today["close"]):
            day_range = float(today["high"] - today["low"])
            if day_range > 0:
                close_pos = float(np.clip((today["close"] - today["low"]) / day_range, 0, 1))
                component_scores["close_position"] = _pv_band_score(
                    close_pos, ((0.75, 2), (0.60, 1), (0.35, 0), (-np.inf, -1))
                )
            elif day_range == 0:
                close_pos = 0.5
                component_scores["close_position"] = 0
                notes.append("平K线")
            else:
                notes.append("日K高低价异常")
        else:
            notes.append("日K价格缺失")

        excess_pct = None
        if today_pct is not None and market_pct is not None:
            excess_pct = today_pct - market_pct
            component_scores["relative_strength"] = _pv_band_score(
                excess_pct, ((2.0, 2), (0.5, 1), (-0.5, 0), (-np.inf, -1))
            )
        else:
            notes.append("三指数同日基准不足" if market_pct is None else "行业涨幅缺失")

        members = member_stats.get(str(code))
        breadth = None
        active_count = 0
        up_count = 0
        positive_count = 0
        top3_concentration = None
        if members:
            active_count = members["active_count"]
            up_count = members["up_count"]
            positive_count = members["positive_count"]
            breadth = members["breadth"]
            top3_concentration = members["top3_concentration"]
            if active_count < PV_MIN_ACTIVE_MEMBERS:
                component_scores["breadth"] = 0
                component_scores["concentration"] = 0
                notes.append("成分不足5只，宽度/集中度按中性")
            else:
                if breadth >= 0.65 and up_count >= 3:
                    component_scores["breadth"] = 2
                elif breadth >= 0.55:
                    component_scores["breadth"] = 1
                elif breadth < 0.35:
                    component_scores["breadth"] = -1
                else:
                    component_scores["breadth"] = 0

                if top3_concentration is None:
                    component_scores["concentration"] = -1
                    notes.append("上涨贡献不足3只")
                else:
                    component_scores["concentration"] = _pv_band_score(
                        -top3_concentration,
                        ((-0.50, 2), (-0.70, 1), (-0.85, 0), (-np.inf, -1)),
                    )
        else:
            notes.append("当日成分行情缺失")

        last3 = grp[grp["date"].le(str(as_of))].tail(3)
        confirm_days = None
        confirm_streak = None
        cum3 = None
        if (
            len(last3) == 3
            and last3["date"].astype(str).tolist() == expected_last3
            and str(last3.iloc[-1]["date"]) == str(as_of)
            and last3["pctChg"].notna().all()
            and last3["day_amount_ratio20"].notna().all()
        ):
            confirm_flags = (
                last3["pctChg"].gt(0) & last3["day_amount_ratio20"].ge(0.90)
            ).tolist()
            confirm_days = int(sum(confirm_flags))
            confirm_streak = 0
            for confirmed in reversed(confirm_flags):
                if not confirmed:
                    break
                confirm_streak += 1
            cum3 = float(_compound(last3["pctChg"].tolist()))
            if confirm_streak == 3:
                component_scores["continuity_3d"] = 2
            elif confirm_streak == 2:
                component_scores["continuity_3d"] = 1
            elif (
                float(last3.iloc[-1]["pctChg"]) < 0
                and (
                    float(last3.iloc[-1]["day_amount_ratio20"]) >= 1.20
                    or cum3 < 0
                )
            ):
                component_scores["continuity_3d"] = -1
            else:
                component_scores["continuity_3d"] = 0
        else:
            notes.append("3日量价基线不足")

        intraday_snapshot = False
        if str(as_of) == today_iso and pd.notna(today.get("updated_at")):
            updated_at = pd.to_datetime(today["updated_at"], errors="coerce")
            if pd.notna(updated_at) and updated_at.strftime("%Y-%m-%d") == str(as_of):
                intraday_snapshot = (updated_at.hour, updated_at.minute) < (15, 0)
        if intraday_snapshot:
            notes.append("当日快照早于15:00")

        complete = (
            len(component_scores) == 6
            and active_count > 0
            and not intraday_snapshot
        )
        pv_score = int(sum(component_scores.values())) if complete else None
        signals = []
        if complete:
            if (
                today_pct > 0
                and amount_ratio20 >= 1.20
                and breadth is not None and breadth >= 0.55
                and close_pos is not None and close_pos >= 0.60
            ):
                signals.append("量价共振")
            if today_pct < 0 and amount_ratio20 >= 1.20:
                signals.append("放量下跌")
            elif today_pct > 0 and amount_ratio20 < 0.80:
                signals.append("缩量上涨")
            if breadth is not None and active_count >= PV_MIN_ACTIVE_MEMBERS:
                if breadth >= 0.65:
                    signals.append("宽度扩散")
                elif breadth < 0.35:
                    signals.append("宽度不足")
            if close_pos is not None and close_pos < 0.35:
                signals.append("收位偏弱")
            if top3_concentration is not None and top3_concentration > 0.85:
                signals.append("单点驱动")
            if confirm_streak is not None and confirm_streak >= 2:
                signals.append("连续确认")
            if excess_pct is not None:
                if excess_pct >= 0.50:
                    signals.append("相对强")
                elif excess_pct < -0.50:
                    signals.append("相对弱")
        features[str(code)] = {
            "version": PV_SHADOW_VERSION,
            "as_of": str(as_of),
            "complete": complete,
            "confidence": "normal" if active_count >= PV_MIN_ACTIVE_MEMBERS else "low",
            "today_pct": round(today_pct, 4) if today_pct is not None else None,
            "amount_ratio20": round(amount_ratio20, 4) if amount_ratio20 is not None else None,
            "breadth": round(breadth, 4) if breadth is not None else None,
            "up_count": up_count,
            "active_count": active_count,
            "close_position": round(close_pos, 4) if close_pos is not None else None,
            "market_pct": round(market_pct, 4) if market_pct is not None else None,
            "excess_pct": round(excess_pct, 4) if excess_pct is not None else None,
            "top3_concentration": (
                round(top3_concentration, 4) if top3_concentration is not None else None
            ),
            "positive_count": positive_count,
            "confirm_days_3d": confirm_days,
            "confirm_streak_3d": confirm_streak,
            "cum3": round(cum3, 4) if cum3 is not None else None,
            "component_scores": component_scores,
            "score": pv_score,
            "signals": signals,
            "notes": notes,
        }
    return features


def _attach_pv_shadow(metrics, features):
    """Build copied shadow records; never mutate or reorder formal metric objects."""
    eligible = []
    shadow_all = []
    eligible_formal_rank = 0
    for formal_rank, item in enumerate(metrics, 1):
        shadow = dict(features.get(str(item["code"]), {
            "version": PV_SHADOW_VERSION,
            "as_of": item.get("win_end"),
            "complete": False,
            "confidence": "missing",
            "score": None,
            "notes": ["量价数据缺失"],
        }))
        shadow["formal_rank"] = formal_rank
        record = dict(item)
        record["shadow"] = shadow
        if shadow.get("complete") and shadow.get("score") is not None:
            eligible_formal_rank += 1
            shadow["enhanced_score"] = round(float(item["score"]) + float(shadow["score"]), 1)
            shadow["eligible_formal_rank"] = eligible_formal_rank
            eligible.append(record)
        else:
            shadow["enhanced_score"] = None
            shadow["eligible_formal_rank"] = None
        shadow["enhanced_rank"] = None
        shadow["rank_delta"] = None
        shadow_all.append(record)

    eligible.sort(
        key=lambda item: (
            -item["shadow"]["enhanced_score"],
            -item["score"],
            str(item["code"]),
        )
    )
    for enhanced_rank, item in enumerate(eligible, 1):
        shadow = item["shadow"]
        shadow["enhanced_rank"] = enhanced_rank
        shadow["rank_delta"] = shadow["eligible_formal_rank"] - enhanced_rank
    return eligible, shadow_all


def _build_metrics(df, window, info):
    all_dates = sorted(df["date"].unique())
    if len(all_dates) < window:
        window = len(all_dates)
    if window < 4:
        return [], all_dates

    win = all_dates[-window:]
    half = window // 2
    wa, wb = win[:half], win[half:]
    recent20 = all_dates[-20:]

    rank_pct = {}
    for date, grp in df.groupby("date"):
        grp = grp.dropna(subset=["pctChg"]).sort_values("pctChg", ascending=False).reset_index(drop=True)
        n = len(grp)
        for i, row in grp.iterrows():
            rank_pct[(row["code"], date)] = (i + 1) / max(n, 1)

    metrics = []
    for code, grp in df.groupby("code"):
        grp = grp.sort_values("date")
        dd = dict(zip(grp["date"], grp["pctChg"]))
        am = dict(zip(grp["date"], grp["amount"]))
        present = sum(date in dd for date in win)
        if present < window - 1:
            continue
        pw = [dd.get(date, 0) for date in win]
        rp_full = [rank_pct.get((code, date), 1.0) for date in win]
        rp_a = [rank_pct.get((code, date), 1.0) for date in wa]
        rp_b = [rank_pct.get((code, date), 1.0) for date in wb]
        rp20 = [rank_pct.get((code, date), 1.0) for date in recent20 if (code, date) in rank_pct]
        am_a = np.mean([am.get(date, 0) or 0 for date in wa]) if wa else 0
        am_b = np.mean([am.get(date, 0) or 0 for date in wb]) if wb else 0
        meta = info.get(code, {})
        metrics.append({
            "code": code,
            "name": meta.get("name", code),
            "l1": meta.get("l1", "-"),
            "l2": meta.get("l2", "-"),
            "cum10": _compound(pw),
            "cumA": _compound([dd.get(date, 0) for date in wa]),
            "cumB": _compound([dd.get(date, 0) for date in wb]),
            "avg_rp": float(np.mean(rp_full)),
            "avg_rpA": float(np.mean(rp_a)) if rp_a else 1.0,
            "avg_rpB": float(np.mean(rp_b)) if rp_b else 1.0,
            "top20_full": int(sum(r <= 0.20 for r in rp_full)),
            "top20_A": int(sum(r <= 0.20 for r in rp_a)),
            "top20_B": int(sum(r <= 0.20 for r in rp_b)),
            "top20_20d": int(sum(r <= 0.20 for r in rp20)),
            "up_days": int(sum((pct or 0) > 0 for pct in pw)),
            "rank_improve": float(np.mean(rp_a) - np.mean(rp_b)) if (rp_a and rp_b) else 0.0,
            "amt_ratio": float(am_b / am_a) if am_a and am_a > 0 else 1.0,
            "last_rp": rp_full[-1],
            "window": window,
            "win_start": win[0],
            "win_end": win[-1],
        })

    for m in metrics:
        m["stage"] = _classify(m, window)
        m["score"] = _score(m)
        bucket, reason, bonus = _tomorrow_plan(m)
        m["tomorrow_bucket"] = bucket
        m["tomorrow_reason"] = reason
        m["tomorrow_bonus"] = bonus
    metrics.sort(key=lambda x: x["score"], reverse=True)
    return metrics, win


def _classify(m, window):
    strong_full = max(3, round(window * 0.5))
    if m["top20_A"] >= 2 and (m["avg_rpB"] - m["avg_rpA"]) >= 0.15 and m["cumB"] < 0:
        return "退潮"
    if m["top20_full"] >= strong_full and m["cum10"] > 0:
        return "持续主线"
    if m["avg_rpB"] <= 0.33 and m["rank_improve"] >= 0.10 and m["cumB"] > 1 and m["top20_B"] >= 2:
        return "上升轮动"
    if m["cum10"] >= 5 and m["up_days"] >= max(4, round(window * 0.6)) and m["cumB"] >= 0:
        return "强势板块"
    if m["last_rp"] <= 0.15 and m["cumB"] > 0:
        return "新晋活跃"
    return "普通轮动"


def _score(m):
    score = 0.0
    score += m["top20_full"] * 6
    score += min(max(m["cum10"], -10), 30) * 0.7
    score += min(max(m["cumB"], -10), 25) * 0.6
    score += max(m["rank_improve"], 0) * 30
    score += min(max(m["amt_ratio"] - 1, 0), 2) * 5
    score += m["top20_20d"] * 0.8
    if m["stage"] == "退潮":
        score -= 25
    elif m["stage"] == "持续主线":
        score += 7
    elif m["stage"] == "上升轮动":
        score += 5
    return round(score, 1)


def _tomorrow_plan(m):
    """Map rotation stage into next-day execution buckets.

    The bucket is intentionally conservative:
    - priority: enough continuity or fresh acceleration to justify stock selection first
    - downgrade: can be watched, but should not win tie-breaks by itself
    - ban: trend has faded enough that technical stock signals should not override it
    """
    stage = m.get("stage")
    cum_b = m.get("cumB", 0) or 0
    cum10 = m.get("cum10", 0) or 0
    avg_rpb = m.get("avg_rpB", 1.0) or 1.0
    last_rp = m.get("last_rp", 1.0) or 1.0
    top20_b = m.get("top20_B", 0) or 0
    rank_improve = m.get("rank_improve", 0) or 0
    amt_ratio = m.get("amt_ratio", 1.0) or 1.0

    if stage == STAGE_FADE:
        return TOMORROW_BAN, "\u5df2\u8fdb\u5165\u9000\u6f6e\uff0c\u524d\u5f3a\u540e\u5f31", -99
    if cum_b <= -5 and avg_rpb >= 0.60:
        return TOMORROW_BAN, "\u540e\u534a\u7a0b\u8dcc\u5e45\u5927\u4e14\u6392\u540d\u9760\u540e", -99
    if last_rp >= 0.80 and cum_b < 0:
        return TOMORROW_BAN, "\u6700\u65b0\u6392\u540d\u843d\u5230\u540e20%\u4e14\u540e\u534a\u7a0b\u8d70\u5f31", -99

    overheated = (cum_b >= 20 and amt_ratio >= 1.40) or cum10 >= 30
    if stage in (STAGE_PERSIST, STAGE_ACCEL):
        if overheated:
            return TOMORROW_DOWNGRADE, "\u8d70\u5f3a\u4f46\u77ed\u7ebf\u6da8\u5e45/\u91cf\u80fd\u504f\u70ed\uff0c\u53ea\u770b\u56de\u8e29", -1
        if cum_b > 0 and (top20_b >= 2 or last_rp <= 0.35):
            return TOMORROW_PRIORITY, "\u8fde\u7eed\u6027/\u8d44\u91d1\u5207\u5165\u786e\u8ba4", 1
        return TOMORROW_DOWNGRADE, "\u9636\u6bb5\u5f3a\u4f46\u8fd1\u534a\u7a0b\u786e\u8ba4\u4e0d\u8db3", -1

    if stage == STAGE_STEADY:
        if overheated:
            return TOMORROW_DOWNGRADE, "\u5f3a\u52bf\u4f46\u5df2\u6709\u8fc7\u70ed\u8ff9\u8c61", -1
        if cum_b >= 1 and last_rp <= 0.40:
            return TOMORROW_PRIORITY, "\u5f3a\u52bf\u4ecd\u5728\u5ef6\u7eed", 1
        return TOMORROW_DOWNGRADE, "\u5f3a\u52bf\u4e0d\u7a33\uff0c\u7b49\u4e2a\u80a1\u5f3a\u786e\u8ba4", -1

    if stage == STAGE_FRESH:
        if rank_improve >= 0.12 and cum_b > 2 and last_rp <= 0.20:
            return TOMORROW_PRIORITY, "\u65b0\u664b\u6d3b\u8dc3\u4e14\u6392\u540d\u660e\u663e\u4e0a\u79fb", 1
        return TOMORROW_DOWNGRADE, "\u65b0\u664b\u6d3b\u8dc3\u9700\u7b49\u6301\u7eed\u6027", -1

    return TOMORROW_DOWNGRADE, "\u666e\u901a\u8f6e\u52a8\uff0c\u4e0d\u4f5c\u9009\u80a1\u4e3b\u653b\u65b9\u5411", -1


def _build_tomorrow_buckets(metrics):
    priority = [m for m in metrics if m.get("tomorrow_bucket") == TOMORROW_PRIORITY]
    downgrade = [m for m in metrics if m.get("tomorrow_bucket") == TOMORROW_DOWNGRADE]
    ban = [m for m in metrics if m.get("tomorrow_bucket") == TOMORROW_BAN]
    priority.sort(key=lambda x: (-x["score"], x["avg_rpB"], -x["cumB"]))
    downgrade.sort(key=lambda x: (-x["score"], x["avg_rpB"], -x["cumB"]))
    ban.sort(key=lambda x: (x["cumB"], -x["last_rp"], x["score"]))
    return priority, downgrade, ban


def _is_mainboard(code):
    return str(code).startswith("sh.60") or str(code).startswith("sz.00")


def _latest_kline_date(conn):
    row = conn.execute(
        "SELECT MAX(date) FROM kline_daily WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'"
    ).fetchone()
    return row[0] if row and row[0] else None


def _load_leaders(conn, l3_code, win_start, win_end, today, top_n=5):
    mem = conn.execute("""
        SELECT s.code, s.code_name
        FROM em_stock_board_l3 s
        JOIN em_board_l3 l3 ON s.l3_id = l3.id
        WHERE l3.board_code = ?
    """, (l3_code,)).fetchall()
    codes = [row[0] for row in mem]
    name_map = {row[0]: row[1] for row in mem}
    if not codes:
        return []

    out = []
    for i in range(0, len(codes), 300):
        part = codes[i:i + 300]
        ph = ",".join(["?"] * len(part))
        df = pd.read_sql_query(
            f"SELECT code,date,close,pctChg FROM kline_daily "
            f"WHERE code IN ({ph}) AND date >= ? AND date <= ?",
            conn,
            params=part + [win_start, today],
        )
        if df.empty:
            continue
        df["date"] = df["date"].astype(str)
        for col in ("close", "pctChg"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for code, grp in df.groupby("code"):
            grp = grp.sort_values("date").dropna(subset=["close"])
            in_win = grp[grp["date"] <= win_end]
            if len(in_win) < 2 or in_win.iloc[0]["close"] <= 0:
                continue
            chg2w = (in_win.iloc[-1]["close"] / in_win.iloc[0]["close"] - 1) * 100
            trow = grp[grp["date"] == str(today)]["pctChg"]
            today_pct = float(trow.iloc[0]) if len(trow) else float("nan")
            out.append({
                "code": code,
                "name": name_map.get(code, ""),
                "chg2w": chg2w,
                "today": today_pct,
                "mb": _is_mainboard(code),
            })
    out.sort(key=lambda x: x["chg2w"], reverse=True)
    return out[:top_n]


def _load_l1_context(conn, l1_codes, info, window, as_of=None):
    anchor = datetime.strptime(str(as_of), "%Y-%m-%d") if as_of else datetime.now()
    df = _load_daily(
        conn,
        l1_codes,
        (anchor - timedelta(days=40)).strftime("%Y-%m-%d"),
        end_date=as_of,
    )
    if df.empty:
        return []
    dates = sorted(df["date"].unique())
    win = dates[-window:] if len(dates) >= window else dates
    out = []
    for code, grp in df.groupby("code"):
        grp = grp.sort_values("date")
        dd = dict(zip(grp["date"], grp["pctChg"]))
        out.append({
            "code": code,
            "name": info.get(code, {}).get("name", code),
            "cum": _compound([dd.get(date, 0) for date in win]),
        })
    out.sort(key=lambda x: x["cum"], reverse=True)
    return out


def analyze(
    window=10,
    lookback=45,
    as_of=None,
    with_leaders=False,
    leader_top=5,
    with_pv_shadow=False,
):
    """Analyze formal rotation and optionally attach an isolated price-volume shadow."""
    conn = get_connection(readonly=True)
    info, l3_codes, l1_codes = _load_info(conn)
    if not l3_codes:
        conn.close()
        return {"error": "em_board_l3 为空，请先运行 _fetch_em_board_hierarchy.py"}

    anchor = datetime.strptime(str(as_of), "%Y-%m-%d") if as_of else datetime.now()
    start_date = (anchor - timedelta(days=lookback)).strftime("%Y-%m-%d")
    df = _load_daily(conn, l3_codes, start_date, end_date=as_of)
    if df.empty:
        conn.close()
        return {"error": "em_board_daily 为空，请先运行 _fetch_em_board_daily.py --days 60"}

    metrics, win = _build_metrics(df, window, info)
    l1_ctx = _load_l1_context(conn, l1_codes, info, window, as_of=win[-1])
    tomorrow_priority, tomorrow_downgrade, tomorrow_ban = _build_tomorrow_buckets(metrics)

    pv_shadow = []
    pv_shadow_all = []
    pv_error = None
    pv_market_pct = None
    pv_market_count = 0
    if with_pv_shadow and metrics:
        try:
            pv_as_of = win[-1]
            pv_board = _load_pv_board_daily(conn, l3_codes, pv_as_of)
            pv_members = _load_pv_members_today(conn, pv_as_of)
            pv_market_pct, pv_market_count = _load_pv_market_today(conn, pv_as_of)
            pv_features = _build_pv_features(
                pv_board,
                pv_members,
                pv_market_pct,
                pv_as_of,
            )
            pv_shadow, pv_shadow_all = _attach_pv_shadow(metrics, pv_features)
        except Exception as exc:
            pv_error = f"{type(exc).__name__}: {exc}"

    if with_leaders and metrics:
        today = win[-1] if as_of else (_latest_kline_date(conn) or win[-1])
        for m in metrics:
            if m["stage"] in STRONG_STAGES:
                m["leaders"] = _load_leaders(conn, m["code"], win[0], win[-1], today, top_n=leader_top)
        stock_today = today
    else:
        stock_today = None
    conn.close()

    if not metrics:
        return {"error": "东方财富三级行业日K不足，至少需要4个交易日"}
    result = {
        "win_start": win[0],
        "win_end": win[-1],
        "window": len(win),
        "stock_today": stock_today,
        "l3_count": len(metrics),
        "l3_total": len(l3_codes),
        "l3_stale": len(l3_codes) - len(metrics),
        "l1_context": l1_ctx,
        "top": metrics,
        "tomorrow_priority": tomorrow_priority,
        "tomorrow_downgrade": tomorrow_downgrade,
        "tomorrow_ban": tomorrow_ban,
        "persist": [m for m in metrics if m["stage"] == "持续主线"],
        "accel": [m for m in metrics if m["stage"] == "上升轮动"],
        "steady": [m for m in metrics if m["stage"] == "强势板块"],
        "fresh": [m for m in metrics if m["stage"] == "新晋活跃"],
        "fade": sorted([m for m in metrics if m["stage"] == "退潮"], key=lambda x: x["cumB"]),
    }
    if with_pv_shadow:
        result.update({
            "pv_shadow_version": PV_SHADOW_VERSION,
            "pv_shadow_activation": PV_SHADOW_ACTIVATION,
            "pv_shadow_status": "shadow_only_no_stable_incremental_alpha",
            "pv_as_of": win[-1],
            "pv_market_pct": pv_market_pct,
            "pv_market_count": pv_market_count,
            "pv_complete_count": len(pv_shadow),
            "pv_shadow_top": pv_shadow,
            "pv_shadow_all": pv_shadow_all,
            "pv_error": pv_error,
        })
    return result


def _print_section(title, items, n):
    print()
    print("▓" * 92)
    print(f"  {title}")
    print("▓" * 92)
    if not items:
        print("  （暂无）")
        return
    print(f"  {'#':>2s} {'三级行业':<12s}{'一级':<8s}{'二级':<10s}{'两周%':>7s}{'A周%':>7s}{'B周%':>7s}"
          f"{'前20%':>7s}{'A/B':>7s}{'量能':>6s} {'档':<6s}")
    print("-" * 92)
    for i, m in enumerate(items[:n], 1):
        print(f"  {i:>2d} {m['name'][:11]:<12s}{str(m['l1'])[:7]:<8s}{str(m['l2'])[:9]:<10s}"
              f"{m['cum10']:>6.1f}%{m['cumA']:>6.1f}%{m['cumB']:>6.1f}%"
              f"{m['top20_full']:>4d}/{m['window']:<2d}"
              f"{m['top20_A']:>3d}/{m['top20_B']:<2d}{m['amt_ratio']:>5.2f}x {_display_stage(m['stage']):<6s}")


def _print_tomorrow_plan(result, n):
    print()
    print("=" * 92)
    print("  \u660e\u65e5\u4e09\u7ea7\u884c\u4e1a\u9884\u6848\uff1a\u4f18\u5148 / \u964d\u6743 / \u7981\u5165")
    print("-" * 92)
    sections = [
        ("\u660e\u65e5\u4f18\u5148\u4e09\u7ea7\u884c\u4e1a", result.get("tomorrow_priority", []), n),
        ("\u964d\u6743\u884c\u4e1a", result.get("tomorrow_downgrade", []), min(n, 12)),
        ("\u7981\u5165\u884c\u4e1a", result.get("tomorrow_ban", []), min(n, 12)),
    ]
    for title, items, limit in sections:
        print(f"  {title}:")
        if not items:
            print("    \uff08\u6682\u65e0\uff09")
            continue
        for i, m in enumerate(items[:limit], 1):
            print(
                f"    {i:>2d}. {m['name'][:12]:<13s}"
                f"[{_display_stage(m.get('stage', '-')):<6s}] "
                f"\u4e24\u5468{m['cum10']:+5.1f}% B\u5468{m['cumB']:+5.1f}% "
                f"\u524d20%={m['top20_full']}/{m['window']} "
                f"\u91cf\u80fd{m['amt_ratio']:.2f}x  "
                f"{_display_rotation_text(m.get('tomorrow_reason', ''))}"
            )
    print("=" * 92)


def _print_leaders(strong, today, win_start, win_end, n_industries=12, per=5):
    print()
    print("=" * 92)
    print(f"  ★ 走强三级 · 龙头股（★主板可买 ○买不了; 两周={win_start}~{win_end}  今={today or '-'}）")
    print("-" * 92)
    shown = 0
    for m in strong:
        leaders = m.get("leaders") or []
        if not leaders:
            continue
        mb = [x for x in leaders if x["mb"]]
        tag = "" if mb else "  ⚠️无主板可买龙头"
        cells = []
        for item in leaders[:per]:
            mark = "★" if item["mb"] else "○"
            today_txt = f"{item['today']:+.0f}%" if item["today"] == item["today"] else "-"
            cells.append(f"{mark}{item['name'][:5]}(两周{item['chg2w']:+.0f}% 今{today_txt})")
        print(f"  【{m['name'][:10]}·{_display_stage(m['stage'])}】{tag}")
        print("      " + "  ".join(cells))
        shown += 1
        if shown >= n_industries:
            break
    if shown == 0:
        print("  （成分桥为空或暂无可计算个股；可加 --no-leaders 只看行业轮动）")
    print("=" * 92)


def _print_pv_shadow(result, n):
    """Append the price-volume shadow table without altering any formal section."""
    line_width = 142
    print()
    print("=" * line_width)
    print("  量价状态影子榜（历史暂未证明隔日增益；不改变正式行业档位、明日预案、排序或仓位）")
    print("-" * line_width)
    if result.get("pv_error"):
        print(f"  影子层计算失败，正式结果不受影响：{result['pv_error']}")
        print("=" * line_width)
        return
    items = result.get("pv_shadow_top") or []
    market = result.get("pv_market_pct")
    market_text = f"{market:+.2f}%" if market is not None else "缺失"
    print(
        f"  日期: {result.get('pv_as_of', '-')}  三指数等权: {market_text}  "
        f"完整覆盖: {result.get('pv_complete_count', 0)}/{result.get('l3_count', 0)}"
    )
    if not items:
        print("  （暂无完整量价样本）")
        print("=" * line_width)
        return
    print(
        f"  {'影排':>4s} {'原排':>4s} {'三级行业':<13s} {'置信':>4s} {'PV':>3s} {'原分':>6s} {'增强':>6s} "
        f"{'今涨':>7s} {'量/20日':>8s} {'上涨宽度':>10s} {'收位':>7s} {'超大盘':>8s} {'前三集中':>9s} {'3日确认':>8s} {'状态':<18s}"
    )
    print("-" * line_width)
    for item in items[:n]:
        shadow = item["shadow"]
        breadth = shadow.get("breadth")
        close_pos = shadow.get("close_position")
        concentration = shadow.get("top3_concentration")
        concentration_text = f"{concentration * 100:>8.0f}%" if concentration is not None else f"{'--':>9s}"
        confidence_text = "正常" if shadow.get("confidence") == "normal" else "低"
        signals_text = "、".join(shadow.get("signals") or []) or "-"
        print(
            f"  {shadow['enhanced_rank']:>4d} {shadow['formal_rank']:>4d} "
            f"{item['name'][:12]:<13s} {confidence_text:>4s} {shadow['score']:>+3d} "
            f"{item['score']:>6.1f} {shadow['enhanced_score']:>6.1f} "
            f"{shadow['today_pct']:>+6.2f}% {shadow['amount_ratio20']:>7.2f}x "
            f"{shadow['up_count']:>3d}/{shadow['active_count']:<3d}"
            f"({breadth * 100:>4.0f}%) {close_pos * 100:>6.0f}% "
            f"{shadow['excess_pct']:>+7.2f}% "
            f"{concentration_text} {shadow['confirm_streak_3d']:>5d}/3 {signals_text[:18]:<18s}"
        )
    print("-" * line_width)
    print("  PV=-6~+12：当日量价、上涨宽度、收盘位置、相对大盘、前三贡献集中度、3日连续确认各-1~+2。")
    print("  置信=低：有效成分不足5只，宽度与集中度按中性计分，不因小样本获得奖惩。")
    print("  前三集中度为 max(个股涨幅,0)×个股成交额的代理值；当前成分映射用于历史检验时存在成分幸存偏差。")
    print("  98日近似walk-forward未发现稳定增量收益，当前只作量价状态/过热风险解释，禁止据此加仓。")
    print("=" * line_width)


def main():
    parser = argparse.ArgumentParser(description="东方财富三级行业两周轮动分析")
    parser.add_argument("--window", type=int, default=10, help="轮动窗口交易日数，默认10")
    parser.add_argument("--lookback", type=int, default=45, help="读取最近多少自然日日K，默认45")
    parser.add_argument("--top", type=int, default=18, help="各榜单最多输出多少个")
    parser.add_argument("--as-of", default=None, help="只分析到指定日期 YYYY-MM-DD")
    parser.add_argument("--no-leaders", action="store_true", help="不展示主板龙头")
    parser.add_argument(
        "--no-pv-shadow",
        action="store_true",
        help="关闭量价增强影子榜（API导入默认关闭，命令行默认展示）",
    )
    args = parser.parse_args()

    result = analyze(
        window=args.window,
        lookback=args.lookback,
        as_of=args.as_of,
        with_leaders=not args.no_leaders,
        with_pv_shadow=not args.no_pv_shadow,
    )
    if "error" in result:
        print(result["error"])
        return 1

    print("=" * 92)
    print("  东方财富三级行业 · 两周轮动雷达")
    print("=" * 92)
    print(f"  窗口: {result['win_start']} ~ {result['win_end']}  ({result['window']}个交易日)  "
          f"参与三级行业: {result['l3_count']}/{result['l3_total']}")
    if result["l1_context"]:
        top_l1 = "、".join(f"{x['name']}{x['cum']:+.1f}%" for x in result["l1_context"][:6])
        bot_l1 = "、".join(f"{x['name']}{x['cum']:+.1f}%" for x in result["l1_context"][-4:])
        print(f"  一级强→弱: {top_l1}  ...  垫底: {bot_l1}")

    _print_tomorrow_plan(result, args.top)

    _print_section("① 持续主线（两周多数日前20%，主线级别）", result["persist"], args.top)
    _print_section("② 上升轮动 / 接力走强（后一周排名跃升，资金正在切入）", result["accel"], args.top)
    _print_section("③ 强势板块（累计涨幅居前、多数日收红，温和走强）", result["steady"], min(args.top, 12))
    _print_section("④ 退潮 / 走弱（前强后弱，减仓回避方向）", result["fade"], min(args.top, 12))

    print()
    print("=" * 92)
    print("  ★ 两周『轮动走强』综合总榜 TOP20  (供选股定方向)")
    print("-" * 92)
    strong = [m for m in result["top"] if m["stage"] in STRONG_STAGES]
    for i, m in enumerate(strong[:20], 1):
        print(f"  {i:>2d}. {m['name'][:12]:<13s}[{_display_stage(m['stage']):<6s}] "
              f"一级:{str(m['l1'])[:6]:<7s} 二级:{str(m['l2'])[:8]:<9s} "
              f"两周{m['cum10']:+5.1f}% B周{m['cumB']:+5.1f}% "
              f"前20%={m['top20_full']}/{m['window']}天 量能{m['amt_ratio']:.2f}x")
    print("=" * 92)

    if not args.no_leaders:
        _print_leaders(strong, result.get("stock_today"), result["win_start"], result["win_end"])

    print("  用法: 先看①②定主线和接力方向，再回到候选股表看同三级行业内的主板前排。")
    print("=" * 92)
    if not args.no_pv_shadow:
        _print_pv_shadow(result, min(args.top, 20))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
