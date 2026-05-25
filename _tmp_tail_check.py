#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时脚本：尾盘候选实时确认"""
import sys, requests
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

def fetch_qq(syms_str):
    resp = requests.get(f"https://qt.gtimg.cn/q={syms_str}", timeout=10)
    resp.encoding = 'gbk'
    return resp.text

def parse_batch(text):
    results = {}
    for line in text.strip().split('\n'):
        if '="' not in line or '~' not in line:
            continue
        try:
            idx = line.index('="') + 2
            items = line[idx:].rstrip('";').split('~')
            if len(items) < 45:
                continue
            def sf(i):
                try: return float(items[i])
                except: return 0.0
            key = line.split('=')[0].replace('v_','').replace('pv_','').strip()
            results[key] = {
                'name': items[1], 'code': items[2],
                'cur': sf(3), 'pre': sf(4), 'open': sf(5),
                'hi': sf(33), 'lo': sf(34),
                'pct': sf(32), 'chg': sf(31),
                'vol': sf(36), 'amt': sf(37), 'turn': sf(38),
                'outer': sf(7), 'inner': sf(8),
                'upd': items[30] if len(items) > 30 else '',
            }
        except:
            continue
    return results

# S2 候选（offline_screener输出）
s2_list = [
    ('sz002158', 'S2-A8', '龙头', 'C34通用设备'),
    ('sz002528', 'S2-A8', '龙头', 'C39电子'),
    ('sh600232', 'S2-A7', '龙头', 'C17纺织'),
    ('sh605005', 'S2-A7', '龙头', 'C36汽车'),
    ('sh605318', 'S2-A7', '龙头', 'C41其他制造'),
    ('sz002023', 'S2-A7', '龙头', 'C43金属维修'),
    ('sz002066', 'S2-A7', '龙头', 'C30非金属'),
    ('sz002283', 'S2-A7', '龙头', 'C36汽车'),
    ('sh600382', 'S2-A7', '跟风', 'B08黑色金属'),
    ('sh600810', 'S2-A7', '跟风', 'C28化纤'),
    ('sh603203', 'S2-A7', '跟风', 'C35专用设备'),
    ('sh603701', 'S2-A7', '补涨', 'C38电气'),
    ('sz002576', 'S2-A7', '跟风', 'C38电气'),
    ('sz000049', 'S2-B6', '龙头', 'C38电气'),
    ('sh605305', 'S2-B6', '跟风', 'C35专用设备'),
    ('sh600114', 'S2-B6', '龙头', 'C33金属制品'),
    ('sh603058', 'S2-B6', '龙头', 'C23印刷'),
    ('sz002838', 'S2-B6', '龙头', 'C29橡塑'),
]

# S3 候选
s3_list = [
    ('sh600234', 'S3-A5', '龙头', 'E50建筑装饰'),
    ('sz002290', 'S3-A5', '龙头', 'C38电气'),
    ('sh600773', 'S3-B4', '龙头', 'K70房地产'),
    ('sh601339', 'S3-B4', '龙头', 'C17纺织'),
    ('sh603958', 'S3-B4', '龙头', 'C19皮革'),
    ('sz002176', 'S3-B4', '龙头', 'C32有色'),
]

# 拉取数据
all_syms = ['sh000001','sz399001','sz399006'] + [s[0] for s in s2_list] + [s[0] for s in s3_list]
syms_str = ','.join(all_syms)
data = parse_batch(fetch_qq(syms_str))

print("="*90)
print("  尾盘实时行情快照 + 三项确认")
print("="*90)

# 大盘
print("\n【大盘指数】")
mkt_ok = True
sh_pct = 0
for sym, label in [('sh000001','上证'), ('sz399001','深证'), ('sz399006','创业板')]:
    d = data.get(sym)
    if d:
        print(f"  {label}: {d['cur']:.2f}  {d['pct']:+.2f}%  高{d['hi']:.2f} 低{d['lo']:.2f}  更新:{d['upd']}")
        if sym == 'sh000001':
            sh_pct = d['pct']
if sh_pct <= -2.0:
    print("  ⛔ 大盘跌>2%，不建议开仓！")
    mkt_ok = False
elif sh_pct <= -1.0:
    print("  ⚠️ 大盘偏弱，轻仓或观望")

# 尾盘三项确认函数
def tail_check(sym, d, strategy, grade, tier, sector):
    """
    尾盘三项确认：
    1. 今日量能变化：换手率是否正常（不能突然放巨量或极度缩量）
    2. 收盘位置：当前价在日内K线的位置（>50%为强势收盘）
    3. 板块同步：涨跌方向与大盘是否一致
    """
    checks = []
    flags = []
    
    # 1. 量能变化
    turn = d['turn']
    if 0.5 <= turn <= 5.0:
        checks.append("✅量能正常")
        flags.append(True)
    elif turn > 5.0:
        checks.append(f"⚠️换手{turn:.1f}%偏高")
        flags.append(False)
    elif turn > 0:
        checks.append(f"⚠️换手{turn:.1f}%偏低")
        flags.append(False)
    else:
        checks.append("❌无量能数据")
        flags.append(False)
    
    # 2. 收盘位置
    rng = d['hi'] - d['lo']
    if rng > 0:
        close_pos = (d['cur'] - d['lo']) / rng * 100
    else:
        close_pos = 50
    if close_pos >= 60:
        checks.append(f"✅收盘强势({close_pos:.0f}%)")
        flags.append(True)
    elif close_pos >= 40:
        checks.append(f"⚠️收盘中位({close_pos:.0f}%)")
        flags.append(True)  # 中位可接受
    else:
        checks.append(f"❌收盘偏弱({close_pos:.0f}%)")
        flags.append(False)
    
    # 3. 板块同步（用涨跌方向判断）
    pct = d['pct']
    if pct >= 0:
        checks.append(f"✅今日涨{pct:+.2f}%")
        flags.append(True)
    elif pct >= -1.5:
        checks.append(f"⚠️微跌{pct:+.2f}%")
        flags.append(True)  # 微跌可接受
    else:
        checks.append(f"❌跌幅较大{pct:+.2f}%")
        flags.append(False)
    
    # 外内盘
    oi = d['outer'] / max(d['inner'], 1)
    oi_str = f"外/内{oi:.2f}"
    if oi >= 1.1:
        oi_str += "🟢"
    elif oi >= 0.9:
        oi_str += "⚪"
    else:
        oi_str += "🔴"
    
    from_hi = (d['hi'] - d['cur']) / max(d['hi'], 0.01) * 100
    
    passed = sum(flags) >= 2  # 3项中至少2项通过
    status = "✅通过" if passed else "❌不通过"
    
    return {
        'sym': sym, 'name': d['name'], 'strategy': strategy, 'grade': grade,
        'tier': tier, 'sector': sector,
        'cur': d['cur'], 'pct': pct, 'turn': turn,
        'close_pos': close_pos, 'from_hi': from_hi, 'oi': oi,
        'amt_yi': d['amt'] / 10000,
        'checks': checks, 'passed': passed, 'status': status,
        'oi_str': oi_str, 'flags': flags,
    }

# 处理所有候选
print("\n" + "="*90)
print("  【S2 大阳后缩量横盘 - 尾盘确认】（M3主力策略）")
print("="*90)

s2_results = []
for sym, grade, tier, sector in s2_list:
    d = data.get(sym)
    if d and d['cur'] > 0:
        r = tail_check(sym, d, 'S2', grade, tier, sector)
        s2_results.append(r)
        mark = r['status']
        print(f"\n  {mark} {sym} {d['name']}  {grade} {tier} [{sector}]")
        print(f"     现价:{r['cur']:.2f} 涨跌:{r['pct']:+.2f}% 换手:{r['turn']:.2f}% "
              f"成交:{r['amt_yi']:.2f}亿 {r['oi_str']} 距高:{r['from_hi']:.1f}% 收盘位:{r['close_pos']:.0f}%")
        print(f"     三项确认: {' | '.join(r['checks'])}")

print("\n" + "="*90)
print("  【S3 放量突破新高 - 尾盘确认】（M3辅助策略）")
print("="*90)

s3_results = []
for sym, grade, tier, sector in s3_list:
    d = data.get(sym)
    if d and d['cur'] > 0:
        r = tail_check(sym, d, 'S3', grade, tier, sector)
        s3_results.append(r)
        mark = r['status']
        print(f"\n  {mark} {sym} {d['name']}  {grade} {tier} [{sector}]")
        print(f"     现价:{r['cur']:.2f} 涨跌:{r['pct']:+.2f}% 换手:{r['turn']:.2f}% "
              f"成交:{r['amt_yi']:.2f}亿 {r['oi_str']} 距高:{r['from_hi']:.1f}% 收盘位:{r['close_pos']:.0f}%")
        print(f"     三项确认: {' | '.join(r['checks'])}")

# 汇总
print("\n" + "="*90)
print("  【汇总：达标+尾盘确认通过】")
print("="*90)

all_pass = [r for r in s2_results + s3_results if r['passed']]
all_fail = [r for r in s2_results + s3_results if not r['passed']]

# 同行业去重：取评分最高
from collections import defaultdict
sector_best = defaultdict(list)
for r in all_pass:
    sector_best[r['sector']].append(r)

final = []
for sector, rs in sector_best.items():
    rs.sort(key=lambda x: x['grade'], reverse=True)
    final.append(rs[0])

final.sort(key=lambda x: x['grade'], reverse=True)

if final:
    print(f"\n  推荐 {len(final)} 只（同行业去重后）：\n")
    print(f"  {'策略':<6} {'代码':<12} {'名称':<8} {'评级':<8} {'梯队':<6} {'板块':<14} "
          f"{'现价':>8} {'涨跌':>8} {'换手':>6} {'成交额':>8} {'收盘位':>6} {'外/内':>6}")
    print("  " + "-"*105)
    for r in final:
        print(f"  {r['strategy']:<6} {r['sym']:<12} {r['name']:<8} {r['grade']:<8} {r['tier']:<6} {r['sector']:<14} "
              f"{r['cur']:>8.2f} {r['pct']:>+7.2f}% {r['turn']:>5.2f}% {r['amt_yi']:>7.2f}亿 {r['close_pos']:>5.0f}% {r['oi']:>5.2f}")
else:
    print("\n  ⚠️ 无候选通过尾盘三项确认")

if all_fail:
    print(f"\n  不达标 {len(all_fail)} 只：")
    for r in all_fail:
        fail_reasons = [c for c, f in zip(r['checks'], r['flags']) if not f]
        print(f"  ❌ {r['sym']} {r['name']} {r['strategy']} | 原因: {', '.join(fail_reasons)}")

print("\n" + "="*90)
print("  注意：尾盘模式仓位减半（A级→1/8，B级→1/16）")
print("="*90)
