#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时包装：强制M3模式运行offline_screener
原因：04-17个股数据尚未入baostock（T+1延迟），导致情绪误判退潮→M5
实际行情：沪指-0.10%、深成指+0.60%、创业板+1.43%，综合情绪7.1/10→M3反弹修复
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

# 在import offline_screener之前，先monkey-patch market_mode
import market_mode as mm

# 保存原始函数
_orig_detect = mm.detect_market_mode
_orig_get_params = mm.get_mode_params

# 用M3配置覆盖
_forced_mode = 'M3'
_forced_config = mm.MODE_CONFIG['M3']
_cached_details = None

def patched_detect(verbose=True):
    global _cached_details
    # 先调用原始检测获取index_data等
    orig_mode, orig_config, details = _orig_detect(verbose=False)
    
    # 覆盖mode为M3
    details['mode'] = _forced_mode
    details['config'] = _forced_config
    # 修正情绪周期（排除04-17空数据的影响）
    # 实际最后交易日04-16: 50涨停 0跌停 涨跌比3.49 = 发酵/修复阶段
    details['cycle_phase'] = '发酵'
    details['cycle_score'] = 7
    details['position_modifier'] = 1.0
    details['cycle_warning'] = '📊 04-17数据未入库，基于04-16评估：发酵期(涨跌比3.49，0跌停)'
    
    _cached_details = details
    
    if verbose:
        print("=" * 80)
        print(f"  ⚠️ 强制M3模式（04-17数据缺失修正）")
        print("=" * 80)
        print(f"  原始判定: {orig_mode}（因04-17零数据→退潮→M5）")
        print(f"  修正判定: M3 反弹修复（综合情绪{details['composite_emotion']}/10）")
        print(f"  情绪周期: 发酵期（04-16涨停50/跌停0/涨跌比3.49）")
        print(f"  策略: S1=观察池 S2=主力 S3=副策略")
        print(f"  板块淘汰: S2后40% S3后40%")
        print()
        
        for name, d in details.get('index_data', {}).items():
            bars = "█" * int(d['emotion']) + "░" * (10 - int(d['emotion']))
            streak_str = f"连{'涨' if d['streak'] > 0 else '跌'}{abs(d['streak'])}天"
            print(f"  {name}: {d['last']:.2f}  5日{d['pct5']:+.2f}%  [{bars}]{d['emotion']}/10  {streak_str}")
        print()
    
    return _forced_mode, _forced_config, details

def patched_get_params():
    if _cached_details is None:
        patched_detect(verbose=False)
    d = _cached_details
    return {
        'mode': _forced_mode,
        'composite_emotion': d.get('composite_emotion', 7),
        'strategies': _forced_config['strategies'],
        'sector_cutoff': _forced_config['sector_cutoff'],
        'position': _forced_config.get('position', {}),
        's3_x2_limit': _forced_config.get('s3_x2_limit', 25),
        's3_x2_relax': _forced_config.get('s3_x2_relax', {}),
        'cycle_phase': '发酵',
        'cycle_score': 7,
        'position_modifier': 1.0,
        'cycle_warning': d.get('cycle_warning', ''),
        'sector_rotation': d.get('cycle_data', {}).get('sector_rotation', {}),
    }

# 应用补丁
mm.detect_market_mode = patched_detect
mm.get_mode_params = patched_get_params

# 现在import offline_screener（它会调用patched版本）
print("=" * 80)
print("  收盘选股 · M3修正模式运行")
print("  注意：明天周六不开盘，候选票下周一(04-20)生效")
print("=" * 80)
print()

# 直接执行offline_screener的主逻辑
exec(open('offline_screener.py', encoding='utf-8').read())
