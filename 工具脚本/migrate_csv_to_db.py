#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性脚本：将 数据缓存/*.csv 全部导入 SQLite 数据库。
导入完成后，原 CSV 文件保留不删除（可手动清理）。

用法:
    python migrate_csv_to_db.py          # 导入全部
    python migrate_csv_to_db.py --dry    # 只统计，不实际写入
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
import time
import pandas as pd

# 确保能导入同目录下的 db_cache
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_cache import init_db, upsert_kline_batch, get_row_count, DB_PATH

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '数据缓存')
DRY_RUN = '--dry' in sys.argv

KLINE_COLS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg']


def main():
    init_db()

    files = sorted([f for f in os.listdir(CACHE_DIR) if f.endswith('.csv')])
    print(f"数据缓存目录: {os.path.abspath(CACHE_DIR)}")
    print(f"数据库路径:    {os.path.abspath(DB_PATH)}")
    print(f"待导入 CSV 文件: {len(files)} 个")
    if DRY_RUN:
        print("(试运行模式，不实际写入)")
    print()

    success = 0
    failed = 0
    total_rows = 0
    t0 = time.time()

    for i, fname in enumerate(files):
        path = os.path.join(CACHE_DIR, fname)
        code = fname.replace('.csv', '').replace('_', '.')  # sh_600000 -> sh.600000

        try:
            df = pd.read_csv(path, dtype=str)
            # 确保列名匹配
            if 'date' not in df.columns:
                print(f"  [{i+1}] {fname} - 缺少 date 列，跳过")
                failed += 1
                continue

            # 只保留需要的列
            available_cols = [c for c in KLINE_COLS if c in df.columns]
            df = df[available_cols]

            if df.empty:
                failed += 1
                continue

            if not DRY_RUN:
                n = upsert_kline_batch(code, df)
                total_rows += n
            else:
                total_rows += len(df)

            success += 1

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                print(f"  进度: {i+1}/{len(files)} | 成功: {success} | 行数: {total_rows:,} | 耗时: {elapsed:.1f}s")

        except Exception as e:
            print(f"  [{i+1}] {fname} - 导入失败: {e}")
            failed += 1

    elapsed = time.time() - t0
    print()
    print("=" * 60)
    print(f"导入完成！")
    print(f"  成功: {success} 只股票")
    print(f"  失败: {failed}")
    print(f"  总行数: {total_rows:,}")
    print(f"  耗时: {elapsed:.1f}s")
    if not DRY_RUN:
        print(f"  数据库总记录: {get_row_count():,}")
        db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
        print(f"  数据库文件大小: {db_size:.1f} MB")
    print()
    print("CSV 文件已保留，确认无误后可手动删除 数据缓存/*.csv")


if __name__ == '__main__':
    main()
