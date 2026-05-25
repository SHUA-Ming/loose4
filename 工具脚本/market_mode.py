#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场模式自动识别 (V6.1)
判定当前属于 M1磨底 / M2震荡 / M3反弹 / M4强趋势 / M5极端
输出对应的策略权重、板块淘汰线、S3 X2阈值等参数

V6.1新增：
  - 集成情绪周期判定（涨停生态）
  - 高潮日/分歧日自动降级仓位
  - 板块轮动预判输出

可独立运行查看结果，也可被 offline_screener.py import 调用
"""
import sys, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

from db_cache import get_connection, init_db
from market_sentiment import get_cycle_phase, compute_sentiment
import pandas as pd
import numpy as np

# ═══ 模式配置 ═══
MODE_CONFIG = {
    'M1': {
        'name': '磨底期', 'emoji': '🧊',
        'desc': '缩量磨底，只做S1蓄力',
        'strategies': {'S1': 'primary', 'S2': 'disabled', 'S3': 'disabled'},
        'sector_cutoff': {'S1': 0.30, 'S2': None, 'S3': None},
        'position': {'S1_A': '1/4', 'S1_B': '1/8'},
        's3_x2_limit': 20,
        's3_x2_relax': None,
    },
    'M2': {
        'name': '震荡期', 'emoji': '🔄',
        'desc': '方向不明，S1蓄力+S2大阳横盘',
        'strategies': {'S1': 'primary', 'S2': 'secondary', 'S3': 'disabled'},
        'sector_cutoff': {'S1': 0.30, 'S2': 0.30, 'S3': None},
        'position': {'S1_A': '1/4', 'S1_B': '1/8', 'S2_A': '1/4', 'S2_B': '1/8'},
        's3_x2_limit': 20,
        's3_x2_relax': None,
    },
    'M3': {
        'name': '反弹修复', 'emoji': '🚀',
        'desc': '反弹行情，S2/S3为主，S1降为观察池',
        'strategies': {'S1': 'watchonly', 'S2': 'primary', 'S3': 'secondary'},
        'sector_cutoff': {'S1': 0.50, 'S2': 0.40, 'S3': 0.40},
        'position': {'S2_A': '1/4', 'S2_B': '1/8', 'S3_A': '1/4', 'S3_B': '1/8'},
        's3_x2_limit': 25,
        's3_x2_relax': {'top30_sector': 30},
    },
    'M4': {
        'name': '强趋势', 'emoji': '🔥',
        'desc': '趋势上升，S3突破为主，S2辅助',
        'strategies': {'S1': 'disabled', 'S2': 'secondary', 'S3': 'primary'},
        'sector_cutoff': {'S1': None, 'S2': 0.40, 'S3': 0.30},
        'position': {'S2_A': '1/4', 'S2_B': '1/8', 'S3_A': '1/4', 'S3_B': '1/8'},
        's3_x2_limit': 35,
        's3_x2_relax': {'top20_sector': 40},
    },
    'M5': {
        'name': '过热/恐慌', 'emoji': '⛔',
        'desc': '极端行情，空仓观望',
        'strategies': {'S1': 'disabled', 'S2': 'disabled', 'S3': 'disabled'},
        'sector_cutoff': {'S1': None, 'S2': None, 'S3': None},
        'position': {},
        's3_x2_limit': 20,
        's3_x2_relax': None,
    },
}


def detect_market_mode(verbose=True):
    """
    检测当前市场模式，返回 (mode_key, mode_config, details_dict)
    details_dict 包含三大指数的情绪、趋势等中间数据
    """
    init_db()
    conn = get_connection()

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
    }

    if verbose:
        # 先输出情绪生态报告
        sentiment_result = compute_sentiment(verbose=True)
        print()  # 分隔线
        _print_report(mode, config, details)
    else:
        sentiment_result = None

    return mode, config, details


def _print_report(mode, config, details):
    composite = details['composite_emotion']
    index_data = details['index_data']

    print("=" * 80)
    print(f"  市场模式判定 (V6) — {config['emoji']} {mode} {config['name']}")
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
        icon = {'primary': '🟢主力', 'secondary': '🔵辅助', 'watchonly': '👀观察', 'disabled': '⛔禁用'}[role]
        print(f"    {s}: {icon}")

    print()
    print("  ── 板块淘汰线 ──")
    for s, cutoff in config['sector_cutoff'].items():
        if cutoff is not None:
            print(f"    {s}: 后{int(cutoff*100)}%淘汰")
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
    if mode == 'M3':
        print("    · S1通过的票标注为「观察池」，不下单")
    print()


def get_mode_params():
    """
    静默返回模式参数字典，供 offline_screener.py 调用
    返回 dict:
      mode: str ('M1'-'M5')
      strategies: dict  (S1/S2/S3 → 'primary'/'secondary'/'watchonly'/'disabled')
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
    }


if __name__ == '__main__':
    detect_market_mode(verbose=True)
