#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性脚本：拉取三大指数近一年日K数据写入数据库"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except:
    pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '工具脚本'))
from sina_kline import fetch_kline
from db_cache import upsert_kline, init_db

init_db()

indices = ['sh.000001', 'sz.399001', 'sz.399006']
names = ['上证指数', '深证成指', '创业板指']

for code, name in zip(indices, names):
    df = fetch_kline(code, days=365)
    # 只保留指定历史范围内的数据
    df = df[(df['date'] >= '2025-04-01') & (df['date'] <= '2026-04-13')]
    if not df.empty:
        upsert_kline(code, df)
        print(f'{name}({code}): 写入 {len(df)} 条, 最新日期 {df["date"].max()}')
    else:
        print(f'{name}({code}): 无数据!')

print('全部完成!')
