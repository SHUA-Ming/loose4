#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念板块数据采集脚本

功能：
    1. 从 akshare 获取概念列表 → concept_info（优先东方财富，失败切同花顺）
  2. 获取概念近一年或近期日K → concept_daily
  3. 获取概念当前成分股 → concept_member

用法：
  python _fetch_concept_data.py                  # 日常增量，默认补近30天并刷新成分股
  python _fetch_concept_data.py --full --days 365 # 首次全量，补近一年并刷新成分股
    python _fetch_concept_data.py --skip-members    # 只补概念日K，不刷新成分股
    python _fetch_concept_data.py --members-only    # 只刷新概念成分股（同花顺默认每概念最多前50个）
    python _fetch_concept_data.py --source ths      # 指定同花顺兜底源
  python _fetch_concept_data.py --limit 20        # 调试：只更新前20个概念
"""
import argparse
import os
import re
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')
os.environ.setdefault('NO_PROXY', '*')
os.environ.setdefault('no_proxy', '*')

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import (
    init_db,
    upsert_concept_info,
    upsert_concept_daily,
    replace_concept_members,
)

SOURCE = 'eastmoney'


def _to_float(value):
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace('%', '').replace(',', '').strip()
            if value in ('', '-', '--', 'None'):
                return None
        return float(value)
    except Exception:
        return None


def _to_int(value):
    num = _to_float(value)
    return int(num) if num is not None else None


def _pick(row, candidates, default=None):
    for key in candidates:
        if key in row and row[key] is not None:
            return row[key]
    return default


def _normalize_code(raw_code):
    code = str(raw_code).strip()
    if not code or code in ('nan', 'None'):
        return ''
    if code.startswith(('sh.', 'sz.', 'bj.')):
        return code
    code = code.replace('SH', '').replace('SZ', '').replace('BJ', '').replace('.', '')
    if len(code) != 6 or not code.isdigit():
        return code
    if code.startswith('6'):
        return f'sh.{code}'
    if code.startswith(('0', '3')):
        return f'sz.{code}'
    if code.startswith(('4', '8')):
        return f'bj.{code}'
    return code


def _retry(label, func, *args, retries=3, sleep_seconds=1.5, **kwargs):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(f"  {label}: 第{attempt}次失败，稍后重试 ({type(exc).__name__})")
                time.sleep(sleep_seconds * attempt)
    raise last_error


def _concept_info_rows(concept_df, today):
    rows = []
    for idx, row in concept_df.iterrows():
        rec = row.to_dict()
        name = str(_pick(rec, ['板块名称', '概念名称', 'name'], '')).strip()
        if not name:
            continue
        code = str(_pick(rec, ['板块代码', '概念代码', '代码', 'code'], '') or '').strip()
        latest_pct = _to_float(_pick(rec, ['涨跌幅', '涨幅']))
        latest_amount = _to_float(_pick(rec, ['成交额', '金额']))
        latest_rank = _to_int(_pick(rec, ['排名'])) or (idx + 1)
        rows.append((SOURCE, name, code, latest_pct, latest_amount, latest_rank, today))
    return rows


def _hist_rows(concept_name, hist_df):
    rows = []
    if hist_df is None or hist_df.empty:
        return rows
    prev_close = None
    for _, row in hist_df.iterrows():
        rec = row.to_dict()
        date_val = _pick(rec, ['日期', 'date'])
        if date_val is None:
            continue
        date_str = str(date_val)[:10]
        close_value = _to_float(_pick(rec, ['收盘', '收盘价', 'close', '最新价']))
        pct_chg = _to_float(_pick(rec, ['涨跌幅', 'pctChg']))
        change_amount = _to_float(_pick(rec, ['涨跌额', 'change_amount']))
        if pct_chg is None and close_value is not None and prev_close:
            pct_chg = round((close_value / prev_close - 1) * 100, 4)
        if change_amount is None and close_value is not None and prev_close:
            change_amount = round(close_value - prev_close, 4)
        rows.append((
            SOURCE,
            concept_name,
            date_str,
            _to_float(_pick(rec, ['开盘', '开盘价', 'open'])),
            _to_float(_pick(rec, ['最高', '最高价', 'high'])),
            _to_float(_pick(rec, ['最低', '最低价', 'low'])),
            close_value,
            _to_float(_pick(rec, ['成交量', 'volume'])),
            _to_float(_pick(rec, ['成交额', 'amount'])),
            _to_float(_pick(rec, ['振幅', 'amplitude'])),
            pct_chg,
            change_amount,
            _to_float(_pick(rec, ['换手率', 'turn'])),
        ))
        if close_value is not None:
            prev_close = close_value
    return rows


def _load_concept_list(ak, source, retries):
    errors = []
    if source in ('auto', 'eastmoney'):
        try:
            df = _retry('东方财富概念列表', ak.stock_board_concept_name_em, retries=retries, sleep_seconds=2)
            return 'eastmoney', df
        except Exception as exc:
            errors.append(f"eastmoney: {type(exc).__name__}: {exc}")
            if source == 'eastmoney':
                raise
            print("  东方财富概念列表失败，切换同花顺兜底")
    if source in ('auto', 'ths'):
        try:
            df = _retry('同花顺概念列表', ak.stock_board_concept_name_ths, retries=retries, sleep_seconds=2)
            return 'ths', df
        except Exception as exc:
            errors.append(f"ths: {type(exc).__name__}: {exc}")
            raise RuntimeError('; '.join(errors))
    raise RuntimeError(f"未知数据源: {source}")


def _load_concept_hist(ak, concept_name, start_date, end_date, retries):
    if SOURCE == 'ths':
        return _retry(
            f'{concept_name} 同花顺日K', ak.stock_board_concept_index_ths,
            symbol=concept_name, start_date=start_date, end_date=end_date,
            retries=retries, sleep_seconds=1.5
        )
    return _retry(
        f'{concept_name} 东方财富日K', ak.stock_board_concept_hist_em,
        symbol=concept_name, period='daily', start_date=start_date,
        end_date=end_date, adjust='', retries=retries,
        sleep_seconds=1.5
    )


def _member_rows(concept_name, member_df, today):
    rows = []
    if member_df is None or member_df.empty:
        return rows
    for _, row in member_df.iterrows():
        rec = row.to_dict()
        raw_code = _pick(rec, ['代码', 'code', '股票代码'])
        code = _normalize_code(raw_code)
        if not code:
            continue
        name = str(_pick(rec, ['名称', '股票简称', 'code_name'], '') or '').strip()
        rows.append((SOURCE, concept_name, code, name, today))
    return rows


def _parse_ths_member_html(html):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, features='lxml')
    rows = []
    for tr in soup.find_all('tr'):
        cells = [cell.get_text(strip=True) for cell in tr.find_all(['td', 'th'])]
        if len(cells) >= 3 and cells[0].isdigit() and cells[1].isdigit():
            rows.append({'代码': cells[1], '名称': cells[2]})

    total_pages = 1
    page_info = soup.find('span', class_='page_info')
    if page_info:
        match = re.search(r'/\s*(\d+)', page_info.get_text(strip=True))
        if match:
            total_pages = max(1, int(match.group(1)))
    return rows, total_pages


def _load_ths_concept_members(concept_name, concept_code, max_pages=5):
    """抓取同花顺概念成分股，返回含 代码/名称 的 DataFrame。"""
    if not concept_code:
        raise RuntimeError(f'{concept_name} 缺少同花顺概念代码')

    import pandas as pd
    import requests

    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'http://q.10jqka.com.cn/gn/detail/code/{concept_code}/',
    }

    first_url = f'http://q.10jqka.com.cn/gn/detail/code/{concept_code}/'
    resp = session.get(first_url, headers=headers, timeout=15)
    resp.encoding = 'gbk'
    all_rows, total_pages = _parse_ths_member_html(resp.text)

    total_pages = min(total_pages, max_pages)
    for page in range(2, total_pages + 1):
        url = (
            'http://q.10jqka.com.cn/gn/detail/field/199112/order/desc/'
            f'page/{page}/ajax/1/code/{concept_code}/'
        )
        resp = session.get(url, headers=headers, timeout=15)
        resp.encoding = 'gbk'
        rows, _ = _parse_ths_member_html(resp.text)
        all_rows.extend(rows)
        time.sleep(0.03)

    if not all_rows:
        return pd.DataFrame(columns=['代码', '名称'])
    return pd.DataFrame(all_rows).drop_duplicates(subset=['代码'])


def main():
    global SOURCE
    parser = argparse.ArgumentParser(description='更新概念板块数据')
    parser.add_argument('--full', action='store_true', help='全量模式，按 --days 拉取历史')
    parser.add_argument('--days', type=int, default=30, help='拉取最近多少个自然日，--full 建议 365')
    parser.add_argument('--source', choices=['auto', 'eastmoney', 'ths'], default='auto', help='数据源：默认东财失败后切同花顺')
    parser.add_argument('--limit', type=int, default=0, help='调试用：只更新前N个概念')
    parser.add_argument('--skip-members', action='store_true', help='跳过概念成分股刷新')
    parser.add_argument('--members-only', action='store_true', help='只刷新概念成分股，跳过概念日K')
    parser.add_argument('--ths-member-pages', type=int, default=5, help='同花顺成分股最多抓取页数，每页10只，默认前50只')
    parser.add_argument('--sleep', type=float, default=0.25, help='每个概念之间的暂停秒数，防止接口过载')
    parser.add_argument('--retries', type=int, default=3, help='接口失败重试次数')
    args = parser.parse_args()

    if args.members_only and args.skip_members:
        print('参数冲突: --members-only 与 --skip-members 不能同时使用')
        return 1

    try:
        import akshare as ak
    except Exception as exc:
        print(f"akshare 导入失败: {exc}")
        return 1

    init_db()
    today = datetime.now().strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y%m%d')
    start_days = args.days if args.full else max(args.days, 30)
    start_date = (datetime.now() - timedelta(days=start_days)).strftime('%Y%m%d')

    print("=" * 80)
    print(f"  概念板块数据更新  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)
    print(f"  模式: {'全量' if args.full else '日常增量'}  范围: {start_date} ~ {end_date}")

    try:
        active_source, concept_df = _load_concept_list(ak, args.source, args.retries)
        SOURCE = active_source
    except Exception as exc:
        print(f"  概念列表获取失败: {type(exc).__name__}: {exc}")
        return 2

    if concept_df is None or concept_df.empty:
        print("  概念列表为空，停止更新")
        return 2

    info_rows = _concept_info_rows(concept_df, today)
    upsert_concept_info(info_rows)
    concept_names = [row[1] for row in info_rows]
    concept_code_map = {row[1]: row[2] for row in info_rows}
    if args.limit > 0:
        concept_names = concept_names[:args.limit]
    print(f"  数据源: {SOURCE}")
    print(f"  概念列表: {len(info_rows)} 个，计划更新: {len(concept_names)} 个")
    if args.members_only:
        print("  模式: 只刷新概念成分股")

    hist_ok = 0
    member_ok = 0
    failed = []
    total_daily_rows = 0
    total_member_rows = 0

    for idx, concept_name in enumerate(concept_names, 1):
        print(f"  [{idx:>3d}/{len(concept_names)}] {concept_name}", end='')
        concept_failed = False

        if args.members_only:
            print("  日K跳过", end='')
        else:
            try:
                hist_df = _load_concept_hist(ak, concept_name, start_date, end_date, args.retries)
                rows = _hist_rows(concept_name, hist_df)
                if rows:
                    upsert_concept_daily(rows)
                    hist_ok += 1
                    total_daily_rows += len(rows)
                    print(f"  日K{len(rows)}", end='')
                else:
                    print("  日K空", end='')
            except Exception as exc:
                concept_failed = True
                print(f"  日K失败({type(exc).__name__})", end='')

        if not args.skip_members:
            try:
                if SOURCE == 'ths':
                    member_df = _retry(
                        f'{concept_name} 同花顺成分股', _load_ths_concept_members,
                        concept_name, concept_code_map.get(concept_name, ''), args.ths_member_pages,
                        retries=args.retries, sleep_seconds=1.5
                    )
                else:
                    member_df = _retry(
                        f'{concept_name} 成分股', ak.stock_board_concept_cons_em,
                        symbol=concept_name, retries=args.retries,
                        sleep_seconds=1.5
                    )
                rows = _member_rows(concept_name, member_df, today)
                replace_concept_members(SOURCE, concept_name, rows)
                member_ok += 1
                total_member_rows += len(rows)
                print(f"  成分{len(rows)}", end='')
            except Exception as exc:
                concept_failed = True
                print(f"  成分失败({type(exc).__name__})", end='')

        if concept_failed:
            failed.append(concept_name)
        print()
        if args.sleep > 0:
            time.sleep(args.sleep)

    print("=" * 80)
    print(f"  完成: 日K成功 {hist_ok}/{len(concept_names)}，写入/更新 {total_daily_rows} 行")
    if not args.skip_members:
        print(f"        成分成功 {member_ok}/{len(concept_names)}，写入/更新 {total_member_rows} 行")
    print(f"        失败概念 {len(failed)} 个")
    if failed:
        print(f"  失败样例: {', '.join(failed[:10])}")
    print("=" * 80)
    return 0 if len(failed) < len(concept_names) else 2


if __name__ == '__main__':
    raise SystemExit(main())