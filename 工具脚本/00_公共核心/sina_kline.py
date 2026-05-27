#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新浪日K线接口封装 —— 替代 baostock.query_history_k_data_plus

被 market_top_down / batch_fetch / cn_stock_analysis 等脚本复用。
不依赖 baostock，直接走新浪 HTTP 接口（无需代理）。

限制说明:
  - amount = volume × close（估算，非精确成交额；与 _update_cache_ak.py 保持一致）
  - turn   = 0.0（换手率新浪日K无此字段）
  - pctChg = 由相邻 close 推算（第一条为 0）
"""

import requests
import pandas as pd

_SINA_URL = (
    "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/CN_MarketData.getKLineData"
    "?symbol={code}&scale=240&ma=no&datalen={n}"
)


def bs_to_sina(bs_code: str) -> str:
    """格式转换: sh.600000 → sh600000,  sz.000001 → sz000001"""
    return bs_code.replace('.', '')


def fetch_kline(bs_code: str, days: int = 150) -> pd.DataFrame:
    """
    拉取日K线数据，返回 DataFrame，列顺序与 baostock 对齐:
        date, open, high, low, close, volume, amount, turn, pctChg

    Parameters
    ----------
    bs_code : str
        baostock 格式代码，如 sh.600000 / sz.000001 / sh.000001（指数）
        也接受无点格式 sh600000（自动兼容）
    days : int
        拉取最近多少个交易日，默认 150。新浪接口支持上限约 500。

    Returns
    -------
    pd.DataFrame  （失败时返回空 DataFrame）
    """
    sina_code = bs_to_sina(bs_code)
    # 多拉 1 条，用于计算第一条记录的 pctChg
    n = days + 1
    try:
        resp = requests.get(_SINA_URL.format(code=sina_code, n=n), timeout=15)
        raw = resp.json()
    except Exception:
        return pd.DataFrame()

    if not raw or not isinstance(raw, list):
        return pd.DataFrame()

    rows = []
    for k in raw:
        try:
            rows.append({
                'date':   k['day'][:10],
                'open':   float(k['open']),
                'high':   float(k['high']),
                'low':    float(k['low']),
                'close':  float(k['close']),
                'volume': float(k.get('volume', 0)),
            })
        except (KeyError, ValueError, TypeError):
            continue

    if not rows:
        return pd.DataFrame()

    rows.sort(key=lambda x: x['date'])

    # 补齐 amount / turn / pctChg
    for i, r in enumerate(rows):
        r['amount'] = r['volume'] * r['close']
        r['turn']   = 0.0
        if i > 0:
            prev = rows[i - 1]['close']
            r['pctChg'] = round((r['close'] / prev - 1) * 100, 4) if prev else 0.0
        else:
            r['pctChg'] = 0.0

    df = pd.DataFrame(rows, columns=[
        'date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg'
    ])
    # 只返回最近 days 天，丢弃用于计算基准的额外那一条
    return df.tail(days).reset_index(drop=True)
