#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用新浪K线HTTP接口补全数据库中所有已缓存股票的最新K线。
baostock 收盘后数据更新慢，新浪接口通常15:30后即可用。

用法: python _update_cache_ak.py
"""
import sys, os, time, warnings, subprocess
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')
os.environ.setdefault('NO_PROXY', '*')
os.environ.setdefault('no_proxy', '*')

import requests
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import get_all_codes, get_last_date, upsert_kline, upsert_kline_batch, DB_PATH, init_db, get_connection, get_industry_map, upsert_sector_daily

init_db()

TODAY = datetime.now().strftime('%Y-%m-%d')

print("=" * 70)
print(f"  新浪K线 数据补全工具  目标日期: {TODAY}")
print(f"  数据库: {os.path.abspath(DB_PATH)}")
print("=" * 70)

# ═══ 新浪K线接口 ═══
def fetch_kline_sina(sina_code, days=10):
    """
    新浪日K线接口 (HTTP, 不走HTTPS代理)
    sina_code: sh600000 / sz000001 / sh000001(指数)
    返回: list of dict  或 None
    """
    try:
        url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={days}")
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if not data or not isinstance(data, list):
            return None
        result = []
        for k in data:
            close_val = float(k['close'])
            vol_val = float(k['volume']) if k.get('volume') else 0
            result.append({
                'date': k['day'][:10],  # 取YYYY-MM-DD部分
                'open': float(k['open']),
                'high': float(k['high']),
                'low': float(k['low']),
                'close': close_val,
                'volume': vol_val,
                # 新浪接口无 amount/turn，用 volume×close 估算成交额（元）
                # turn 留 None，由 fetch_one 从历史数据补填
                '_amount_est': vol_val * close_val,
            })
        return result
    except Exception:
        return None


def bs_to_sina(bs_code):
    """sh.600000 → sh600000, sz.000001 → sz000001"""
    return bs_code.replace('.', '')


# ═══ Step 0: 刷新股票池（补主板/创业板/科创板新股，删退市）═══
print("\n🔄 Step 0: 刷新股票池（补新股/删退市）...")
try:
    universe_script = Path(__file__).resolve().parent / "_refresh_stock_universe.py"
    if universe_script.exists():
        result = subprocess.run(
            [sys.executable, str(universe_script), "--apply", "--new-days", "300", "--sleep", "0.12"],
            check=False,
            timeout=1800,
        )
        if result.returncode == 0:
            print("  股票池刷新完成 ✅")
        else:
            print(f"  股票池刷新未完全成功，返回码 {result.returncode}（不影响后续K线更新）")
    else:
        print(f"  未找到股票池刷新脚本: {universe_script}")
except subprocess.TimeoutExpired:
    print("  股票池刷新超时（不影响K线数据），可稍后单独运行 _refresh_stock_universe.py --apply")
except Exception as e:
    print(f"  股票池刷新失败: {e}（不影响后续K线更新）")


# ═══ Step 1: 更新三大指数 ═══
print("\n📊 Step 1: 更新三大指数...")
INDEX_CODES = {
    'sh.000001': '上证指数',
    'sz.399001': '深证成指',
    'sz.399006': '创业板指',
}

for bs_code, name in INDEX_CODES.items():
    last = get_last_date(bs_code)
    if last and last >= TODAY:
        print(f"  {name}({bs_code}): 已是最新 ({last})")
        continue

    sina_code = bs_to_sina(bs_code)
    klines = fetch_kline_sina(sina_code, days=10)
    if not klines:
        print(f"  {name}: 无数据")
        continue

    # 只保留比 last_date 新的
    new_klines = [k for k in klines if k['date'] > (last or '2000-01-01')]
    if not new_klines:
        print(f"  {name}: 无新数据 (最新={klines[-1]['date']})")
        continue

    # 计算涨跌幅: 先拿前一天的收盘价
    all_sorted = sorted(klines, key=lambda x: x['date'])
    for j, k in enumerate(new_klines):
        # 找到前一天的close
        prev_close = None
        idx_in_all = next((i for i, a in enumerate(all_sorted) if a['date'] == k['date']), None)
        if idx_in_all and idx_in_all > 0:
            prev_close = all_sorted[idx_in_all - 1]['close']
        k['pctChg'] = round((k['close'] / prev_close - 1) * 100, 4) if prev_close else 0
        k['amount'] = k.pop('_amount_est', 0)  # 指数用 volume×close，选股无关但保持一致
        k['turn'] = 0  # 指数无换手率

    df = pd.DataFrame(new_klines)
    upsert_kline(bs_code, df)
    print(f"  {name}: +{len(new_klines)} 条 → 最新 {new_klines[-1]['date']}")

# ═══ Step 2: 更新所有个股 ═══
print("\n📈 Step 2: 更新个股K线...")
codes = get_all_codes()
stock_codes = [c for c in codes if c not in INDEX_CODES]

# 先筛出需要更新的
need_update = []
for bs_code in stock_codes:
    last = get_last_date(bs_code)
    if last and last >= TODAY:
        continue
    if not last:
        continue
    need_update.append((bs_code, last))

print(f"  共 {len(stock_codes)} 只个股, {len(need_update)} 只需要更新")

updated = 0
failed = 0
no_data = 0


def fetch_one(args):
    """拉取单只股票的新浪K线"""
    bs_code, last_date = args
    sina_code = bs_to_sina(bs_code)
    klines = fetch_kline_sina(sina_code, days=10)
    if not klines:
        return (bs_code, None, 'no_kline')
    new_klines = [k for k in klines if k['date'] > last_date]
    if not new_klines:
        return (bs_code, None, 'no_new')

    # 算涨跌幅
    all_sorted = sorted(klines, key=lambda x: x['date'])
    for k in new_klines:
        prev_close = None
        idx_in_all = next((i for i, a in enumerate(all_sorted) if a['date'] == k['date']), None)
        if idx_in_all and idx_in_all > 0:
            prev_close = all_sorted[idx_in_all - 1]['close']
        k['pctChg'] = round((k['close'] / prev_close - 1) * 100, 4) if prev_close else 0
        # amount: 优先用接口字段，新浪接口无此字段则用 volume×close 估算
        k['amount'] = k.pop('_amount_est', k['volume'] * k['close'])

    # 补填换手率：取 DB 中该股近30日有效换手率均值
    _turn_conn = None
    try:
        from db_cache import get_connection as _get_conn
        _turn_conn = _get_conn()
        _turn_rows = _turn_conn.execute(
            "SELECT turn FROM kline_daily WHERE code=? AND turn>0 ORDER BY date DESC LIMIT 30",
            (bs_code,)
        ).fetchall()
        _turn_avg = round(sum(r[0] for r in _turn_rows) / len(_turn_rows), 4) if _turn_rows else 1.0
    except Exception:
        _turn_avg = 1.0
    finally:
        if _turn_conn is not None:
            try:
                _turn_conn.close()
            except Exception:
                pass
    for k in new_klines:
        k['turn'] = _turn_avg

    return (bs_code, new_klines, 'ok')


# 多线程并发拉取（新浪HTTP接口允许并发）
WORKERS = 20
batch_results = []

print(f"  使用 {WORKERS} 线程并发拉取...")
t0 = time.time()

with ThreadPoolExecutor(max_workers=WORKERS) as executor:
    futures = {executor.submit(fetch_one, args): args for args in need_update}
    done_count = 0
    for future in as_completed(futures):
        done_count += 1
        try:
            bs_code, new_klines, status = future.result()
            if status == 'ok' and new_klines:
                batch_results.append((bs_code, new_klines))
            elif status == 'no_kline':
                failed += 1
            else:
                no_data += 1
        except Exception:
            failed += 1

        if done_count % 500 == 0:
            elapsed = time.time() - t0
            print(f"  拉取进度: {done_count}/{len(need_update)} ({elapsed:.0f}s) 成功:{len(batch_results)} 失败:{failed}")

elapsed = time.time() - t0
print(f"  拉取完成: {len(batch_results)} 只有新数据 ({elapsed:.1f}s)")

# 批量写入数据库
print(f"  写入数据库...")
for bs_code, new_klines in batch_results:
    try:
        df = pd.DataFrame(new_klines)
        upsert_kline(bs_code, df)
        updated += 1
    except Exception:
        failed += 1

# ═══ Step 3: 重算板块统计 ═══
print(f"\n📊 Step 3: 重算板块统计...")
try:
    industry_map = get_industry_map()
    conn = get_connection()

    new_dates_cursor = conn.execute(
        "SELECT DISTINCT date FROM kline_daily WHERE date > '2026-04-17' ORDER BY date"
    )
    new_dates = [r[0] for r in new_dates_cursor.fetchall()]

    if new_dates:
        print(f"  需要计算的新日期: {new_dates}")
        sector_rows = []
        for dt in new_dates:
            df_day = pd.read_sql_query(
                "SELECT code, close, volume, amount, turn, pctChg FROM kline_daily WHERE date = ?",
                conn, params=[dt]
            )
            if df_day.empty:
                continue
            df_day['industry'] = df_day['code'].map(industry_map)
            df_day = df_day.dropna(subset=['industry'])

            for ind, grp in df_day.groupby('industry'):
                pcts = grp['pctChg'].dropna()
                up = int((pcts > 0).sum())
                down = int((pcts < 0).sum())
                flat = int((pcts == 0).sum())
                avg_pct = round(float(pcts.mean()), 4) if len(pcts) > 0 else 0
                total_amt = float(grp['amount'].sum())
                avg_turn = round(float(grp['turn'].mean()), 4) if 'turn' in grp.columns else 0
                top_idx = pcts.idxmax() if len(pcts) > 0 else None
                top_gainer = grp.loc[top_idx, 'code'] if top_idx is not None else ''
                top_gainer_pct = round(float(pcts.max()), 2) if len(pcts) > 0 else 0
                stock_count = len(grp)

                sector_rows.append((
                    ind, dt, avg_pct, up, down, flat,
                    total_amt, avg_turn, top_gainer, top_gainer_pct, stock_count
                ))

        if sector_rows:
            upsert_sector_daily(sector_rows)
            print(f"  板块统计: 写入 {len(sector_rows)} 条 ({len(new_dates)} 个交易日)")
        else:
            print(f"  板块统计: 无新数据可算")
    else:
        print(f"  板块统计: 无新日期需要计算")
    conn.close()
except Exception as e:
    print(f"  板块统计计算失败: {e}")

# ═══ 汇总 ═══
# ═══ Step 4: 补算 pctChg（兜底，防止新浪接口因无前日数据而漏算）═══
print(f"\n🔢 Step 4: 补算缺失 pctChg...")
try:
    conn4 = get_connection()
    rows_need_fix = conn4.execute(
        "SELECT code, date, close FROM kline_daily WHERE date = ? AND (pctChg IS NULL OR pctChg = 0) AND close > 0",
        (TODAY,)
    ).fetchall()
    updates = []
    for code, date_val, close_val in rows_need_fix:
        prev_row = conn4.execute(
            "SELECT close FROM kline_daily WHERE code = ? AND date < ? AND close > 0 ORDER BY date DESC LIMIT 1",
            (code, date_val)
        ).fetchone()
        if prev_row:
            prev_close = prev_row[0]
            pct = round((close_val / prev_close - 1) * 100, 4)
            if pct != 0:
                updates.append((pct, code, date_val))
    if updates:
        conn4.executemany(
            "UPDATE kline_daily SET pctChg = ? WHERE code = ? AND date = ?",
            updates
        )
        conn4.commit()
        print(f"  共补算 {len(updates)} 条 pctChg ✅")
    else:
        print(f"  pctChg 无需补算 ✅")
    conn4.close()
except Exception as e:
    print(f"  pctChg 补算失败: {e}")

# ═══ Step 5: 更新概念板块数据 ═══
print(f"\n🧭 Step 5: 更新概念板块数据...")
try:
    concept_script = Path(__file__).resolve().parent / "_fetch_concept_data.py"
    if concept_script.exists():
        result = subprocess.run(
            [sys.executable, str(concept_script), "--days", "30"],
            check=False,
            timeout=1200,
        )
        if result.returncode == 0:
            print("  概念板块数据更新完成 ✅")
        else:
            print(f"  概念板块数据更新未完全成功，返回码 {result.returncode}（不影响K线数据）")
    else:
        print(f"  未找到概念更新脚本: {concept_script}")
except subprocess.TimeoutExpired:
    print("  概念板块数据更新超时（不影响K线数据），可稍后单独运行 _fetch_concept_data.py")
except Exception as e:
    print(f"  概念板块数据更新失败: {e}")

print()
print("=" * 70)
print(f"  ✅ 完成！更新: {updated} | 无新数据: {no_data} | 失败: {failed}")

# 验证
last_sh = get_last_date('sh.000001')
last_sample = get_last_date('sh.600000') if 'sh.600000' in codes else None
print(f"  上证指数最新: {last_sh}")
if last_sample:
    print(f"  浦发银行最新: {last_sample}")
print("=" * 70)
