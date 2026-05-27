#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘指数 + 行业板块 数据采集脚本

功能：
  1. 从baostock拉取行业分类映射 → stock_industry表
  2. 从baostock增量更新3大指数日K → kline_daily表
  3. 从kline_daily中已有个股数据 + 行业映射，聚合计算板块每日统计 → sector_daily表

用法：
  python _fetch_market_data.py               # 增量更新（只补最新数据）
  python _fetch_market_data.py --full        # 全量重建（重算所有板块日数据）
  python _fetch_market_data.py --from 2026-01-01  # 从指定日期开始重算板块数据
"""

import sys, os, warnings, argparse
from pathlib import Path
from datetime import datetime, timedelta
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import (
    get_connection, init_db,
    upsert_kline_batch, get_last_date,
    upsert_industry, get_industry_map,
    upsert_sector_daily
)
from sina_kline import fetch_kline, bs_to_sina
import pandas as pd
import numpy as np

init_db()

TODAY = datetime.now().strftime('%Y-%m-%d')

# ════════════════════════════════════════════
# Step 1: 更新行业分类映射
# ════════════════════════════════════════════
def fetch_industry_map():
    """行业分类映射——尝试通过 baostock 获取，不可用时保留现有DB数据"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            print(f"  baostock 不可用 ({lg.error_msg})，使用DB现有行业映射")
            return len(get_industry_map())

        rs = bs.query_stock_industry()
        rows = []
        while rs.next():
            r = rs.get_row_data()
            if r[1] and r[3]:
                rows.append((r[1], r[2], r[3], r[4], r[0]))

        bs.logout()
        print(f"  行业分类: 获取 {len(rows)} 条映射")

        if rows:
            n = upsert_industry(rows)
            print(f"  写入数据库: {n} 条")

            industries = {}
            for r in rows:
                ind = r[2]
                industries[ind] = industries.get(ind, 0) + 1
            print(f"  共 {len(industries)} 个行业, TOP10:")
            for ind, cnt in sorted(industries.items(), key=lambda x: -x[1])[:10]:
                print(f"    {ind}: {cnt}只")

        return len(rows)

    except Exception as e:
        print(f"  行业分类获取失败: {e}，使用DB现有数据")
        return len(get_industry_map())


# ════════════════════════════════════════════
# Step 2: 更新大盘指数K线
# ════════════════════════════════════════════
INDEX_CODES = {
    'sh.000001': '上证指数',
    'sz.399001': '深证成指',
    'sz.399006': '创业板指',
}


def fetch_index_klines():
    """增量更新3大指数日K线（新浪接口）"""
    total_new = 0
    for code, name in INDEX_CODES.items():
        last = get_last_date(code)
        if last and last >= TODAY:
            print(f"  {name}({code}): 已是最新 (最后日期 {last})")
            continue

        days_needed = 10 if last else 365
        kdf = fetch_kline(code, days=days_needed)
        if kdf.empty:
            print(f"  {name}({code}): 无数据")
            continue

        if last:
            kdf = kdf[kdf['date'] > last]
        if kdf.empty:
            print(f"  {name}({code}): 无新数据")
            continue

        n = upsert_kline_batch(code, kdf)
        print(f"  {name}({code}): +{n}条 (最新 {kdf['date'].max()})")
        total_new += n

    return total_new


# ════════════════════════════════════════════
# Step 3: 从个股数据聚合计算板块每日统计
# ════════════════════════════════════════════
def compute_sector_daily(start_date=None):
    """
    从kline_daily + stock_industry聚合计算板块每日统计。
    start_date: 从哪天开始计算（None则增量：只算缺失的日期）
    """
    conn = get_connection()

    # 获取行业映射
    ind_map = get_industry_map()
    if not ind_map:
        print("  错误: 行业映射为空，请先运行 Step1")
        conn.close()
        return 0

    print(f"  行业映射: {len(ind_map)} 只股票 → {len(set(ind_map.values()))} 个行业")

    # 确定计算日期范围
    if start_date is None:
        # 增量模式：找sector_daily中最新日期，从其后一天开始
        cursor = conn.execute("SELECT MAX(date) FROM sector_daily")
        last = cursor.fetchone()[0]
        if last:
            start_date = (datetime.strptime(last, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            start_date = '2025-01-01'

    print(f"  计算范围: {start_date} ~ {TODAY}")

    # 读取对应日期范围的所有个股K线数据
    sql = """
        SELECT k.code, k.date, k.close, k.volume, k.amount, k.turn, k.pctChg
        FROM kline_daily k
        WHERE k.date >= ? AND k.date <= ?
          AND k.code NOT LIKE 'sh.000%'
          AND k.code NOT LIKE 'sz.399%'
    """
    df = pd.read_sql_query(sql, conn, params=[start_date, TODAY])
    conn.close()

    if df.empty:
        print("  无个股K线数据")
        return 0

    for col in ['close', 'volume', 'amount', 'turn', 'pctChg']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # 添加行业列
    df['industry'] = df['code'].map(ind_map)
    df = df.dropna(subset=['industry', 'pctChg'])

    print(f"  有效数据: {len(df)} 条 ({df['date'].nunique()} 个交易日)")

    # 按 (行业, 日期) 分组聚合
    rows_to_insert = []
    for (industry, date), grp in df.groupby(['industry', 'date']):
        pcts = grp['pctChg'].values
        avg_pct = float(np.mean(pcts))
        up_count = int(np.sum(pcts > 0))
        down_count = int(np.sum(pcts < 0))
        flat_count = int(np.sum(pcts == 0))
        total_amount = float(grp['amount'].sum()) if grp['amount'].notna().any() else 0
        avg_turn = float(grp['turn'].mean()) if grp['turn'].notna().any() else 0
        stock_count = len(grp)

        # 领涨股
        best_idx = grp['pctChg'].idxmax()
        top_gainer = grp.loc[best_idx, 'code']
        top_gainer_pct = float(grp.loc[best_idx, 'pctChg'])

        rows_to_insert.append((
            industry, date, avg_pct, up_count, down_count, flat_count,
            total_amount, avg_turn, top_gainer, top_gainer_pct, stock_count
        ))

    if rows_to_insert:
        n = upsert_sector_daily(rows_to_insert)
        dates = sorted(set(r[1] for r in rows_to_insert))
        industries = sorted(set(r[0] for r in rows_to_insert))
        print(f"  写入: {n} 条 ({len(dates)} 个交易日 × {len(industries)} 个行业)")
    else:
        n = 0
        print("  无新数据需要写入")

    return n


# ════════════════════════════════════════════
# Step 4: 数据验证与摘要报告
# ════════════════════════════════════════════
def print_summary():
    """打印数据概览"""
    conn = get_connection()

    # 指数数据概览
    print("\n" + "═" * 60)
    print("  📊 数据概览")
    print("═" * 60)

    for code, name in INDEX_CODES.items():
        cursor = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM kline_daily WHERE code = ?",
            (code,)
        )
        r = cursor.fetchone()
        print(f"  {name}: {r[0]} ~ {r[1]} ({r[2]}条)")

    # 行业映射概览
    cursor = conn.execute("SELECT COUNT(*), COUNT(DISTINCT industry) FROM stock_industry")
    r = cursor.fetchone()
    print(f"  行业映射: {r[0]}只股票 → {r[1]}个行业")

    # 板块统计概览
    cursor = conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*), COUNT(DISTINCT industry), COUNT(DISTINCT date) FROM sector_daily"
    )
    r = cursor.fetchone()
    if r[0]:
        print(f"  板块统计: {r[0]} ~ {r[1]} ({r[4]}个交易日 × {r[3]}个行业 = {r[2]}条)")
    else:
        print("  板块统计: 无数据")

    # 最近一天的板块排名
    cursor = conn.execute("SELECT MAX(date) FROM sector_daily")
    latest = cursor.fetchone()[0]
    if latest:
        print(f"\n  📈 最新交易日({latest})板块涨幅TOP10:")
        df = pd.read_sql_query(
            "SELECT industry, avg_pct, up_count, down_count, stock_count, top_gainer, top_gainer_pct "
            "FROM sector_daily WHERE date = ? ORDER BY avg_pct DESC LIMIT 10",
            conn, params=[latest]
        )
        for _, row in df.iterrows():
            ratio = f"{row['up_count']}/{row['down_count']}"
            print(f"    {row['industry']:<30s} {row['avg_pct']:>+6.2f}%  "
                  f"涨/跌={ratio:<6s} ({row['stock_count']}只)  "
                  f"领涨:{row['top_gainer']} {row['top_gainer_pct']:>+5.1f}%")

        print(f"\n  📉 最新交易日({latest})板块跌幅TOP5:")
        df2 = pd.read_sql_query(
            "SELECT industry, avg_pct, up_count, down_count, stock_count "
            "FROM sector_daily WHERE date = ? ORDER BY avg_pct ASC LIMIT 5",
            conn, params=[latest]
        )
        for _, row in df2.iterrows():
            ratio = f"{row['up_count']}/{row['down_count']}"
            print(f"    {row['industry']:<30s} {row['avg_pct']:>+6.2f}%  "
                  f"涨/跌={ratio:<6s} ({row['stock_count']}只)")

    conn.close()


# ════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='大盘指数+行业板块数据采集')
    parser.add_argument('--full', action='store_true', help='全量重建板块统计')
    parser.add_argument('--from', dest='from_date', type=str, default=None,
                        help='从指定日期开始重算板块数据 (YYYY-MM-DD)')
    parser.add_argument('--skip-index', action='store_true', help='跳过指数更新')
    parser.add_argument('--skip-industry', action='store_true', help='跳过行业分类更新')
    args = parser.parse_args()

    print("═" * 60)
    print("  大盘指数 + 行业板块 数据采集")
    print(f"  日期: {TODAY}")
    print("═" * 60)

    # Step 1: 行业分类
    if not args.skip_industry:
        print("\n📋 Step 1: 更新行业分类映射...")
        fetch_industry_map()
    else:
        print("\n📋 Step 1: 跳过行业分类更新")

    # Step 2: 指数K线
    if not args.skip_index:
        print("\n📊 Step 2: 更新大盘指数K线...")
        fetch_index_klines()
    else:
        print("\n📊 Step 2: 跳过指数更新")

    # Step 3: 板块统计
    print("\n📈 Step 3: 计算行业板块每日统计...")
    if args.full:
        compute_sector_daily(start_date='2025-01-01')
    elif args.from_date:
        compute_sector_daily(start_date=args.from_date)
    else:
        compute_sector_daily()  # 增量

    # 摘要
    print_summary()

    print("\n✅ 数据采集完成！")
