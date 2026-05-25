import sys; sys.stdout.reconfigure(encoding='utf-8')
import sqlite3, os, numpy as np, pandas as pd

db = sqlite3.connect(os.path.join('数据缓存', 'stock_cache.db'))

codes = [r[0] for r in db.execute('SELECT DISTINCT code FROM kline_daily').fetchall()]
stats = {'total': 0, 'extended': 0, 'bounced_strong': 0, 'consolidating': 0, 'trend_pullback': 0}

trend_pullbacks = []
breakout_candidates = []
momentum_leaders = []

ind_map = {}
try:
    rows = db.execute('SELECT code, industry FROM stock_industry').fetchall()
    ind_map = {r[0]:r[1] for r in rows}
except: pass

sec_mom = {}
sec_df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY industry, date', db)
for ind, grp in sec_df.groupby('industry'):
    vals = grp.sort_values('date')['avg_pct'].values
    if len(vals) >= 5:
        sec_mom[ind] = float(np.mean(vals[-5:]))
sorted_secs = sorted(sec_mom.items(), key=lambda x:x[1])
n_sec = len(sorted_secs)
sec_rank = {ind: i/max(n_sec-1,1) for i,(ind,_) in enumerate(sorted_secs)}

for code in codes:
    if code.startswith('sh.000') or code.startswith('sz.399'): continue
    df = pd.read_sql('SELECT * FROM kline_daily WHERE code=? ORDER BY date', db, params=[code])
    if len(df) < 60: continue
    for col in ['open','high','low','close','volume','amount','turn']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['close','volume'])
    if len(df) < 60: continue
    
    c = df['close'].values
    v = df['volume'].values
    h = df['high'].values
    l = df['low'].values
    o = df['open'].values
    last = c[-1]
    if last < 3 or last > 200: continue
    
    t = df['turn'].values[-1]
    amt = df['amount'].values[-1]
    if t and t > 0 and last > 0:
        mcap = (amt / (last*100)) / (t/100) * 100 * last / 1e8
    else: continue
    if mcap < 30 or mcap > 300: continue
    
    stats['total'] += 1
    
    ma5 = np.mean(c[-5:])
    ma10 = np.mean(c[-10:])
    ma20 = np.mean(c[-20:])
    ma60 = np.mean(c[-60:])
    
    chg5 = (c[-1]/c[-6]-1)*100
    v5 = np.mean(v[-5:])
    v20 = np.mean(v[-20:])
    vr = v5/v20 if v20 > 0 else 0
    h60 = max(h[-60:])
    drawback = (c[-1]/h60-1)*100
    
    ind = ind_map.get(code, '')
    srank = sec_rank.get(ind, 0.5)
    
    if chg5 > 8: stats['extended'] += 1
    elif chg5 > 3 and c[-1] > ma5 > ma10: stats['bounced_strong'] += 1
    elif abs(chg5) <= 3 and (max(h[-5:])-min(l[-5:]))/np.mean(c[-5:])*100 <= 5:
        stats['consolidating'] += 1
    
    # === 策略A：强势趋势回踩 ===
    chg20 = (c[-1]/c[-21]-1)*100 if len(c) >= 21 else 0
    if (ma5 > ma10 > ma20 and c[-1] > ma60 and 
        abs(c[-1]/ma5-1)*100 <= 2 and
        chg5 <= 2 and chg5 >= -3 and
        chg20 >= 10 and
        vr <= 1.0 and srank >= 0.3):
        stats['trend_pullback'] += 1
        trend_pullbacks.append((code, ind, last, chg5, vr, srank, mcap, chg20, drawback))
    
    # === 策略B：放量突破20日高点 ===
    h20 = max(h[-21:-1]) if len(h) >= 21 else max(h[-11:-1])
    if (c[-1] > h20 and v[-1] > v20 * 1.5 and
        c[-1] > ma20 > ma60 and c[-1] > o[-1] and srank >= 0.5):
        breakout_candidates.append((code, ind, last, (c[-1]/h20-1)*100,
                                    v[-1]/v20, srank, mcap, chg5))
    
    # === 策略C：大阳线后缩量横盘 ===
    dates = df['date'].values
    idx_0408 = None
    for i, d in enumerate(dates):
        if d == '2026-04-08':
            idx_0408 = i; break
    if idx_0408 is not None and idx_0408 > 0 and idx_0408 < len(c):
        chg_0408 = (c[idx_0408]/c[idx_0408-1]-1)*100
        if (chg_0408 >= 4 and c[-1] > ma60 and
            len(c) > idx_0408 + 1 and
            np.mean(v[-3:]) < v[idx_0408] * 0.7 and
            c[-1] >= c[idx_0408] * 0.97 and
            mcap >= 30 and srank >= 0.3):
            momentum_leaders.append((code, ind, last, chg_0408,
                                    np.mean(v[-3:])/v[idx_0408],
                                    srank, mcap, chg5))

trend_pullbacks.sort(key=lambda x: (-x[5], -x[7]))
breakout_candidates.sort(key=lambda x: (-x[5], -x[4]))
momentum_leaders.sort(key=lambda x: (-x[5], x[4]))

print('='*70)
print('  当前市场结构分析 (30-300亿流通市值)')
print('='*70)
print(f'  有效股票: {stats["total"]}只')
print(f'  5日涨>8%(已走远): {stats["extended"]}只')
print(f'  5日强势反弹中: {stats["bounced_strong"]}只')
print(f'  窄幅横盘中: {stats["consolidating"]}只')
print(f'  趋势回踩候选: {stats["trend_pullback"]}只')
print()

print('='*70)
print(f'  策略A: 强势趋势回踩 ({len(trend_pullbacks)}只)')
print('  MA5>10>20, 回踩MA5附近, 缩量, 20日涨>10%')
print('='*70)
for i, (code,ind,px,c5,vr,sr,mc,c20,dbk) in enumerate(trend_pullbacks[:15]):
    stag = 'TOP' if sr >= 0.7 else ''
    print(f'  {i+1:2d}. {code} {ind[:16]:16s} {stag:3s} px:{px:7.2f} 5d:{c5:+.1f}% 20d:{c20:+.1f}% vr:{vr:.2f} mcap:{mc:.0f}亿')
print()

print('='*70)
print(f'  策略B: 放量突破20日高点 ({len(breakout_candidates)}只)')
print('  今日突破+放量>1.5x+收阳+板块中上')
print('='*70)
for i, (code,ind,px,brk,vr,sr,mc,c5) in enumerate(breakout_candidates[:15]):
    stag = 'TOP' if sr >= 0.7 else ''
    print(f'  {i+1:2d}. {code} {ind[:16]:16s} {stag:3s} px:{px:7.2f} brk:{brk:+.1f}% vol_r:{vr:.1f}x 5d:{c5:+.1f}% mcap:{mc:.0f}亿')
print()

print('='*70)
print(f'  策略C: 4/8大阳后缩量横盘 ({len(momentum_leaders)}只)')
print('  4/8涨>=4%, 之后缩量<70%, 没跌回, 板块不弱')
print('='*70)
for i, (code,ind,px,c08,vr,sr,mc,c5) in enumerate(momentum_leaders[:15]):
    stag = 'TOP' if sr >= 0.7 else ''
    print(f'  {i+1:2d}. {code} {ind[:16]:16s} {stag:3s} px:{px:7.2f} 4/8:{c08:+.1f}% v_shrk:{vr:.2f} 5d:{c5:+.1f}% mcap:{mc:.0f}亿')

conn2 = sqlite3.connect(os.path.join('数据缓存', 'stock_cache.db'))
conn2.close()
