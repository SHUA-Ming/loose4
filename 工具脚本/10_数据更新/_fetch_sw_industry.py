#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
申万行业(三层树)数据采集脚本

背景：
    库里原有的行业口径是"证监会大类"(sector_daily，单层约86类，太粗，C39把
    机器人/算力/电子/PCB 全塞一个桶)；同花顺概念(concept_daily)扁平不分级、重叠噪声大。
    申万行业提供"能分级、不重叠"的骨架：一级31 → 二级131 → 三级336，且每个指数
    都有可回溯的日K，做细分板块轮动最干净。

功能：
    1. 拉取申万一/二/三级行业信息(含上级行业、成份数、估值) → sw_industry_info
    2. 拉取各级行业指数日K → sw_industry_daily（默认只灌一级+三级，二级可选）
       日K接口只需 index_hist_sw，不依赖已损坏的三级成分股接口。

用法：
    python _fetch_sw_industry.py                # 日常：刷新三层树信息 + 一/三级近250日K
    python _fetch_sw_industry.py --full --days 400   # 首次多留历史
    python _fetch_sw_industry.py --levels 1,2,3      # 连二级日K一起灌(慢，多131个)
    python _fetch_sw_industry.py --info-only         # 只刷新三层树信息，不动日K
    python _fetch_sw_industry.py --limit 20          # 调试：每级只更新前20个指数

注意：纯采集脚本，走事务连接、及时提交；三级"成分股→个股"映射接口已坏，本脚本不依赖它。
"""
import argparse
import os
import sys
import time
import warnings
from datetime import datetime
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
    init_db, get_connection,
    upsert_sw_industry_info, upsert_sw_industry_daily,
    replace_sw_industry_member,
)

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def _to_float(value):
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace('%', '').replace(',', '').strip()
            if value in ('', '-', '--', 'None', 'nan'):
                return None
        import math
        f = float(value)
        return None if math.isnan(f) else f
    except Exception:
        return None


def _to_int(value):
    f = _to_float(value)
    return int(f) if f is not None else None


def _bare(code):
    """去掉 .SI 后缀，统一存 6位纯数字代码。"""
    return str(code).split('.')[0].strip()


def _retry(label, func, *args, retries=3, sleep_seconds=1.5, **kwargs):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds * attempt)
    raise last_error


def _info_rows(df, level, today, has_parent):
    rows = []
    if df is None or df.empty:
        return rows
    for _, row in df.iterrows():
        rec = row.to_dict()
        code = _bare(rec.get('行业代码', ''))
        name = str(rec.get('行业名称', '') or '').strip()
        if not code or not name:
            continue
        parent = str(rec.get('上级行业', '') or '').strip() if has_parent else None
        rows.append((
            code, level, name, parent,
            _to_int(rec.get('成份个数')),
            _to_float(rec.get('TTM(滚动)市盈率')),
            _to_float(rec.get('市净率')),
            today,
        ))
    return rows


def _norm_code(raw):
    """legulegu '688017.SH' → 'sh.688017'，统一成 kline_daily 的代码格式。"""
    s = str(raw).strip().upper()
    num, mkt = (s.split('.', 1) + [''])[:2] if '.' in s else (s, '')
    num = ''.join(ch for ch in num if ch.isdigit())
    if len(num) != 6:
        return ''
    if mkt.startswith('SH') or num.startswith('6'):
        return f'sh.{num}'
    if mkt.startswith('SZ') or num.startswith(('0', '3')):
        return f'sz.{num}'
    if mkt.startswith('BJ') or num.startswith(('4', '8')):
        return f'bj.{num}'
    return ''


def _scrape_l3_members(l3_code, today, retries=3):
    """抓取申万三级成分股。akshare sw_index_third_cons 因上游多一列贴错列名而坏，
    这里直接爬同一 legulegu 页面，按列位置取 代码(第1列)/简称(第2列)，稳。"""
    import requests
    import pandas as pd
    from io import StringIO

    url = f'https://legulegu.com/stockdata/index-composition?industryCode={l3_code}.SI'

    def _do():
        r = requests.get(url, headers={'User-Agent': _UA}, timeout=20)
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        if not tables or tables[0].shape[1] < 3:
            return []
        df = tables[0]
        rows = []
        for _, x in df.iterrows():
            code = _norm_code(x.iloc[1])
            if not code:
                continue
            name = str(x.iloc[2]).strip()
            rows.append((l3_code, code, name, today))
        return rows

    return _retry(f'{l3_code}成分', _do, retries=retries, sleep_seconds=2)


def _active_l3_codes(days=10):
    """返回近端有日K的三级代码(剔除申万改版停更的旧代码)。只读查询。"""
    conn = get_connection(readonly=True)
    latest = conn.execute("SELECT MAX(date) FROM sw_industry_daily").fetchone()[0]
    if not latest:
        conn.close()
        return []
    rows = conn.execute(
        """SELECT d.code FROM sw_industry_daily d JOIN sw_industry_info i ON d.code=i.code
           WHERE i.level=3 GROUP BY d.code HAVING DATEDIFF(?, MAX(d.date)) <= ?""",
        (latest, days)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _daily_rows(code, hist_df, keep_days):
    """把 index_hist_sw 结果转成 (code,date,o,h,l,c,vol,amt,pctChg)，pctChg 由收盘价推算。"""
    rows = []
    if hist_df is None or hist_df.empty:
        return rows
    hist_df = hist_df.sort_values('日期')
    prev_close = None
    buffer = []
    for _, row in hist_df.iterrows():
        rec = row.to_dict()
        date_str = str(rec.get('日期', ''))[:10]
        if not date_str:
            continue
        close_v = _to_float(rec.get('收盘'))
        pct = None
        if close_v is not None and prev_close:
            pct = round((close_v / prev_close - 1) * 100, 4)
        buffer.append((
            code, date_str,
            _to_float(rec.get('开盘')), _to_float(rec.get('最高')),
            _to_float(rec.get('最低')), close_v,
            _to_float(rec.get('成交量')), _to_float(rec.get('成交额')),
            pct,
        ))
        if close_v is not None:
            prev_close = close_v
    if keep_days and keep_days > 0:
        buffer = buffer[-keep_days:]
    return buffer


def _members_done_recently(days=6):
    """近 days 天内已抓过成分的三级代码，用于断点续传：legulegu 易限流/504，
    分多次轻量跑，每次只补缺失的，避免一被限流就前功尽弃。"""
    conn = get_connection(readonly=True)
    rows = conn.execute(
        "SELECT DISTINCT l3_code FROM sw_industry_member WHERE DATEDIFF(CURDATE(), update_date) <= ?",
        (days,)
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def _run_members(today, member_sleep, limit, retries, force=False):
    """抓取活跃三级成分股入库。可断点续传(默认跳过近6天已抓的)。返回 (成功数, 目标数, 写入行数)。"""
    codes = _active_l3_codes()
    if not codes:
        print("  成分抓取跳过：库中无活跃三级日K，请先跑一次日K。")
        return 0, 0, 0
    if not force:
        done = _members_done_recently()
        pending = [c for c in codes if c not in done]
        print(f"  成分断点续传：活跃三级 {len(codes)} 个，已抓 {len(done & set(codes))} 个，待补 {len(pending)} 个")
        codes = pending
    if not codes:
        print("  成分已是最新，无需补抓。")
        return 0, 0, 0
    if limit > 0:
        codes = codes[:limit]
    print(f"  抓取三级成分股: {len(codes)} 个 (爬 legulegu，礼貌间隔 {member_sleep}s)")
    ok = 0
    total = 0
    fail = []
    for idx, code in enumerate(codes, 1):
        try:
            rows = _scrape_l3_members(code, today, retries=retries)
            replace_sw_industry_member(code, rows)
            if rows:
                ok += 1
                total += len(rows)
            else:
                fail.append(f"{code}(空)")
        except Exception as exc:
            fail.append(f"{code}({type(exc).__name__})")
        if idx % 40 == 0:
            print(f"    成分进度 {idx}/{len(codes)}  成功{ok}  写入{total}行")
        if member_sleep > 0:
            time.sleep(member_sleep)
    print(f"  成分抓取完成: {ok}/{len(codes)} 个三级有成分，写入 {total} 行"
          + (f"，失败 {len(fail)} 个" if fail else ""))
    return ok, len(codes), total


def main():
    parser = argparse.ArgumentParser(description='采集申万三层行业信息与指数日K')
    parser.add_argument('--full', action='store_true', help='全量模式，多留历史(配合 --days)')
    parser.add_argument('--days', type=int, default=250, help='每个指数保留最近多少个交易日日K，默认250')
    parser.add_argument('--levels', default='1,3', help='拉取日K的层级，逗号分隔，默认 1,3；加 2 会多131个较慢')
    parser.add_argument('--info-only', action='store_true', help='只刷新三层树信息，不动日K')
    parser.add_argument('--members', action='store_true', help='额外抓取活跃三级成分股→sw_industry_member(爬legulegu，较慢，成分慢变每周刷一次即可)')
    parser.add_argument('--members-only', action='store_true', help='只刷新三级成分股，跳过信息树与日K')
    parser.add_argument('--members-force', action='store_true', help='成分全量重抓(默认断点续传，跳过近6天已抓的)')
    parser.add_argument('--member-sleep', type=float, default=1.0, help='抓成分时每个三级之间暂停秒数，礼貌爬(legulegu易限流，别调太小)')
    parser.add_argument('--member-limit', type=int, default=0, help='本次成分最多补N个三级(断点续传下用于每天增量补，避开legulegu限流)')
    parser.add_argument('--limit', type=int, default=0, help='调试：指数日K只更新前N个')
    parser.add_argument('--sleep', type=float, default=0.12, help='每个指数之间暂停秒数')
    parser.add_argument('--retries', type=int, default=3, help='接口失败重试次数')
    args = parser.parse_args()

    try:
        import akshare as ak
    except Exception as exc:
        print(f"akshare 导入失败: {exc}")
        return 1

    init_db()
    today = datetime.now().strftime('%Y-%m-%d')
    keep_days = max(args.days, 60) if not args.full else args.days
    want_levels = {int(x) for x in args.levels.split(',') if x.strip() in ('1', '2', '3')}

    print("=" * 80)
    print(f"  申万行业数据更新  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)

    # ── 0. 只刷成分：跳过信息树与日K，直接抓成分 ──
    if args.members_only:
        _run_members(today, args.member_sleep, args.member_limit, args.retries, force=args.members_force)
        print("=" * 80)
        return 0

    # ── 1. 三层树信息 ──
    level_fetch = [
        (1, ak.sw_index_first_info, False),
        (2, ak.sw_index_second_info, True),
        (3, ak.sw_index_third_info, True),
    ]
    info_by_level = {}
    all_info_rows = []
    for level, fn, has_parent in level_fetch:
        try:
            df = _retry(f'申万{level}级信息', fn, retries=args.retries, sleep_seconds=2)
            rows = _info_rows(df, level, today, has_parent)
            info_by_level[level] = rows
            all_info_rows.extend(rows)
            print(f"  {level}级行业: {len(rows)} 个")
        except Exception as exc:
            print(f"  {level}级信息获取失败: {type(exc).__name__}: {exc}")
            info_by_level[level] = []
    if all_info_rows:
        upsert_sw_industry_info(all_info_rows)
        print(f"  三层树信息写入 {len(all_info_rows)} 行")

    if args.info_only:
        print("  模式: 只刷新信息，跳过日K")
        print("=" * 80)
        return 0

    # ── 2. 指数日K（按 --levels）──
    daily_ok = 0
    daily_fail = []
    total_rows = 0
    targets = []
    for level in sorted(want_levels):
        rows = info_by_level.get(level, [])
        codes = [(r[0], r[2]) for r in rows]  # (code, name)
        if args.limit > 0:
            codes = codes[:args.limit]
        targets.extend([(level, c, n) for c, n in codes])

    print(f"  计划拉取日K: {len(targets)} 个指数 (层级 {sorted(want_levels)})，每个保留近 {keep_days} 日")
    for idx, (level, code, name) in enumerate(targets, 1):
        try:
            hist = _retry(f'{name}({code})日K', ak.index_hist_sw,
                          symbol=code, period='day',
                          retries=args.retries, sleep_seconds=1.5)
            rows = _daily_rows(code, hist, keep_days)
            if rows:
                upsert_sw_industry_daily(rows)
                daily_ok += 1
                total_rows += len(rows)
            else:
                daily_fail.append(f"{name}(空)")
        except Exception as exc:
            daily_fail.append(f"{name}({type(exc).__name__})")
        if idx % 40 == 0:
            print(f"    进度 {idx}/{len(targets)}  成功{daily_ok}  写入{total_rows}行")
        if args.sleep > 0:
            time.sleep(args.sleep)

    print("=" * 80)
    print(f"  完成: 日K成功 {daily_ok}/{len(targets)}，写入/更新 {total_rows} 行")
    print(f"        失败 {len(daily_fail)} 个" + (f"，样例: {', '.join(daily_fail[:8])}" if daily_fail else ""))

    # ── 3. 可选：抓三级成分股 ──
    if args.members:
        print("-" * 80)
        _run_members(today, args.member_sleep, args.member_limit, args.retries, force=args.members_force)

    print("=" * 80)
    return 0 if daily_ok > 0 else 2


if __name__ == '__main__':
    raise SystemExit(main())
