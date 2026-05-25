import sys; sys.stdout.reconfigure(encoding='utf-8')
import sqlite3, os, numpy as np, pandas as pd

db = sqlite3.connect(os.path.join('数据缓存', 'stock_cache.db'))

ind_map = {}
try:
    rows = db.execute('SELECT code, industry FROM stock_industry').fetchall()
    ind_map = {r[0]:r[1] for r in rows}
except: pass

codes = [r[0] for r in db.execute('SELECT DISTINCT code FROM kline_daily').fetchall()]

sec_mom = {}
sec_df = pd.read_sql('SELECT industry, date, avg_pct FROM sector_daily ORDER BY industry, date', db)
for ind, grp in sec_df.groupby('industry'):
    vals = grp.sort_values('date')['avg_pct'].values
    if len(vals) >= 5:
        sec_mom[ind] = float(np.mean(vals[-5:]))
sorted_secs = sorted(sec_mom.items(), key=lambda x:x[1])
n_sec = len(sorted_secs)
sec_rank = {ind: i/max(n_sec-1,1) for i,(ind,_) in enumerate(sorted_secs)}

near_misses = []
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
    
    # F1
    latest_turn = df['turn'].values[-1]
    latest_amt = df['amount'].values[-1]
    if latest_turn and latest_turn > 0 and last > 0:
        float_mcap = (latest_amt / (last*100)) / (latest_turn/100) * 100 * last / 1e8
    else:
        continue
    if float_mcap < 30 or float_mcap > 300: continue
    
    # F2
    c60 = c[-60:]
    chgs = np.diff(c60)/c60[:-1]*100
    if not any(chgs >= 9.5): continue
    
    # F3
    g60 = (c[-1]/c[-60]-1)*100
    if g60 < 10 or g60 > 60: continue
    
    # F4
    h60 = max(h[-60:])
    db_pct = (c[-1]/h60-1)*100
    if db_pct < -20 or db_pct > -5: continue
    
    # F5
    ma60 = np.mean(c[-60:])
    if c[-1] <= ma60: continue
    
    # Sector
    ind = ind_map.get(code, '')
    rank = sec_rank.get(ind, 0.5)
    if rank < 0.3: continue
    
    # Score
    score = 0
    v5 = np.mean(v[-5:]); v20 = np.mean(v[-20:])
    vr = v5/v20 if v20 > 0 else 999
    t5 = np.mean(df['turn'].values[-5:])
    
    vol_pts = 0
    if 0.4 <= vr <= 0.8: vol_pts += 1
    if t5 <= 2: vol_pts += 1
    if v[-1] < v[-2] < v[-3]: vol_pts += 1
    score += min(vol_pts, 3)
    
    r5 = (max(h[-5:])-min(l[-5:]))/np.mean(c[-5:])*100
    cg_shift = abs(np.mean(c[-5:])/np.mean(c[-10:])-1)*100
    hp_pts = 0
    if r5 <= 5: hp_pts += 2
    elif r5 <= 8: hp_pts += 1
    if cg_shift <= 1: hp_pts += 1
    if c[-1] > ma60: hp_pts += 1
    score += min(hp_pts, 4)
    
    ma5 = np.mean(c[-5:]); ma10 = np.mean(c[-10:]); ma20 = np.mean(c[-20:])
    gap = (max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/np.mean([ma5,ma10,ma20])*100
    ma_pts = 0
    if gap <= 3: ma_pts += 2
    elif gap <= 5: ma_pts += 1
    if c[-1] > ma60: ma_pts += 1
    if ma5 >= ma10 or (len(c)>6 and np.mean(c[-5:]) > np.mean(c[-6:-1])): ma_pts += 1
    score += min(ma_pts, 4)
    
    bodies5 = np.abs(c[-5:]-o[-5:])
    bodies20 = np.abs(c[-20:]-o[-20:])
    br = np.mean(bodies5)/max(np.mean(bodies20),0.01)
    daily_chgs = np.abs(np.diff(c[-6:])/c[-6:-1]*100)
    amp5 = max(daily_chgs)
    avg_chg5 = np.mean(daily_chgs)
    entity_pts = 0
    if br <= 0.5: entity_pts += 2
    elif br <= 0.8: entity_pts += 1
    if amp5 <= 3: entity_pts += 1
    score += min(entity_pts, 3)
    
    bonus = 0
    for i in range(-5, 0):
        if c[i] > o[i] and (o[i]-l[i]) >= 2*max(c[i]-o[i],0.01):
            bonus += 1; break
    for i in range(-5, 0):
        if abs(c[i]-o[i])/max(o[i],1)*100 <= 0.5:
            bonus += 1; break
    colors = ['R' if c[i]>=o[i] else 'G' for i in range(-5,0)]
    has_3same = any(colors[j]==colors[j+1]==colors[j+2] for j in range(len(colors)-2))
    if not has_3same: bonus += 1
    score += min(bonus, 6)
    
    if score >= 13:
        sec_tag = '🔥' if rank >= 0.7 else ''
        near_misses.append((code, ind, last, score, vr, t5, r5, gap, g60, db_pct, float_mcap, sec_tag))

near_misses.sort(key=lambda x: -x[3])
print(f'评分>=13的候选: {len(near_misses)}只')
print()
for i, (code, ind, px, sc, vr, t5, r5, gap, g60, _db, mcap, stag) in enumerate(near_misses[:30]):
    grade = 'A' if sc>=16 else ('B15' if sc==15 else f'{sc}分')
    print(f'{i+1:2d}. {code:12s} {ind[:18]:18s} 价:{px:7.2f} 评分:{sc:2d}/20({grade:3s}) {stag} 量比:{vr:.2f} 换手:{t5:.1f}% 5日幅:{r5:.1f}% 线距:{gap:.1f}% 60日涨:{g60:+.0f}% 回撤:{_db:+.1f}% 市值:{mcap:.0f}亿')
conn = sqlite3.connect(os.path.join('数据缓存', 'stock_cache.db'))
conn.close()
