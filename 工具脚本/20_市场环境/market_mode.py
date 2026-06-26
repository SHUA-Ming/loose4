#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场模式自动识别 (V10主线趋势版)
判定当前属于 M1磨底 / M2震荡 / M3反弹 / M4强趋势 / M5极端
输出对应的策略角色、板块准入线、S3 X2阈值等参数

当前能力：
  - 集成情绪周期判定（涨停生态）
  - 高潮日/分歧日自动降级仓位
  - 板块轮动预判输出
    - V8策略角色与板块准入线

可独立运行查看结果，也可被 offline_screener.py import 调用
"""
import sys, warnings
from pathlib import Path
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
from db_cache import get_connection, init_db
from market_sentiment import get_cycle_phase, compute_sentiment
import pandas as pd
import numpy as np

# ═══ 模式配置 ═══
# sector_cutoff 语义：sector_rank 强度分位必须 >= 该值。
# sector_rank 越接近 1 越强，所以 0.70 = 只看前30%强板块。
MODE_CONFIG = {
    'M1': {
        'name': '磨底期', 'emoji': '🧊',
        'desc': '缩量磨底，S1为主，S2/S3仅A级小仓试探，S4禁用',
        'strategies': {'S1': 'primary', 'S2': 'trial', 'S3': 'trial', 'S4': 'disabled'},
        'sector_cutoff': {'S1': 0.50, 'S2': None, 'S3': None, 'S4': None},
        'position': {'S1_A': '1/4', 'S1_B': '1/8', 'S2_A': '1/12', 'S2_B': '1/16', 'S3_A': '1/12', 'S3_B': '1/16'},
        's3_x2_limit': 20,
        's3_x2_relax': None,
    },
    'M2': {
        'name': '震荡期', 'emoji': '🔄',
        'desc': '方向不明，S1蓄力+S2大阳横盘，S3/S4仅A级小仓试探',
        'strategies': {'S1': 'primary', 'S2': 'secondary', 'S3': 'trial', 'S4': 'trial'},
        'sector_cutoff': {'S1': 0.50, 'S2': 0.60, 'S3': None, 'S4': 0.70},
        'position': {'S1_A': '1/4', 'S1_B': '1/8', 'S2_A': '1/4', 'S2_B': '1/8', 'S3_A': '1/12', 'S3_B': '1/16', 'S4_A': '1/12', 'S4_B': '1/16'},
        's3_x2_limit': 20,
        's3_x2_relax': None,
    },
    'M3': {
        'name': '反弹修复', 'emoji': '🚀',
        'desc': '反弹行情，S2/S4为主，S3辅助，S1仅A级小仓试探',
        'strategies': {'S1': 'trial', 'S2': 'primary', 'S3': 'secondary', 'S4': 'primary'},
        'sector_cutoff': {'S1': 0.60, 'S2': 0.70, 'S3': 0.70, 'S4': 0.70},
        'position': {'S1_A': '1/12', 'S1_B': '1/16', 'S2_A': '1/4', 'S2_B': '1/8', 'S3_A': '1/4', 'S3_B': '1/8', 'S4_A': '1/8', 'S4_B': '1/12'},
        's3_x2_limit': 25,
        's3_x2_relax': {'top30_sector': 30},
    },
    'M4': {
        'name': '强趋势', 'emoji': '🔥',
        'desc': '趋势上升，S3/S4为主，S2辅助，S1仅A级小仓试探',
        'strategies': {'S1': 'trial', 'S2': 'secondary', 'S3': 'primary', 'S4': 'primary'},
        'sector_cutoff': {'S1': None, 'S2': 0.70, 'S3': 0.75, 'S4': 0.75},
        'position': {'S1_A': '1/12', 'S1_B': '1/16', 'S2_A': '1/4', 'S2_B': '1/8', 'S3_A': '1/4', 'S3_B': '1/8', 'S4_A': '1/6', 'S4_B': '1/12'},
        's3_x2_limit': 35,
        's3_x2_relax': {'top20_sector': 40},
    },
    'M5': {
        'name': '过热/恐慌', 'emoji': '⛔',
        'desc': '极端行情，空仓观望',
        'strategies': {'S1': 'disabled', 'S2': 'disabled', 'S3': 'disabled', 'S4': 'disabled'},
        'sector_cutoff': {'S1': None, 'S2': None, 'S3': None, 'S4': None},
        'position': {},
        's3_x2_limit': 20,
        's3_x2_relax': None,
    },
}


def _max_date(conn, sql, params=None):
    try:
        row = conn.execute(sql, params or []).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _check_data_freshness(conn, index_data):
    """检查指数、个股、板块、概念数据日期是否对齐。"""
    stock_latest = _max_date(
        conn,
        """
        SELECT MAX(date)
        FROM kline_daily
        WHERE code NOT LIKE 'sh.000%' AND code NOT LIKE 'sz.399%'
        """,
    )
    sector_latest = _max_date(conn, "SELECT MAX(date) FROM sector_daily")
    concept_row = None
    try:
        concept_row = conn.execute(
            """
            SELECT source, MAX(date)
            FROM concept_daily
            GROUP BY source
            ORDER BY MAX(date) DESC, COUNT(*) DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        concept_row = None

    index_dates = [meta.get('last_date') for meta in index_data.values() if meta.get('last_date')]
    index_latest = max(index_dates) if index_dates else None
    concept_source = concept_row[0] if concept_row else None
    concept_latest = concept_row[1] if concept_row else None
    warnings_list = []

    if stock_latest and index_latest and index_latest < stock_latest:
        warnings_list.append(
            f"指数数据滞后：指数最新{index_latest}，个股最新{stock_latest}。先运行 `python 工具脚本/10_数据更新/_fetch_market_data.py --from {stock_latest} --skip-industry`。"
        )
    if stock_latest and sector_latest and sector_latest < stock_latest:
        warnings_list.append(
            f"板块统计滞后：板块最新{sector_latest}，个股最新{stock_latest}。先运行 `python 工具脚本/10_数据更新/_fetch_market_data.py --from {stock_latest} --skip-index --skip-industry`。"
        )
    if stock_latest and concept_latest and concept_latest < stock_latest:
        warnings_list.append(
            f"概念数据滞后：{concept_source or 'concept'}最新{concept_latest}，个股最新{stock_latest}。先运行 `python 工具脚本/10_数据更新/_fetch_concept_data.py`。"
        )

    return {
        'stock_latest': stock_latest,
        'index_latest': index_latest,
        'sector_latest': sector_latest,
        'concept_source': concept_source,
        'concept_latest': concept_latest,
        'warnings': warnings_list,
    }


_MODE_CACHE = None  # 进程级缓存：一次运行内市场模式只算一次


def detect_market_mode(verbose=True):
    """
    检测当前市场模式，返回 (mode_key, mode_config, details_dict)
    details_dict 包含三大指数的情绪、趋势等中间数据
    """
    global _MODE_CACHE
    if _MODE_CACHE is not None:
        mode, config, details = _MODE_CACHE
        if verbose:
            compute_sentiment(verbose=True)  # 命中缓存(内部亦缓存)，仅补打印
            print()
            _print_report(mode, config, details)
        return _MODE_CACHE

    conn = get_connection(readonly=True)  # 纯分析：只读连接，不建表

    index_data = {}
    emotions = []

    for idx_code, idx_name in [('sh.000001', '上证指数'), ('sz.399001', '深证成指'), ('sz.399006', '创业板指')]:
        df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', conn, params=[idx_code])
        if df.empty:
            continue
        for c in ['open', 'high', 'low', 'close', 'volume', 'pctChg']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['close'])

        cls = df['close'].values
        pcts = df['pctChg'].values
        vols = df['volume'].values
        n = len(cls)
        if n < 60:
            continue

        last = cls[-1]
        ma5 = np.mean(cls[-5:])
        ma10 = np.mean(cls[-10:])
        ma20 = np.mean(cls[-20:])
        ma60 = np.mean(cls[-60:])
        pct5 = (cls[-1] - cls[-5]) / cls[-5] * 100
        vol520 = np.mean(vols[-5:]) / np.mean(vols[-20:]) if np.mean(vols[-20:]) > 0 else 1

        bullish = sum([ma5 > ma10, ma10 > ma20, ma20 > ma60, last > ma60, last > ma20])

        # 情绪评分 (0-10)
        emotion = 5
        if bullish >= 4:
            emotion += 2
        elif bullish <= 1:
            emotion -= 2
        if vol520 > 1.2:
            emotion += 1
        elif vol520 < 0.7:
            emotion -= 1
        if pct5 > 3:
            emotion += 1
        elif pct5 < -3:
            emotion -= 1
        emotion = max(0, min(10, emotion))
        emotions.append(emotion)

        # 连涨连跌
        streak = 0
        for i in range(len(pcts) - 1, -1, -1):
            if streak == 0:
                streak = 1 if pcts[i] > 0 else -1
            elif (streak > 0 and pcts[i] > 0):
                streak += 1
            elif (streak < 0 and pcts[i] < 0):
                streak -= 1
            else:
                break

        index_data[idx_name] = {
            'last': last, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'pct5': pct5, 'vol520': vol520, 'bullish': bullish, 'emotion': emotion,
            'streak': streak,
            'above_ma20': last > ma20,
            'above_ma60': last > ma60,
            'ma20_above_ma60': ma20 > ma60,
            'last_date': df['date'].iloc[-1],
        }

    freshness = _check_data_freshness(conn, index_data)
    conn.close()

    if not emotions:
        return 'M2', MODE_CONFIG['M2'], {}

    # 综合情绪 = 三大指数均值（创业板权重稍高）
    if len(emotions) == 3:
        composite = emotions[0] * 0.3 + emotions[1] * 0.3 + emotions[2] * 0.4
    else:
        composite = np.mean(emotions)
    composite = round(composite, 1)

    # 取创业板为主要参考（反弹市创业板最敏感）
    ref = index_data.get('创业板指', index_data.get('深证成指', list(index_data.values())[0]))

    # ═══ 模式判定 ═══
    if composite <= 1 or composite >= 10:
        mode = 'M5'
    elif composite <= 3 and not ref.get('above_ma60', True):
        mode = 'M1'
    elif composite >= 8 and ref.get('above_ma60', False) and ref.get('ma20_above_ma60', False):
        mode = 'M4'
    elif composite >= 6 and ref.get('pct5', 0) > 3 and ref.get('above_ma20', False):
        mode = 'M3'
    # 注意：规则要求M3必须同时满足 情绪6-7+近5日涨>3%+站上MA20，不再放宽
    elif composite <= 3:
        mode = 'M1'
    else:
        mode = 'M2'

    config = MODE_CONFIG[mode]

    # V6.1: 集成情绪周期判定
    cycle_data = get_cycle_phase()
    cycle_phase = cycle_data.get('phase', '未知')
    cycle_score = cycle_data.get('score', 0)

    # V6.1: 情绪周期修正模式判定
    # 高潮日：仓位减半，不追新仓
    # 分歧/退潮：仓位减半，只守不攻
    position_modifier = 1.0  # 1.0=正常，0.5=减半，0=禁止
    cycle_warning = ''
    if cycle_phase == '高潮':
        position_modifier = 0.5
        cycle_warning = '⚠️ 情绪高潮日！仓位自动减半，不追新仓。当前是给别人抬轿的时候，不是上轿的时候！'
    elif cycle_phase == '分歧':
        position_modifier = 0.5
        cycle_warning = '⚠️ 情绪分歧期！仓位减半，只守不攻，等方向明确。'
    elif cycle_phase == '退潮':
        position_modifier = 0  # 退潮期不开新仓
        cycle_warning = '⛔ 情绪退潮期！禁止开新仓，等待冰点信号。'
        mode = 'M5'  # 强制降级到空仓模式
        config = MODE_CONFIG['M5']
    elif cycle_phase == '过热':
        position_modifier = 0
        cycle_warning = '⛔ 情绪过热！全部策略禁用。'
        mode = 'M5'
        config = MODE_CONFIG['M5']
    elif cycle_phase == '冰点':
        cycle_warning = '🧊 情绪冰点，可开始S1蓄力选股，等待反转。'

    details = {
        'composite_emotion': composite,
        'emotions': emotions,
        'index_data': index_data,
        'mode': mode,
        'config': config,
        'cycle_phase': cycle_phase,
        'cycle_score': cycle_score,
        'cycle_data': cycle_data,
        'position_modifier': position_modifier,
        'cycle_warning': cycle_warning,
        'freshness': freshness,
    }

    if verbose:
        # 先输出情绪生态报告
        sentiment_result = compute_sentiment(verbose=True)
        print()  # 分隔线
        _print_report(mode, config, details)
    else:
        sentiment_result = None

    _MODE_CACHE = (mode, config, details)
    return mode, config, details


def _print_report(mode, config, details):
    composite = details['composite_emotion']
    index_data = details['index_data']

    print("=" * 80)
    print(f"  市场模式判定 (V10主线趋势版) — {config['emoji']} {mode} {config['name']}")
    print("=" * 80)
    print()

    # 指数概况
    for name, d in index_data.items():
        bars = "█" * int(d['emotion']) + "░" * (10 - int(d['emotion']))
        pos = ""
        if d['above_ma60']:
            pos += ">MA60 "
        if d['above_ma20']:
            pos += ">MA20 "
        if d['ma20_above_ma60']:
            pos += "MA20>60 "
        streak_str = f"连{'涨' if d['streak'] > 0 else '跌'}{abs(d['streak'])}天"
        print(f"  {name}: {d['last']:.2f}  5日{d['pct5']:+.2f}%  [{bars}]{d['emotion']}/10  {streak_str}  {pos}")

    print()
    bars_c = "█" * int(composite) + "░" * (10 - int(composite))
    print(f"  综合情绪: [{bars_c}] {composite}/10")
    print(f"  判定模式: {config['emoji']} {mode} — {config['name']}")
    print(f"  描述: {config['desc']}")

    freshness = details.get('freshness', {})
    freshness_warnings = freshness.get('warnings', [])
    if freshness_warnings:
        print()
        print("  ── 数据新鲜度警告 ──")
        for warning_text in freshness_warnings:
            print(f"    ⚠️ {warning_text}")

    # V6.1: 情绪周期信息
    cycle_phase = details.get('cycle_phase', '未知')
    cycle_score = details.get('cycle_score', 0)
    pos_mod = details.get('position_modifier', 1.0)
    cycle_warning = details.get('cycle_warning', '')
    phase_emoji_map = {'冰点': '🧊', '修复': '🌱', '发酵': '🔥', '高潮': '🎆', '分歧': '⚡', '退潮': '🌊', '过热': '💥'}
    pe = phase_emoji_map.get(cycle_phase, '❓')
    print(f"  情绪周期: {pe} {cycle_phase} (得分{cycle_score}/12)")
    if pos_mod < 1:
        print(f"  仓位修正: ×{pos_mod} ({'减半' if pos_mod == 0.5 else '禁止开仓'})")
    if cycle_warning:
        print(f"  ❗ {cycle_warning}")

    print()
    print("  ── 策略配置 ──")
    for s, role in config['strategies'].items():
        icon = {'primary': '🟢主力', 'secondary': '🔵辅助', 'trial': '🟡试探', 'watchonly': '👀观察', 'disabled': '⛔禁用'}[role]
        print(f"    {s}: {icon}")

    print()
    print("  ── 板块准入线 ──")
    for s, cutoff in config['sector_cutoff'].items():
        if cutoff is not None:
            print(f"    {s}: 强度分位≥{cutoff:.2f}（约前{int((1-cutoff)*100)}%）")
        else:
            role = config['strategies'].get(s, 'disabled')
            if role == 'trial':
                print(f"    {s}: —（试探策略由筛选器按模式使用默认强板块线）")
            else:
                print(f"    {s}: —（该策略禁用）")

    print()
    print("  ── S3 X2阈值 ──")
    print(f"    5日涨幅上限: >{config['s3_x2_limit']}%排除")
    if config['s3_x2_relax']:
        for cond, val in config['s3_x2_relax'].items():
            print(f"    放宽条件: {cond} → 放宽到{val}%")

    print()
    if config['position']:
        print("  ── 仓位配置 ──")
        for k, v in config['position'].items():
            print(f"    {k}: {v}")
    else:
        print("  ── 仓位配置 ──")
        print("    空仓观望，不开新仓")

    print()
    print("  ── 附加规则 ──")
    print("    · 同行业硬性只推1只（最高分）")
    print("    · 同板块取5日涨幅TOP3")
    print("    · 非主力试探策略只允许A级候选，仓位按V8表缩小")
    print()


def get_mode_params():
    """
    静默返回模式参数字典，供 offline_screener.py 调用
    返回 dict:
      mode: str ('M1'-'M5')
    strategies: dict  (S1/S2/S3 → 'primary'/'secondary'/'trial'/'watchonly'/'disabled')
      sector_cutoff: dict (S1/S2/S3 → float or None)
      s3_x2_limit: int
      s3_x2_relax: dict or None
      composite_emotion: float
    """
    mode, config, details = detect_market_mode(verbose=False)
    return {
        'mode': mode,
        'strategies': config['strategies'],
        'sector_cutoff': config['sector_cutoff'],
        's3_x2_limit': config['s3_x2_limit'],
        's3_x2_relax': config['s3_x2_relax'],
        'position': config['position'],
        'composite_emotion': details.get('composite_emotion', 5),
        'cycle_phase': details.get('cycle_phase', '\u672a\u77e5'),
        'cycle_score': details.get('cycle_score', 0),
        'position_modifier': details.get('position_modifier', 1.0),
        'cycle_warning': details.get('cycle_warning', ''),
        'sector_rotation': details.get('cycle_data', {}).get('sector_rotation', {}),
        'freshness': details.get('freshness', {}),
    }


if __name__ == '__main__':
    detect_market_mode(verbose=True)
