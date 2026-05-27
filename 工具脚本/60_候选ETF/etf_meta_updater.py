#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF meta 字段自动刷新脚本。

自动维护的字段（抓不到时保留 CSV/库内现值）：
  - fund_size_yi       规模(亿)         源: fund_etf_spot_em / fund_etf_scale_sse+净值
  - tracking_error_60d 60日跟踪误差(年化%) 源: 本地 etf_kline_daily + index_zh_a_hist
  - valuation_pct_5y   5年PE分位         源: stock_index_pe_lg（仅限白名单指数）

不自动维护（沿用 CSV/库内）：
  - fee_rate, dividend_yield, payout_ratio  （akshare 雪球详情接口当前无法稳定调用）
  - style_bucket / style_corr_group / notes / track_index  （人工维护）

工作流：
  1. 从 etf_meta.csv 读入人工字段（首次同步到库）
  2. 调用各 fetcher，拿到能抓的数值
  3. partial upsert 到 etf_meta 表（COALESCE 防止 NULL 覆盖原值）
  4. 可选 --write-csv 把库内结果写回 CSV

用法：
  python3 工具脚本/60_候选ETF/etf_meta_updater.py                  # 刷库（不动 CSV）
  python3 工具脚本/60_候选ETF/etf_meta_updater.py --write-csv      # 刷库并同步回 CSV
  python3 工具脚本/60_候选ETF/etf_meta_updater.py --bootstrap      # 首次把 CSV 全量灌库
  python3 工具脚本/60_候选ETF/etf_meta_updater.py --codes 510880   # 仅刷指定
  python3 工具脚本/60_候选ETF/etf_meta_updater.py --dry-run        # 只打印不入库
"""
from __future__ import annotations

import argparse
import sys
import time
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
_SELF_DIR = Path(__file__).resolve().parent
if str(_SELF_DIR) not in sys.path:
    sys.path.insert(0, str(_SELF_DIR))

import akshare as ak
import numpy as np
import pandas as pd

import db_cache  # type: ignore

CSV_PATH = _SELF_DIR / "etf_meta.csv"

# 跟踪指数 → akshare index_zh_a_hist 可用代码（用于跟踪误差）
INDEX_HIST_CODE = {
    "沪深300": "000300",
    "中证A500": "000510",
    "中证500": "000905",
    "创业板指": "399006",
    "科创50": "000688",
    "中证红利": "000922",
    "中证红利低波动100": "930955",
    "中证医疗": "399989",
    "中证新能源汽车": "399976",
    "中证军工": "399967",
    # 以下指数 akshare 当前无可靠日 K 接口或非 A 股，跳过：
    # 标普中国A股红利机会 / 上证10年期国债 / 上证城投债 / SGE黄金9999
    # 纳斯达克100 / 标普500 / 中华半导体芯片
}

# 跟踪指数 → 乐咕乐股 stock_index_pe_lg 支持的 symbol（仅有限指数有效）
INDEX_PE_LG_SYMBOL = {
    "沪深300": "沪深300",
    "中证500": "中证500",
    "中证1000": "中证1000",
    "中证100": "中证100",
    "中证800": "中证800",
    "上证50": "上证50",
    "上证180": "上证180",
    "上证380": "上证380",
    "深证100": "深证100",
    "创业板50": "创业板50",
    "中证红利": "深证红利",   # 注：legulegu 只有"上证红利/深证红利"，作近似代理
    "上证红利": "上证红利",
}


def _retry(fn, *args, retries=3, sleep=1.5, label="?", **kwargs):
    """通用重试包装：捕获 ProxyError/网络异常重试 retries 次。"""
    last = None
    for i in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last = exc
            kind = type(exc).__name__
            if i < retries:
                time.sleep(sleep * i)
                continue
            print(f"  ! {label} 重试 {retries} 次仍失败: {kind}: {str(exc)[:120]}")
    if last:
        raise last


def _load_csv_meta() -> pd.DataFrame:
    if not CSV_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(CSV_PATH, dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


# ─────────────────────────── 抓取函数 ───────────────────────────

def fetch_fund_sizes(codes: list[str]) -> dict[str, float]:
    """返回 {code: 规模(亿)}。优先 fund_etf_spot_em，失败时用份额×净值兜底。"""
    out: dict[str, float] = {}

    # 主路径：fund_etf_spot_em 全市场
    try:
        df = _retry(ak.fund_etf_spot_em, retries=3, sleep=2.0, label="fund_etf_spot_em")
    except Exception:
        df = pd.DataFrame()

    if not df.empty:
        # 列名因接口版本而异；自动匹配
        code_col = "代码" if "代码" in df.columns else df.columns[0]
        size_col = None
        for cand in ("总市值", "流通市值", "基金规模", "规模"):
            if cand in df.columns:
                size_col = cand
                break
        if size_col is not None:
            for c in codes:
                row = df[df[code_col].astype(str) == c]
                if not row.empty:
                    v = row.iloc[0][size_col]
                    try:
                        fv = float(v)
                        if fv > 0:
                            # spot_em 的总市值/规模单位为"元"，转亿
                            out[c] = round(fv / 1e8, 2)
                    except (ValueError, TypeError):
                        pass

    missing = [c for c in codes if c not in out]
    if not missing:
        return out

    # 兜底路径：份额表 × 最新净值
    try:
        share_sse = _retry(ak.fund_etf_scale_sse, retries=2, sleep=1.5, label="fund_etf_scale_sse")
    except Exception:
        share_sse = pd.DataFrame()
    try:
        share_szse = _retry(ak.fund_etf_scale_szse, retries=2, sleep=1.5, label="fund_etf_scale_szse")
    except Exception:
        share_szse = pd.DataFrame()

    share_map: dict[str, float] = {}
    for share_df in (share_sse, share_szse):
        if share_df is None or share_df.empty:
            continue
        code_col = "基金代码" if "基金代码" in share_df.columns else share_df.columns[1]
        share_col = None
        for cand in ("基金份额", "上市份额", "流通份额", "份额"):
            if cand in share_df.columns:
                share_col = cand
                break
        if share_col is None:
            continue
        for _, row in share_df.iterrows():
            try:
                share_map[str(row[code_col]).zfill(6)] = float(row[share_col])
            except (ValueError, TypeError, KeyError):
                pass

    # 净值：从本地 DB 拿最近一条 close
    for c in missing:
        shares = share_map.get(c)
        if not shares:
            continue
        last_close = None
        kdf = db_cache.read_etf_kline(c)
        if not kdf.empty:
            last_close = float(kdf["close"].iloc[-1])
        if last_close is None or last_close <= 0:
            continue
        out[c] = round(shares * last_close / 1e8, 2)
    return out


def fetch_tracking_error_60d(code: str, track_index: str) -> float | None:
    """计算最近 60 交易日的跟踪误差（ETF 日收益 - 指数日收益 的年化标准差，%）。"""
    idx_code = INDEX_HIST_CODE.get(str(track_index).strip())
    if not idx_code:
        return None

    # ETF 收益
    etf_df = db_cache.read_etf_kline(code)
    if etf_df is None or etf_df.empty or len(etf_df) < 65:
        return None
    etf_df = etf_df.sort_values("date").tail(120).copy()
    etf_df["ret"] = etf_df["close"].pct_change()

    # 指数收益（拉最近 6 个月）
    end_d = datetime.today().strftime("%Y%m%d")
    start_d = (datetime.today() - timedelta(days=240)).strftime("%Y%m%d")
    try:
        idx_df = _retry(
            ak.index_zh_a_hist,
            symbol=idx_code, period="daily", start_date=start_d, end_date=end_d,
            retries=3, sleep=2.0, label=f"index_zh_a_hist({idx_code})",
        )
    except Exception:
        return None
    if idx_df is None or idx_df.empty:
        return None
    if "日期" in idx_df.columns:
        idx_df = idx_df.rename(columns={"日期": "date", "收盘": "close"})
    idx_df["date"] = idx_df["date"].astype(str).str[:10]
    idx_df = idx_df[["date", "close"]].sort_values("date").copy()
    idx_df["ret_idx"] = idx_df["close"].astype(float).pct_change()

    etf_df["date"] = etf_df["date"].astype(str).str[:10]
    merged = etf_df[["date", "ret"]].merge(idx_df[["date", "ret_idx"]], on="date", how="inner")
    merged = merged.dropna().tail(60)
    if len(merged) < 40:
        return None
    diff = merged["ret"] - merged["ret_idx"]
    te_annualized = float(diff.std(ddof=1) * np.sqrt(252) * 100)  # 百分比
    return round(te_annualized, 3)


def fetch_valuation_pct_5y(track_index: str) -> float | None:
    """返回跟踪指数最近 PE 在过去 5 年（1300 交易日）中的分位（0-100）。"""
    sym = INDEX_PE_LG_SYMBOL.get(str(track_index).strip())
    if not sym:
        return None
    try:
        df = _retry(
            ak.stock_index_pe_lg, symbol=sym,
            retries=3, sleep=2.0, label=f"stock_index_pe_lg({sym})",
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None
    # 列识别
    pe_col = None
    for cand in ("滚动市盈率", "静态市盈率", "市盈率", "pe", "PE"):
        if cand in df.columns:
            pe_col = cand
            break
    if pe_col is None:
        return None
    pe_series = pd.to_numeric(df[pe_col], errors="coerce").dropna()
    if pe_series.empty:
        return None
    window = pe_series.tail(1300)  # 5 年约 1250 交易日
    latest = float(window.iloc[-1])
    pct = float((window <= latest).sum()) / len(window) * 100
    return round(pct, 1)


# ─────────────────────────── 主流程 ───────────────────────────

def run_update(codes_filter: list[str] | None, write_csv: bool, dry_run: bool, bootstrap: bool) -> int:
    csv_df = _load_csv_meta()
    if csv_df.empty:
        print(f"  ! 找不到 {CSV_PATH}，无法运行")
        return 1

    if bootstrap:
        print(f"[+] bootstrap: 全量灌入 etf_meta（{len(csv_df)} 行）…")
        if not dry_run:
            rows = csv_df.to_dict("records")
            n = db_cache.upsert_etf_meta(rows, partial_update_cols=None)
            print(f"  ✓ 灌入 {n} 行")
        else:
            print(f"  (dry-run) 会灌入 {len(csv_df)} 行")

    if codes_filter:
        targets = csv_df[csv_df["code"].isin(codes_filter)].copy()
    else:
        targets = csv_df.copy()

    if targets.empty:
        print("  ! 无目标 ETF")
        return 1

    print(f"\n[+] 开始刷新 {len(targets)} 只 ETF 的可自动维护字段…\n")
    updates: list[dict] = []
    for i, row in targets.reset_index(drop=True).iterrows():
        code = row["code"]
        track_index = row.get("track_index", "")
        rec: dict = {"code": code}
        msg_parts: list[str] = []

        # 规模（统一一次性抓更高效，这里逐只抓也行；为简洁本脚本批量调一次）
        # 跟踪误差
        try:
            te = fetch_tracking_error_60d(code, track_index)
            if te is not None:
                rec["tracking_error_60d"] = te
                msg_parts.append(f"TE60={te}")
        except Exception as exc:
            msg_parts.append(f"TE60 FAIL({type(exc).__name__})")

        # 估值分位
        try:
            pct = fetch_valuation_pct_5y(track_index)
            if pct is not None:
                rec["valuation_pct_5y"] = pct
                msg_parts.append(f"PE_pct5y={pct}")
        except Exception as exc:
            msg_parts.append(f"PE FAIL({type(exc).__name__})")

        updates.append(rec)
        print(f"  [{i+1:>2}/{len(targets)}] {code} {row.get('name','')[:18]:<18}  {' / '.join(msg_parts) or '无更新'}")
        time.sleep(0.3)

    # 规模：批量一次性抓
    print("\n[+] 批量抓取 fund_size_yi …")
    try:
        size_map = fetch_fund_sizes([r["code"] for r in updates])
        for rec in updates:
            v = size_map.get(rec["code"])
            if v is not None and v > 0:
                rec["fund_size_yi"] = v
        ok_n = sum(1 for r in updates if r.get("fund_size_yi") is not None)
        print(f"  ✓ 抓到 {ok_n}/{len(updates)} 只 ETF 规模")
    except Exception as exc:
        print(f"  ! 规模抓取整体失败: {type(exc).__name__}: {exc}")

    # 入库（仅更新自动维护的 3 列）
    auto_cols = ["fund_size_yi", "tracking_error_60d", "valuation_pct_5y"]
    nonempty = [r for r in updates if any(k in r and r[k] is not None for k in auto_cols)]
    print(f"\n[+] 待入库 {len(nonempty)} 行（{len(updates)} 行中有自动字段更新的）")

    if dry_run:
        print("  (dry-run) 跳过入库")
    elif nonempty:
        n = db_cache.upsert_etf_meta(nonempty, partial_update_cols=auto_cols)
        print(f"  ✓ 已 partial-upsert {n} 行到 etf_meta 表")

    if write_csv and not dry_run:
        print(f"\n[+] 同步库内最新值回写 CSV: {CSV_PATH}")
        live = db_cache.read_etf_meta()
        if not live.empty:
            # 保留 CSV 原列顺序
            csv_cols = list(csv_df.columns)
            # 用库内值覆盖 csv_df 中能匹配到的列
            merged = csv_df.set_index("code").copy()
            live_idx = live.set_index("code")
            for col in csv_cols:
                if col == "code":
                    continue
                if col in live_idx.columns:
                    merged[col] = live_idx[col].combine_first(merged.get(col))
            merged = merged.reset_index()[csv_cols]
            merged.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
            print(f"  ✓ CSV 已更新（{len(merged)} 行）")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF meta 字段自动刷新")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔 ETF 代码；默认全部")
    ap.add_argument("--write-csv", action="store_true", help="同步把库内结果写回 etf_meta.csv")
    ap.add_argument("--dry-run", action="store_true", help="只打印不入库")
    ap.add_argument("--bootstrap", action="store_true", help="先将 etf_meta.csv 全量灌入 etf_meta 表")
    args = ap.parse_args()

    codes = None
    if args.codes.strip():
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]

    return run_update(codes, args.write_csv, args.dry_run, args.bootstrap)


if __name__ == "__main__":
    sys.exit(main())
