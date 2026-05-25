#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测：2025年1-3月，按选股手册选股 + 短波策略止盈止损
逻辑：
  每个交易日用选股手册筛选蓄力候选（B级及以上），
  次日以开盘价买入，然后按操作手册短波规则执行：
    - 涨3%: 卖一半
    - 涨5%: 全清
    - 跌2%: 止损全清
    - 最多持有2个交易日，到期强制清仓
"""
import sys, os, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd
import numpy as np

init_db()
conn = get_connection()

# ═══════════════════════════════════════════════
# 获取数据
# ═══════════════════════════════════════════════
print("加载数据库数据...")
all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
conn.close()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

# 按股票分组
stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df) >= 60:
        stock_dict[code] = df

print(f"有效股票数: {len(stock_dict)}")

# 获取所有交易日（2025-01-01 ~ 2025-03-31）
all_dates = sorted(all_data[all_data['date'] >= '2025-01-01']['date'].unique())
bt_dates = [d for d in all_dates if '2025-01-01' <= d <= '2025-03-31']
print(f"回测日期范围: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 个交易日")

# ═══════════════════════════════════════════════
# 选股函数（完全按手册实现）
# ═══════════════════════════════════════════════
def screen_stock(df, idx):
    """在df的第idx行位置，用往前看的数据判断是否符合选股条件"""
    if idx < 60:
        return None

    data = df.iloc[:idx+1]
    cls = data['close'].values
    ops = data['open'].values
    his = data['high'].values
    los = data['low'].values
    vols = data['volume'].values
    turns = data['turn'].values
    pcts = data['pctChg'].values
    amts = data['amount'].values
    n = len(data)
    last = cls[-1]

    # 价格过滤
    if last < 3 or last > 200:
        return None
    # 日均成交额过滤（近20日）
    avg_amt_20 = np.mean(amts[-20:]) / 10000
    if avg_amt_20 < 1000:
        return None

    # === 第一关：前置过滤 ===
    ma60 = np.mean(cls[-60:])
    # F5: 现价 > MA60
    if last <= ma60:
        return None
    # F3: 近60日涨幅 10%~60%
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60):
        return None
    # F4: 高点回撤 15%~30%（放宽到5%~35%覆盖更多）
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-35 <= dd60 <= -5):
        return None
    # F2: 近60日至少1次涨停
    p60 = pcts[-60:]
    if np.sum(p60 >= 9.5) < 1:
        return None

    # 排除项
    if np.any(pcts[-5:] < -5):
        return None
    if np.any(turns[-5:] > 8):
        return None

    # === 第二关：7大核心指标 ===
    ma5 = np.mean(cls[-5:])
    ma10 = np.mean(cls[-10:])
    ma20 = np.mean(cls[-20:])

    score = 0

    # ① 缩量
    vol5 = np.mean(vols[-5:])
    vol20 = np.mean(vols[-20:])
    vol60 = np.mean(vols[-60:])
    vr520 = vol5 / vol20 if vol20 > 0 else 999
    vr560 = vol5 / vol60 if vol60 > 0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = (vols[-1] < vols[-2] < vols[-3]) if n >= 3 else False
    vol_min60 = np.min(vols[-60:])
    floor_vol = vols[-1] <= vol_min60 * 1.2
    sc1 = sum([vr520 <= 0.6, vr560 <= 0.5, turn5 <= 2, vol_dec, floor_vol])
    if sc1 >= 3:
        score += 5
    elif sc1 >= 1:
        score += 2

    # ② 横盘
    c5 = cls[-5:]
    rng5 = (np.max(c5) - np.min(c5)) / np.mean(c5) * 100
    cs = (np.mean(cls[-5:]) - np.mean(cls[-10:])) / np.mean(cls[-10:]) * 100
    sc2 = sum([rng5 <= 5, abs(cs) <= 1, last > ma60])
    if sc2 >= 3:
        score += 4
    elif sc2 >= 2:
        score += 2

    # ③ 均线粘合
    ma_sp = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ((ma5+ma10+ma20)/3) * 100
    sc3 = sum([ma_sp <= 3, last > ma60, ma5 > ma10 or ma5/ma10 > 0.995])
    if sc3 >= 3:
        score += 4
    elif sc3 >= 2:
        score += 2

    # ④ K线实体缩小
    bodies = np.abs(cls - ops)
    br = np.mean(bodies[-5:]) / np.mean(bodies[-20:]) if np.mean(bodies[-20:]) > 0 else 999
    amp3 = np.max((his[-3:] - los[-3:]) / cls[-4:-1] * 100) if n >= 4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br <= 0.5, amp3 <= 3, pct_abs5 <= 1.5])
    if sc4 >= 2:
        score += 3
    elif sc4 >= 1:
        score += 1

    # ⑤ 下影线阳线
    lsb = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o)
        ls_len = min(o, c) - l
        if c > o and body > 0 and ls_len >= 2 * body and pcts[i] <= 2:
            lsb += 1
    if lsb >= 1:
        score += 3

    # ⑥ 小十字星
    doji = 0
    for i in range(-5, 0):
        o, c, h, l = ops[i], cls[i], his[i], los[i]
        body = abs(c - o)
        bp = body / o * 100 if o > 0 else 999
        shadow = max(h - max(o, c), min(o, c) - l)
        if bp <= 0.5 and body > 0 and shadow >= 2 * body:
            doji += 1
    if doji >= 2:
        score += 2
    elif doji >= 1:
        score += 1

    # ⑦ 红绿交替
    colors = ['R' if cls[i] >= ops[i] else 'G' for i in range(-5, 0)]
    no3 = all(not (colors[i] == colors[i+1] == colors[i+2]) for i in range(3))
    pct5r = all(-2 <= pcts[i] <= 2 for i in range(-5, 0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2 <= pct5s <= 3:
        score += 2

    grade = 'A' if score >= 18 else 'B' if score >= 12 else 'C'
    if grade in ('A', 'B'):
        return {'score': score, 'grade': grade, 'price': last,
                'ma5': ma5, 'ma10': ma10, 'ma60': ma60}
    return None


# ═══════════════════════════════════════════════
# 短波回测（模拟盘中逐日判断）
# ═══════════════════════════════════════════════
print()
print("=" * 80)
print("  开始回测: 2025年1月-3月 选股手册 + 短波策略")
print("=" * 80)
print()

trades = []       # 所有已完成交易
hold_positions = []  # 当前持仓

# 每天最多开几个新仓
MAX_NEW_PER_DAY = 3

for di, scan_date in enumerate(bt_dates[:-3]):  # 留3天给持仓结束
    # 获取扫描日后面的交易日（用于模拟买入和持有）
    future_dates = [d for d in bt_dates if d > scan_date]
    if len(future_dates) < 3:
        continue

    buy_date = future_dates[0]   # D1 买入日
    hold_date = future_dates[1]  # D2 持有日
    exit_date = future_dates[2]  # D3 强制清仓日

    # 当天跑选股
    selected = []
    for code, df in stock_dict.items():
        date_list = df['date'].values.tolist()
        if scan_date not in date_list:
            continue
        idx = date_list.index(scan_date)
        result = screen_stock(df, idx)
        if result:
            result['code'] = code
            selected.append(result)

    # 按评分排序取top
    selected.sort(key=lambda x: x['score'], reverse=True)
    selected = selected[:MAX_NEW_PER_DAY]

    for s in selected:
        code = s['code']
        df = stock_dict[code]
        date_list = df['date'].values.tolist()

        # 检查buy_date, hold_date, exit_date 都有数据
        if buy_date not in date_list or hold_date not in date_list or exit_date not in date_list:
            continue

        buy_idx = date_list.index(buy_date)
        hold_idx = date_list.index(hold_date)
        exit_idx = date_list.index(exit_date)

        buy_price = df.iloc[buy_idx]['open']  # 次日开盘买入
        if buy_price <= 0 or np.isnan(buy_price):
            continue

        # 模拟持有过程
        # 先处理 D1（买入日）盘中: 看最高和最低
        d1_high = df.iloc[buy_idx]['high']
        d1_low = df.iloc[buy_idx]['low']
        d1_close = df.iloc[buy_idx]['close']

        d2_open = df.iloc[hold_idx]['open']
        d2_high = df.iloc[hold_idx]['high']
        d2_low = df.iloc[hold_idx]['low']
        d2_close = df.iloc[hold_idx]['close']

        d3_open = df.iloc[exit_idx]['open']
        d3_close = df.iloc[exit_idx]['close']

        tp1_price = buy_price * 1.03  # +3% 卖一半
        tp2_price = buy_price * 1.05  # +5% 全清
        sl_price = buy_price * 0.98    # -2% 止损

        # 模拟逐日处理
        remaining_pct = 1.0  # 1.0 = 满仓
        total_return = 0.0
        exit_reason = ""
        actual_exit_date = ""

        # === D1 当天 ===
        # 检查是否触发止损
        if d1_low <= sl_price and remaining_pct > 0:
            # 止损。保守假设以止损价成交
            total_return += remaining_pct * ((sl_price - buy_price) / buy_price * 100)
            remaining_pct = 0
            exit_reason = "D1止损-2%"
            actual_exit_date = buy_date
        else:
            # 检查是否触发止盈（用最高价判断）
            if d1_high >= tp2_price and remaining_pct > 0:
                # 先卖一半+3%，再全清+5%
                half = remaining_pct / 2
                total_return += half * 3.0  # 第一笔+3%
                total_return += half * 5.0  # 第二笔+5%
                remaining_pct = 0
                exit_reason = "D1止盈+5%"
                actual_exit_date = buy_date
            elif d1_high >= tp1_price and remaining_pct > 0:
                # 只触发+3%
                half = remaining_pct / 2
                total_return += half * 3.0
                remaining_pct -= half
                # 看收盘价决定剩余部分
                # D1 还没结束，继续判断

        # === D2 ===
        if remaining_pct > 0:
            # 用 D2 最低判断止损
            if d2_low <= sl_price:
                total_return += remaining_pct * ((sl_price - buy_price) / buy_price * 100)
                remaining_pct = 0
                exit_reason = ("D2止损" if not exit_reason else exit_reason + "+D2止损")
                actual_exit_date = hold_date
            else:
                if d2_high >= tp2_price:
                    if remaining_pct == 1.0:
                        half = 0.5
                        total_return += half * 3.0
                        total_return += half * 5.0
                    else:
                        total_return += remaining_pct * 5.0
                    remaining_pct = 0
                    exit_reason = ("D2止盈+5%" if not exit_reason else exit_reason + "+D2止盈+5%")
                    actual_exit_date = hold_date
                elif d2_high >= tp1_price and remaining_pct == 1.0:
                    half = 0.5
                    total_return += half * 3.0
                    remaining_pct -= half
                    exit_reason = "D2半仓+3%"

        # === D3 强制清仓 ===
        if remaining_pct > 0:
            # 以D3开盘价清仓
            d3_ret = (d3_open - buy_price) / buy_price * 100
            total_return += remaining_pct * d3_ret
            exit_reason = (exit_reason + "+D3清仓" if exit_reason else "D3强制清仓")
            actual_exit_date = exit_date
            remaining_pct = 0

        trades.append({
            'scan_date': scan_date,
            'buy_date': buy_date,
            'exit_date': actual_exit_date if actual_exit_date else exit_date,
            'code': code,
            'grade': s['grade'],
            'score': s['score'],
            'buy_price': buy_price,
            'return_pct': total_return,
            'exit_reason': exit_reason,
        })


# ═══════════════════════════════════════════════
# 统计结果
# ═══════════════════════════════════════════════
print(f"总交易笔数: {len(trades)}")
print()

if not trades:
    print("没有产生交易")
    sys.exit(0)

returns = [t['return_pct'] for t in trades]
wins = [r for r in returns if r > 0]
losses = [r for r in returns if r < 0]
evens = [r for r in returns if r == 0]

total_return = sum(returns)
avg_return = np.mean(returns)
win_rate = len(wins) / len(returns) * 100
avg_win = np.mean(wins) if wins else 0
avg_loss = np.mean(losses) if losses else 0
profit_factor = (sum(wins) / abs(sum(losses))) if losses else float('inf')

print("=" * 80)
print("  回测结果汇总: 2025年1-3月")
print("=" * 80)
print()
print(f"  总交易笔数:  {len(trades)}")
print(f"  盈利笔数:    {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
print(f"  亏损笔数:    {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
print(f"  平局笔数:    {len(evens)}")
print()
print(f"  总收益率:    {total_return:+.2f}%")
print(f"  平均每笔:    {avg_return:+.3f}%")
print(f"  平均盈利:    {avg_win:+.3f}%")
print(f"  平均亏损:    {avg_loss:+.3f}%")
print(f"  盈亏比:      {abs(avg_win/avg_loss):.2f}:1" if avg_loss != 0 else "  盈亏比: ∞")
print(f"  利润因子:    {profit_factor:.2f}" if profit_factor != float('inf') else "  利润因子: ∞")
print()

# 按月统计
print("─" * 60)
print("  按月统计")
print("─" * 60)
for m in ['2025-01', '2025-02', '2025-03']:
    mt = [t for t in trades if t['buy_date'].startswith(m)]
    if mt:
        mr = [t['return_pct'] for t in mt]
        mw = [r for r in mr if r > 0]
        print(f"  {m}: {len(mt)}笔 | 胜率{len(mw)/len(mt)*100:.0f}% | 总收益{sum(mr):+.2f}% | 均{np.mean(mr):+.3f}%")

# 按出场原因统计
print()
print("─" * 60)
print("  按出场方式统计")
print("─" * 60)
exit_stats = {}
for t in trades:
    key = t['exit_reason']
    if key not in exit_stats:
        exit_stats[key] = {'count': 0, 'total_ret': 0}
    exit_stats[key]['count'] += 1
    exit_stats[key]['total_ret'] += t['return_pct']

for reason, stats in sorted(exit_stats.items(), key=lambda x: -x[1]['count']):
    cnt = stats['count']
    ret = stats['total_ret']
    print(f"  {reason:<30s}: {cnt:>4d}笔 | 总收益{ret:>+8.2f}% | 均{ret/cnt:>+7.3f}%")

# 按评级统计
print()
print("─" * 60)
print("  按评级统计")
print("─" * 60)
for g in ['A', 'B']:
    gt = [t for t in trades if t['grade'] == g]
    if gt:
        gr = [t['return_pct'] for t in gt]
        gw = [r for r in gr if r > 0]
        print(f"  {g}级: {len(gt)}笔 | 胜率{len(gw)/len(gt)*100:.0f}% | 总收益{sum(gr):+.2f}% | 均{np.mean(gr):+.3f}%")

# 累计收益曲线（文字版）
print()
print("─" * 60)
print("  累计收益变化")
print("─" * 60)
cum = 0
week_data = {}
for t in sorted(trades, key=lambda x: x['buy_date']):
    cum += t['return_pct']
    week = t['buy_date'][:7]
    week_data[week] = cum

for w, c in week_data.items():
    bar = "█" * int(max(0, c / 2)) if c >= 0 else "▒" * int(abs(c / 2))
    sign = "+" if c >= 0 else ""
    print(f"  {w}: {sign}{c:.2f}% {bar}")

# 显示最后20笔明细
print()
print("─" * 60)
print("  最近20笔交易明细")
print("─" * 60)
print(f"  {'扫描日':>12s} {'买入日':>12s} {'代码':<12s} {'级':>2s} {'分':>3s} {'买入价':>8s} {'收益%':>8s} {'出场原因'}")
for t in sorted(trades, key=lambda x: x['buy_date'])[-20:]:
    print(f"  {t['scan_date']:>12s} {t['buy_date']:>12s} {t['code']:<12s} {t['grade']:>2s} {t['score']:>3d} {t['buy_price']:>8.2f} {t['return_pct']:>+7.3f}% {t['exit_reason']}")

print()

# 假设每笔投入10万，算实际金额
capital_per_trade = 100000
total_profit = sum(r / 100 * capital_per_trade for r in returns)
print(f"═══ 如果每笔投入10万元 ═══")
print(f"  总盈亏金额: {total_profit:>+,.0f} 元")
print(f"  平均每笔:   {total_profit/len(trades):>+,.0f} 元")
print(f"  最大单笔盈: {max(returns)/100*capital_per_trade:>+,.0f} 元 ({max(returns):+.2f}%)")
print(f"  最大单笔亏: {min(returns)/100*capital_per_trade:>+,.0f} 元 ({min(returns):+.2f}%)")
