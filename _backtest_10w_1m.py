#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10万本金实盘模拟回测 — V3优化版策略
回测区间：近1个月（2026-03-10 ~ 2026-04-11）

V3优化（相对V2的改进）：
  ① 最低入选分数 score≥15（淘汰低分B级）
  ② 3连亏熔断：暂停开仓2天
  ③ 空头转多确认：空头结束后等1天再交易
  ④ 单只仓位上限 1/3→1/4（更分散）

仓位管理铁律：
  · 初始资金 100,000 元
  · 同时最多持有 2 只
  · 单只仓位不超过总资金的 1/4
  · 首仓最低 1/5 总资金
  · 每日最多 2 笔新仓
  · 收盘止损（D1盘中-3%硬止损, D1收盘-1.5%软止损, D2-2%止损）
  · D3强制清仓
  · B级优先（score≥15）
  · 大盘空头排列不开仓 + 空头转多需确认1天
  · 3连亏熔断暂停2天
  · 手续费：买入万2.5 + 卖出万2.5 + 印花税卖出千1（单边）
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

print("加载数据库数据...")
all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)

# ═══ 加载行业映射和板块数据 ═══
industry_map = {}
try:
    ind_df = pd.read_sql('SELECT code, industry FROM stock_industry', conn)
    industry_map = dict(zip(ind_df['code'], ind_df['industry']))
    print(f"行业映射: {len(industry_map)} 只股票")
except:
    print("警告: 无行业映射数据，板块过滤不生效")

sector_data = {}
try:
    sec_df = pd.read_sql('SELECT * FROM sector_daily ORDER BY industry, date', conn)
    for col in ['avg_pct', 'total_amount', 'avg_turn', 'top_gainer_pct']:
        sec_df[col] = pd.to_numeric(sec_df[col], errors='coerce')
    # 建立 (industry, date) → row 的快速查找
    for _, row in sec_df.iterrows():
        key = (row['industry'], row['date'])
        sector_data[key] = row
    # 按行业分组计算5日动量
    sector_momentum = {}  # (industry, date) → 5日平均涨幅
    for ind, grp in sec_df.groupby('industry'):
        grp = grp.sort_values('date').reset_index(drop=True)
        pcts = grp['avg_pct'].values
        dates = grp['date'].values
        for i in range(4, len(grp)):
            m5 = float(np.mean(pcts[i-4:i+1]))
            sector_momentum[(ind, dates[i])] = m5
    print(f"板块数据: {len(sec_df)} 条, 动量计算: {len(sector_momentum)} 条")
except:
    print("警告: 无板块统计数据，板块过滤不生效")

conn.close()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

# ════════════════════════════════════════════
# 大盘状态（用真实上证指数替代全市场平均）
# ════════════════════════════════════════════
idx_code = 'sh.000001'
idx_data = all_data[all_data['code'] == idx_code].sort_values('date').reset_index(drop=True)
if len(idx_data) >= 20:
    idx_cls = idx_data['close'].values
    idx_dates = idx_data['date'].values
    market_state = {}
    for i in range(20, len(idx_data)):
        ma5 = np.mean(idx_cls[i-4:i+1])
        ma10 = np.mean(idx_cls[i-9:i+1])
        ma20 = np.mean(idx_cls[i-19:i+1])
        ma60 = np.mean(idx_cls[max(0,i-59):i+1])
        bearish = ma5 < ma10 < ma20
        close_i = idx_cls[i]
        # ═══ V5: 大盘温度计 ═══
        # Level 3 热: 多头排列 + 站上MA20 → 全力出击
        # Level 2 暖: 站上MA20但非多头 → 正常操作
        # Level 1 凉: 跌破MA20但站在MA60上 → 轻仓谨慎
        # Level 0 冷: 空头排列 → 不交易
        if bearish:
            mkt_level = 0
        elif ma5 > ma10 > ma20 and close_i > ma20:
            mkt_level = 3
        elif close_i > ma20:
            mkt_level = 2
        else:
            mkt_level = 1
        market_state[idx_dates[i]] = {
            'bearish': bearish,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'close': close_i, 'level': mkt_level,
            'pct': float(idx_data.iloc[i]['pctChg']) if pd.notna(idx_data.iloc[i]['pctChg']) else 0,
        }
    lvl_counts = {}
    for v in market_state.values():
        lvl_counts[v['level']] = lvl_counts.get(v['level'], 0) + 1
    print(f"大盘状态: 使用上证指数(真实数据), {len(market_state)} 个交易日")
    print(f"  温度分布: 热L3={lvl_counts.get(3,0)}天 暖L2={lvl_counts.get(2,0)}天 凉L1={lvl_counts.get(1,0)}天 冷L0={lvl_counts.get(0,0)}天")
else:
    # fallback: 用全市场平均
    market_daily = all_data.groupby('date').agg(
        mkt_close=('close', 'mean'),
        mkt_pct=('pctChg', 'mean'),
    ).reset_index().sort_values('date').reset_index(drop=True)
    mkt_cls = market_daily['mkt_close'].values
    mkt_dates = market_daily['date'].values
    market_state = {}
    for i in range(20, len(market_daily)):
        ma5 = np.mean(mkt_cls[i-4:i+1])
        ma10 = np.mean(mkt_cls[i-9:i+1])
        ma20 = np.mean(mkt_cls[i-19:i+1])
        bearish = ma5 < ma10 < ma20
        market_state[mkt_dates[i]] = {
            'bearish': bearish,
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
            'pct': market_daily.iloc[i]['mkt_pct'],
        }
    print(f"大盘状态: fallback全市场平均, {len(market_state)} 个交易日")

# ════════════════════════════════════════════
# 股票数据
# ════════════════════════════════════════════
stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'):
        continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df) >= 60:
        stock_dict[code] = df

print(f"有效股票数: {len(stock_dict)}")

# ════════════════════════════════════════════
# 回测区间
# ════════════════════════════════════════════
BT_START = '2025-10-10'
BT_END   = '2026-04-10'
all_dates_list = sorted(all_data['date'].unique())
bt_dates = [d for d in all_dates_list if BT_START <= d <= BT_END]
print(f"回测范围: {bt_dates[0]} ~ {bt_dates[-1]}，共 {len(bt_dates)} 个交易日")

# ════════════════════════════════════════════
# V2终极版选股函数
# ════════════════════════════════════════════
def screen_v2(df, idx):
    """V2终极版：满分20, 缩量3, 下影2, A≥16/B≥10, 回撤-20~-5, 量比0.4-0.8"""
    if idx < 60:
        return None
    data = df.iloc[:idx+1]
    cls = data['close'].values; ops = data['open'].values
    his = data['high'].values; los = data['low'].values
    vols = data['volume'].values; turns = data['turn'].values
    pcts = data['pctChg'].values; amts = data['amount'].values
    n = len(data); last = cls[-1]

    # 前置过滤
    if last < 3 or last > 200: return None
    if np.mean(amts[-20:]) / 10000 < 1000: return None
    ma60 = np.mean(cls[-60:])
    if last <= ma60: return None
    c60 = cls[-60:]
    pct60 = (last - c60[0]) / c60[0] * 100
    if not (10 <= pct60 <= 60): return None
    max60 = np.max(c60)
    dd60 = (last - max60) / max60 * 100
    if not (-20 <= dd60 <= -5): return None
    if np.sum(pcts[-60:] >= 9.5) < 1: return None
    if np.any(pcts[-5:] < -5): return None
    if np.any(turns[-5:] > 8): return None

    ma5 = np.mean(cls[-5:]); ma10 = np.mean(cls[-10:]); ma20 = np.mean(cls[-20:])

    score = 0
    vol5 = np.mean(vols[-5:]); vol20 = np.mean(vols[-20:]); vol60 = np.mean(vols[-60:])
    vr520 = vol5/vol20 if vol20>0 else 999; vr560 = vol5/vol60 if vol60>0 else 999
    turn5 = np.mean(turns[-5:])
    vol_dec = vols[-1]<vols[-2]<vols[-3] if n>=3 else False
    floor_vol = vols[-1] <= np.min(vols[-60:])*1.2
    sc1 = sum([0.4<=vr520<=0.8, vr560<=0.7, turn5<=2, vol_dec, floor_vol])
    if sc1>=3: score+=3
    elif sc1>=1: score+=1

    rng5 = (np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
    cs = (np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
    sc2 = sum([rng5<=5, abs(cs)<=1, last>ma60])
    if sc2>=3: score+=4
    elif sc2>=2: score+=2

    ma_sp = (max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
    sc3 = sum([ma_sp<=3, last>ma60, ma5>ma10 or ma5/ma10>0.995])
    if sc3>=3: score+=4
    elif sc3>=2: score+=2

    bodies = np.abs(cls-ops)
    br = np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
    amp3 = np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
    pct_abs5 = np.mean(np.abs(pcts[-5:]))
    sc4 = sum([br<=0.5, amp3<=3, pct_abs5<=1.5])
    if sc4>=2: score+=3
    elif sc4>=1: score+=1

    lsb = sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0
              and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
    if lsb>=1: score+=2

    doji = sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/ops[i]*100<=0.5
              and abs(cls[i]-ops[i])>0
              and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
    if doji>=2: score+=2
    elif doji>=1: score+=1

    colors = ['R' if cls[i]>=ops[i] else 'G' for i in range(-5,0)]
    no3 = all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r = all(-2<=pcts[i]<=2 for i in range(-5,0))
    pct5s = np.sum(pcts[-5:])
    if no3 and pct5r and -2<=pct5s<=3: score+=2

    grade = 'A' if score>=16 else 'B' if score>=10 else 'C'
    if grade in ('A','B'):
        return {'score': score, 'grade': grade, 'price': last,
                'dd60': dd60, 'vr520': vr520, 'name': df.iloc[idx].get('code_name', '')}
    return None


# ════════════════════════════════════════════
# 手续费计算
# ════════════════════════════════════════════
FEE_BUY = 0.00025     # 买入佣金万2.5
FEE_SELL = 0.00025    # 卖出佣金万2.5
STAMP_TAX = 0.001     # 印花税卖出千1

def calc_fee_buy(amount):
    fee = amount * FEE_BUY
    return max(fee, 5.0)  # 最低5元

def calc_fee_sell(amount):
    commission = max(amount * FEE_SELL, 5.0)
    stamp = amount * STAMP_TAX
    return commission + stamp


# ════════════════════════════════════════════
# 真实资金模拟引擎
# ════════════════════════════════════════════
INIT_CAPITAL = 100000
MAX_HOLDINGS = 2          # 同时最多持有2只
MAX_NEW_PER_DAY = 1       # 每日最多1笔新仓
MIN_SCORE = 15            # V3: 最低入选分数（淘汰低分B级）
LOSS_STREAK_LIMIT = 3     # V3: 连亏熔断阈值
LOSS_STREAK_PAUSE = 2     # V3: 熔断暂停天数
BEARISH_COOLDOWN_DAYS = 1 # V3: 空头转多确认天数

# ═══ V6: 保守自适应仓位 ═══
# 教训: 40%胜率下1/2仓位是毒药, 永不超过1/3
# 弱市缩仓+只选A级, 强市稍放大但仍克制
POSITION_BY_LEVEL = {
    # level: (A级仓位, B级仓位, 最低分数)
    3: (1/3, 1/6, 15),   # 热: 稍大A仓,小B仓,勇而不莽
    2: (1/4, 1/8, 15),   # 暖: V3+基线,验证过的
    1: (1/5, 0,   16),   # 凉: 只做A级,缩减仓位
    0: (0,   0,   99),   # 冷: 不交易
}

capital = INIT_CAPITAL
holdings = {}  # code -> {buy_price, shares, buy_date, buy_day_idx, cost}
trade_log = []  # 所有交易记录
daily_equity = []  # 每日净值记录
skipped_bearish = 0
skipped_full = 0
skipped_no_money = 0
skipped_sector_total = 0  # V3+: 板块弱势跳过计数
skipped_streak = 0        # V3: 熔断跳过计数
skipped_cooldown = 0      # V3: 空头确认跳过计数
loss_streak = 0           # V3: 当前连亏计数
streak_pause_until = ''   # V3: 熔断暂停到哪天
prev_bearish = True       # V3: 上一日是否空头
bearish_end_date = ''     # V3: 空头结束日期


def get_stock_price(code, date, field='open'):
    """获取某股票某日的价格"""
    df = stock_dict.get(code)
    if df is None:
        return None
    dl = df['date'].values.tolist()
    if date not in dl:
        return None
    idx = dl.index(date)
    val = df.iloc[idx][field]
    if pd.isna(val) or val <= 0:
        return None
    return val


def get_stock_ohlc(code, date):
    """获取某股票某日的OHLC"""
    df = stock_dict.get(code)
    if df is None:
        return None
    dl = df['date'].values.tolist()
    if date not in dl:
        return None
    idx = dl.index(date)
    row = df.iloc[idx]
    o, h, l, c = row['open'], row['high'], row['low'], row['close']
    if any(pd.isna(x) or x <= 0 for x in [o, h, l, c]):
        return None
    return {'open': o, 'high': h, 'low': l, 'close': c}


def sell_stock(code, price, date, reason):
    """卖出"""
    global capital
    if code not in holdings:
        return
    h = holdings[code]
    sell_amount = price * h['shares']
    fee = calc_fee_sell(sell_amount)
    net = sell_amount - fee
    profit = net - h['cost']
    profit_pct = profit / h['cost'] * 100

    trade_log.append({
        'buy_date': h['buy_date'],
        'sell_date': date,
        'code': code,
        'grade': h.get('grade', '?'),
        'score': h.get('score', 0),
        'buy_price': h['buy_price'],
        'sell_price': price,
        'shares': h['shares'],
        'cost': h['cost'],
        'revenue': net,
        'profit': profit,
        'profit_pct': profit_pct,
        'fee_total': h['buy_fee'] + fee,
        'reason': reason,
        'hold_days': 0,  # will be filled later
    })

    capital += net
    del holdings[code]


def buy_stock(code, price, alloc_amount, date, grade, score):
    """买入"""
    global capital
    shares = int(alloc_amount / price / 100) * 100  # 整百股
    if shares < 100:
        return False
    actual_amount = shares * price
    fee = calc_fee_buy(actual_amount)
    total_cost = actual_amount + fee

    if total_cost > capital:
        return False

    capital -= total_cost
    holdings[code] = {
        'buy_price': price,
        'shares': shares,
        'buy_date': date,
        'cost': total_cost,
        'buy_fee': fee,
        'grade': grade,
        'score': score,
    }
    return True


# ════════════════════════════════════════════
# 主回测循环
# ════════════════════════════════════════════
print()
print("═" * 80)
print(f"  10万本金实盘模拟  V6保守自适应策略")
print(f"  回测区间: {bt_dates[0]} ~ {bt_dates[-1]}")
print(f"  初始资金: ¥{INIT_CAPITAL:,.0f}")
print("═" * 80)
print()

for di, today in enumerate(bt_dates):
    # ─── 1. 盘中处理持仓（止盈/止损） ───
    to_sell = []
    for code, h in list(holdings.items()):
        ohlc = get_stock_ohlc(code, today)
        if ohlc is None:
            continue

        bp = h['buy_price']
        buy_date_idx = bt_dates.index(h['buy_date']) if h['buy_date'] in bt_dates else -1
        if buy_date_idx < 0:
            continue
        days_held = di - buy_date_idx  # 0=买入日, 1=D2, 2=D3

        tp1 = bp * 1.03
        tp2 = bp * 1.05
        hard_sl = bp * 0.97   # -3%硬止损（保留原值，-2.5%反而更差）
        soft_sl = bp * 0.985  # -1.5%收盘软止损
        d2_sl = bp * 0.98     # D2 -2%止损
        breakeven_touch = bp * 1.02  # V3: 冲高回落保本触发线
        breakeven_exit = bp * 1.005  # V3: 保本出场线

        if days_held == 0:
            # D1: 买入日
            if ohlc['low'] <= hard_sl:
                to_sell.append((code, hard_sl, 'D1硬止损-3%'))
            elif ohlc['high'] >= tp2:
                to_sell.append((code, tp2, 'D1止盈+5%'))
            elif ohlc['high'] >= tp1:
                # 只能模拟全仓卖出（简化）, 这里保守按+3%卖
                to_sell.append((code, tp1, 'D1止盈+3%'))
            # V3: 冲高回落保本出（日内涨过+2%但收盘跌回+0.5%以下）
            elif ohlc['high'] >= breakeven_touch and ohlc['close'] < breakeven_exit:
                to_sell.append((code, ohlc['close'], f'D1冲高回落保本{(ohlc["close"]/bp-1)*100:+.1f}%'))
            elif ohlc['close'] < soft_sl:
                to_sell.append((code, ohlc['close'], f'D1收盘软止损{(ohlc["close"]/bp-1)*100:.1f}%'))

        elif days_held == 1:
            # D2
            if ohlc['low'] <= d2_sl:
                to_sell.append((code, d2_sl, 'D2止损-2%'))
            elif ohlc['high'] >= tp2:
                to_sell.append((code, tp2, 'D2止盈+5%'))
            elif ohlc['high'] >= tp1:
                to_sell.append((code, tp1, 'D2止盈+3%'))
            # V3: D2也支持冲高回落保本
            elif ohlc['high'] >= breakeven_touch and ohlc['close'] < breakeven_exit:
                to_sell.append((code, ohlc['close'], f'D2冲高回落保本{(ohlc["close"]/bp-1)*100:+.1f}%'))

        elif days_held >= 2:
            # D3: 强制清仓（开盘价出）
            to_sell.append((code, ohlc['open'], f'D3强制清仓{(ohlc["open"]/bp-1)*100:+.1f}%'))

    for code, price, reason in to_sell:
        if code in holdings:
            h = holdings[code]
            buy_date_idx = bt_dates.index(h['buy_date']) if h['buy_date'] in bt_dates else di
            hold_days = di - buy_date_idx
            sell_stock(code, price, today, reason)
            # 补充持有天数
            if trade_log:
                trade_log[-1]['hold_days'] = hold_days
                # V3: 连亏计数
                if trade_log[-1]['profit'] < 0:
                    loss_streak += 1
                    if loss_streak >= LOSS_STREAK_LIMIT:
                        # 触发熔断，暂停LOSS_STREAK_PAUSE天
                        pause_idx = min(di + LOSS_STREAK_PAUSE, len(bt_dates) - 1)
                        streak_pause_until = bt_dates[pause_idx]
                        loss_streak = 0  # 重置
                else:
                    loss_streak = 0  # 盈利重置

    # ─── 2. 盘后选股（为明天的买入做准备） ───
    # 如果是最后2天，不再选新股
    if di >= len(bt_dates) - 3:
        pass  # 不选股
    else:
        # 大盘过滤 + V5温度自适应
        ms = market_state.get(today)
        cur_bearish = ms['bearish'] if ms else False
        mkt_level = ms['level'] if ms else 0

        # V3: 追踪空头转多
        if prev_bearish and not cur_bearish:
            bearish_end_date = today  # 空头刚结束
        prev_bearish = cur_bearish

        # 用 can_trade 标志统一控制
        can_trade = True

        if mkt_level == 0:
            skipped_bearish += 1
            can_trade = False

        # V3: 空头转多确认期
        if can_trade and bearish_end_date:
            be_idx = bt_dates.index(bearish_end_date) if bearish_end_date in bt_dates else -1
            if be_idx >= 0 and (di - be_idx) < BEARISH_COOLDOWN_DAYS:
                skipped_cooldown += 1
                can_trade = False
            else:
                bearish_end_date = ''  # 确认期过了，清除

        # V3: 连亏熔断检查
        if can_trade and streak_pause_until and today < streak_pause_until:
            skipped_streak += 1
            can_trade = False

        if can_trade and len(holdings) >= MAX_HOLDINGS:
            skipped_full += 1
            can_trade = False

        if can_trade:
            # ═══ V5: 根据温度读取本轮参数 ═══
            lvl_cfg = POSITION_BY_LEVEL.get(mkt_level, (0, 0, 99))
            a_pct, b_pct, lvl_min_score = lvl_cfg

            # ═══ 板块动量排名（当日） ═══
            sector_rank = {}  # industry → percentile (0~1, 1=最强)
            if sector_momentum:
                today_moms = []
                for (ind, dt), m5 in sector_momentum.items():
                    if dt == today:
                        today_moms.append((ind, m5))
                if today_moms:
                    today_moms.sort(key=lambda x: x[1])
                    n_sec = len(today_moms)
                    for rank_i, (ind, _) in enumerate(today_moms):
                        sector_rank[ind] = rank_i / max(n_sec - 1, 1)

            # 选股
            selected = []
            skipped_sector = 0
            for code, df in stock_dict.items():
                if code in holdings:
                    continue
                dl = df['date'].values.tolist()
                if today not in dl:
                    continue
                idx = dl.index(today)
                r = screen_v2(df, idx)
                if r and r['score'] >= lvl_min_score:  # V5: 温度决定最低分数
                    r['code'] = code
                    # ═══ 板块过滤：跳过弱势板块（后30%） ═══
                    ind = industry_map.get(code, '')
                    r['industry'] = ind
                    sec_pct = sector_rank.get(ind, 0.5)  # 默认中间
                    r['sector_pct'] = sec_pct
                    if sec_pct < 0.3 and ind:  # 板块处于后30%，跳过
                        skipped_sector += 1
                        skipped_sector_total += 1
                        continue
                    # 板块加分：前30%强势板块的股票优先
                    r['sector_bonus'] = 1 if sec_pct >= 0.7 else 0
                    selected.append(r)

            # V3: A级优先（A级赚钱，B级减仓）+ 板块加分
            selected.sort(key=lambda x: (0 if x['grade']=='A' else 1, -x.get('sector_bonus',0), -x['score']))

            # 可开仓数
            avail_slots = MAX_HOLDINGS - len(holdings)
            can_buy = min(avail_slots, MAX_NEW_PER_DAY)
            selected = selected[:can_buy]

            # 明天买入（用明天开盘价）
            tomorrow_idx = di + 1
            if tomorrow_idx < len(bt_dates):
                buy_date = bt_dates[tomorrow_idx]
                new_bought = 0
                for s in selected:
                    if new_bought >= can_buy:
                        break

                    buy_price = get_stock_price(s['code'], buy_date, 'open')
                    if buy_price is None:
                        continue

                    # 计算可用资金和仓位
                    total_equity = capital + sum(
                        h['shares'] * (get_stock_price(c, today, 'close') or h['buy_price'])
                        for c, h in holdings.items()
                    )
                    # V5: 温度自适应仓位
                    grade_pct = a_pct if s['grade'] == 'A' else b_pct
                    if grade_pct <= 0:
                        continue  # 该温度下不买此级别
                    max_alloc = total_equity * grade_pct
                    min_alloc = total_equity * grade_pct * 0.7  # 最低仓位=目标的70%
                    alloc = min(max_alloc, capital * 0.98)  # 可近满仓

                    if alloc < min_alloc:
                        skipped_no_money += 1
                        continue

                    if buy_stock(s['code'], buy_price, alloc, buy_date, s['grade'], s['score']):
                        new_bought += 1

    # ─── 3. 记录每日净值 ───
    port_value = capital
    for code, h in holdings.items():
        close_p = get_stock_price(code, today, 'close')
        if close_p:
            port_value += close_p * h['shares']
        else:
            port_value += h['buy_price'] * h['shares']

    daily_equity.append({
        'date': today,
        'equity': port_value,
        'cash': capital,
        'n_holdings': len(holdings),
        'pct_return': (port_value / INIT_CAPITAL - 1) * 100,
    })


# ════════════════════════════════════════════
# 输出结果
# ════════════════════════════════════════════
final_equity = daily_equity[-1]['equity']
total_return = final_equity - INIT_CAPITAL
total_return_pct = total_return / INIT_CAPITAL * 100

print("═" * 80)
print("  📊 整体表现")
print("═" * 80)
print(f"  初始资金:     ¥{INIT_CAPITAL:>12,.0f}")
print(f"  最终净值:     ¥{final_equity:>12,.2f}")
print(f"  总盈亏:       ¥{total_return:>+12,.2f}")
print(f"  总收益率:     {total_return_pct:>+10.2f}%")
print(f"  总交易笔数:   {len(trade_log):>10d} 笔")

peak = INIT_CAPITAL; max_dd = 0; max_dd_pct = 0
for d in daily_equity:
    peak = max(peak, d['equity'])
    dd = d['equity'] - peak
    if dd < max_dd:
        max_dd = dd
        max_dd_pct = dd / peak * 100

print(f"  最大回撤:     ¥{max_dd:>+12,.2f} ({max_dd_pct:+.2f}%)")
print(f"  峰值净值:     ¥{peak:>12,.2f}")

# 胜率
if trade_log:
    wins = [t for t in trade_log if t['profit'] > 0]
    losses = [t for t in trade_log if t['profit'] <= 0]
    wr = len(wins) / len(trade_log) * 100
    avg_win = np.mean([t['profit'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['profit'] for t in losses]) if losses else 0
    total_fees = sum(t['fee_total'] for t in trade_log)

    print(f"  胜率:         {wr:>10.1f}% ({len(wins)}胜/{len(losses)}负)")
    print(f"  平均盈利:     ¥{avg_win:>+12,.2f}/笔")
    print(f"  平均亏损:     ¥{avg_loss:>+12,.2f}/笔")
    print(f"  盈亏比:       {abs(avg_win/avg_loss) if avg_loss != 0 else 999:>10.2f}")
    print(f"  总手续费:     ¥{total_fees:>12,.2f}")
    print(f"  跳过(空头):   {skipped_bearish:>10d} 天")
    print(f"  跳过(确认期): {skipped_cooldown:>10d} 天")
    print(f"  跳过(熔断):   {skipped_streak:>10d} 天")
    print(f"  跳过(满仓):   {skipped_full:>10d} 天")
    print(f"  跳过(没钱):   {skipped_no_money:>10d} 次")
    print(f"  跳过(弱板块): {skipped_sector_total:>10d} 只")

print()
print("═" * 80)
print("  📋 逐笔交易明细")
print("═" * 80)
print()
if trade_log:
    print(f"{'序号':>3s} {'买入日':>10s} {'卖出日':>10s} {'代码':<12s} {'级':>2s} {'分':>3s} {'买价':>7s} {'卖价':>7s} {'股数':>5s} {'盈亏':>9s} {'收益%':>7s} {'持有':>3s} {'出场原因'}")
    print("─" * 120)
    cum_profit = 0
    for i, t in enumerate(trade_log, 1):
        cum_profit += t['profit']
        print(f"{i:>3d} {t['buy_date']:>10s} {t['sell_date']:>10s} {t['code']:<12s} "
              f"{t['grade']:>2s} {t['score']:>3d} {t['buy_price']:>7.2f} {t['sell_price']:>7.2f} "
              f"{t['shares']:>5d} ¥{t['profit']:>+8.2f} {t['profit_pct']:>+6.2f}% "
              f"D{t['hold_days']+1:>1d} {t['reason']}")
    print("─" * 120)
    print(f"  累计盈亏: ¥{cum_profit:>+,.2f}  手续费合计: ¥{total_fees:>,.2f}  净盈亏: ¥{cum_profit:>+,.2f}")

print()
print("═" * 80)
print("  📅 按周汇总")
print("═" * 80)
print()
if trade_log:
    from collections import defaultdict
    weekly = defaultdict(list)
    for t in trade_log:
        # 用卖出日的周
        from datetime import datetime
        dt = datetime.strptime(t['sell_date'], '%Y-%m-%d')
        week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        weekly[week_key].append(t)

    print(f"{'周':>10s} {'笔数':>4s} {'胜/负':>5s} {'胜率':>6s} {'盈亏':>10s} {'累计':>10s}")
    print("─" * 60)
    cum = 0
    for week in sorted(weekly.keys()):
        trades_w = weekly[week]
        profits = [t['profit'] for t in trades_w]
        w = sum(1 for p in profits if p > 0)
        l = len(profits) - w
        wr = w / len(profits) * 100 if profits else 0
        week_pnl = sum(profits)
        cum += week_pnl
        bar = "█" * int(abs(week_pnl) / 200) if week_pnl >= 0 else "▒" * int(abs(week_pnl) / 200)
        sign = "+" if week_pnl >= 0 else ""
        print(f"  {week:>8s} {len(trades_w):>4d}  {w}/{l:<3d} {wr:>5.1f}% ¥{week_pnl:>+9,.2f} ¥{cum:>+9,.2f} {bar}")

print()
print("═" * 80)
print("  📈 每日净值曲线（文字版）")
print("═" * 80)
print()
if daily_equity:
    max_eq = max(d['equity'] for d in daily_equity)
    min_eq = min(d['equity'] for d in daily_equity)
    eq_range = max_eq - min_eq if max_eq != min_eq else 1

    for d in daily_equity:
        bar_len = int((d['equity'] - min_eq) / eq_range * 40)
        bar = "█" * bar_len
        hold_str = f"[持{d['n_holdings']}只]" if d['n_holdings'] > 0 else "[空仓]"
        print(f"  {d['date']} ¥{d['equity']:>10,.2f} {d['pct_return']:>+6.2f}% {hold_str} {bar}")

print()
print("═" * 80)
print("  💡 按出场原因统计")
print("═" * 80)
print()
if trade_log:
    from collections import Counter
    reason_stats = defaultdict(lambda: {'count': 0, 'profit': 0, 'wins': 0})
    for t in trade_log:
        r = t['reason']
        reason_stats[r]['count'] += 1
        reason_stats[r]['profit'] += t['profit']
        if t['profit'] > 0:
            reason_stats[r]['wins'] += 1

    print(f"{'出场原因':<30s} {'笔数':>4s} {'胜率':>6s} {'总盈亏':>10s} {'均盈亏':>9s}")
    print("─" * 70)
    for reason, stats in sorted(reason_stats.items(), key=lambda x: -x[1]['count']):
        wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        avg = stats['profit'] / stats['count']
        print(f"  {reason:<28s} {stats['count']:>4d}  {wr:>5.1f}% ¥{stats['profit']:>+9,.2f} ¥{avg:>+8,.2f}")

print()
print("═" * 80)
print("  ⚖️ 按评级统计")
print("═" * 80)
print()
if trade_log:
    for g in ['A', 'B']:
        gt = [t for t in trade_log if t['grade'] == g]
        if gt:
            gw = sum(1 for t in gt if t['profit'] > 0)
            gwr = gw / len(gt) * 100
            gp = sum(t['profit'] for t in gt)
            print(f"  {g}级: {len(gt)}笔  胜率{gwr:.1f}%  总盈亏¥{gp:>+,.2f}  均盈亏¥{gp/len(gt):>+,.2f}")

print()
print("═" * 80)
print("  🧠 策略诊断")
print("═" * 80)
print()

if trade_log:
    # 连续亏损分析
    max_consec_loss = 0; cur_consec = 0; max_consec_loss_amt = 0; cur_loss_amt = 0
    for t in trade_log:
        if t['profit'] <= 0:
            cur_consec += 1
            cur_loss_amt += t['profit']
            if cur_consec > max_consec_loss:
                max_consec_loss = cur_consec
                max_consec_loss_amt = cur_loss_amt
        else:
            cur_consec = 0; cur_loss_amt = 0

    print(f"  最大连续亏损: {max_consec_loss}笔 (¥{max_consec_loss_amt:+,.2f})")

    # 手续费占比
    if total_return != 0:
        fee_pct = total_fees / abs(total_return) * 100
        print(f"  手续费占盈亏比: {fee_pct:.1f}%")
    print(f"  日均持股: {np.mean([d['n_holdings'] for d in daily_equity]):.1f}只")
    print(f"  空仓天数: {sum(1 for d in daily_equity if d['n_holdings']==0)}天")
    print(f"  满仓天数: {sum(1 for d in daily_equity if d['n_holdings']>=MAX_HOLDINGS)}天")

    # 月化收益
    n_days = len(daily_equity)
    if n_days > 0:
        monthly_ret = total_return_pct / n_days * 22  # 约每月22个交易日
        annual_ret = monthly_ret * 12
        print(f"  月化收益率: {monthly_ret:+.2f}%")
        print(f"  年化收益率(估): {annual_ret:+.2f}%")

print()
print("  回测完成！以上结果基于历史数据，不代表未来收益。")
