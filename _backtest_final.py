#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终版回测：组合验证最佳改动
基于消融实验结论，保留有效改动，回撤过度优化
"""
import sys, os, warnings
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
from db_cache import get_connection, init_db
import pandas as pd, numpy as np

init_db()
conn = get_connection()
print("加载数据...")
all_data = pd.read_sql('SELECT * FROM kline_daily ORDER BY code, date', conn)
conn.close()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    all_data[c] = pd.to_numeric(all_data[c], errors='coerce')

market_daily = all_data.groupby('date').agg(mkt_close=('close','mean')).reset_index().sort_values('date').reset_index(drop=True)
mkt_cls = market_daily['mkt_close'].values; mkt_dates = market_daily['date'].values
market_state = {}
for i in range(20, len(market_daily)):
    ma5=np.mean(mkt_cls[i-4:i+1]); ma10=np.mean(mkt_cls[i-9:i+1]); ma20=np.mean(mkt_cls[i-19:i+1])
    market_state[mkt_dates[i]] = {'bearish': ma5<ma10<ma20, 'ma5_lt_ma10': ma5<ma10}

stock_dict = {}
for code, group in all_data.groupby('code'):
    if code.startswith('sh.000') or code.startswith('sz.399'): continue
    df = group.dropna(subset=['close','volume']).reset_index(drop=True)
    if len(df)>=60: stock_dict[code]=df
print(f"股票数: {len(stock_dict)}")

BT_START='2025-10-01'; BT_END='2026-04-08'
all_dates_list=sorted(all_data['date'].unique())
bt_dates=[d for d in all_dates_list if BT_START<=d<=BT_END]
print(f"范围: {bt_dates[0]}~{bt_dates[-1]}, {len(bt_dates)}天")


def screen_final(df, idx):
    """最终版选股：保留原始23分体系 + 回撤收紧到5-20%"""
    if idx<60: return None
    data=df.iloc[:idx+1]
    cls=data['close'].values; ops=data['open'].values
    his=data['high'].values; los=data['low'].values
    vols=data['volume'].values; turns=data['turn'].values
    pcts=data['pctChg'].values; amts=data['amount'].values
    n=len(data); last=cls[-1]

    if last<3 or last>200: return None
    if np.mean(amts[-20:])/10000<1000: return None
    ma60=np.mean(cls[-60:])
    if last<=ma60: return None
    c60=cls[-60:]
    pct60=(last-c60[0])/c60[0]*100
    if not(10<=pct60<=60): return None
    max60=np.max(c60)
    dd60=(last-max60)/max60*100
    if not(-20<=dd60<=-5): return None  # 收紧回撤
    if np.sum(pcts[-60:]>=9.5)<1: return None
    if np.any(pcts[-5:]<-5): return None
    if np.any(turns[-5:]>8): return None

    ma5=np.mean(cls[-5:]); ma10=np.mean(cls[-10:]); ma20=np.mean(cls[-20:])
    score=0

    # ① 缩量 5分（保留原权重，回测显示原体系总收益更高）
    vol5=np.mean(vols[-5:]); vol20=np.mean(vols[-20:]); vol60=np.mean(vols[-60:])
    vr520=vol5/vol20 if vol20>0 else 999; vr560=vol5/vol60 if vol60>0 else 999
    turn5=np.mean(turns[-5:])
    vol_dec=vols[-1]<vols[-2]<vols[-3] if n>=3 else False
    floor_vol=vols[-1]<=np.min(vols[-60:])*1.2
    sc1=sum([vr520<=0.6, vr560<=0.5, turn5<=2, vol_dec, floor_vol])
    if sc1>=3: score+=5
    elif sc1>=1: score+=2

    # ② 横盘 4分
    rng5=(np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
    cs=(np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
    sc2=sum([rng5<=5, abs(cs)<=1, last>ma60])
    if sc2>=3: score+=4
    elif sc2>=2: score+=2

    # ③ 均线 4分
    ma_sp=(max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
    sc3=sum([ma_sp<=3, last>ma60, ma5>ma10 or ma5/ma10>0.995])
    if sc3>=3: score+=4
    elif sc3>=2: score+=2

    # ④ 实体 3分
    bodies=np.abs(cls-ops)
    br=np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
    amp3=np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
    pct_abs5=np.mean(np.abs(pcts[-5:]))
    sc4=sum([br<=0.5, amp3<=3, pct_abs5<=1.5])
    if sc4>=2: score+=3
    elif sc4>=1: score+=1

    # ⑤ 下影线 3分（保留原权重）
    lsb=sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0 and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
    if lsb>=1: score+=3

    # ⑥ 十字 2分
    doji=sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/ops[i]*100<=0.5 and abs(cls[i]-ops[i])>0 and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
    if doji>=2: score+=2
    elif doji>=1: score+=1

    # ⑦ 交替 2分
    colors=['R' if cls[i]>=ops[i] else 'G' for i in range(-5,0)]
    no3=all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r=all(-2<=pcts[i]<=2 for i in range(-5,0))
    pct5s=np.sum(pcts[-5:])
    if no3 and pct5r and -2<=pct5s<=3: score+=2

    # 评级（保留原23分体系）
    grade='A' if score>=18 else 'B' if score>=12 else 'C'
    if grade in ('A','B'):
        return {'score':score,'grade':grade,'price':last,'dd60':dd60,'vr520':vr520}
    return None


def sim(screen_fn, max_day, market_filter, b_first, close_sl):
    trades=[]; skip=0
    for di, sd in enumerate(bt_dates[:-3]):
        fut=[d for d in bt_dates if d>sd]
        if len(fut)<3: continue
        bd,hd,ed=fut[0],fut[1],fut[2]
        if market_filter:
            ms=market_state.get(sd)
            if ms and ms['bearish']: skip+=1; continue
        sel=[]
        for code, df in stock_dict.items():
            dl=df['date'].values.tolist()
            if sd not in dl: continue
            r=screen_fn(df, dl.index(sd))
            if r: r['code']=code; sel.append(r)
        if b_first:
            sel.sort(key=lambda x:(0 if x['grade']=='B' else 1, -x['score']))
        else:
            sel.sort(key=lambda x:-x['score'])
        sel=sel[:max_day]
        for s in sel:
            code=s['code']; df=stock_dict[code]; dl=df['date'].values.tolist()
            if not all(d in dl for d in [bd,hd,ed]): continue
            bi=dl.index(bd); hi=dl.index(hd); ei=dl.index(ed)
            bp=df.iloc[bi]['open']
            if bp<=0 or np.isnan(bp): continue
            d1h=df.iloc[bi]['high']; d1l=df.iloc[bi]['low']; d1c=df.iloc[bi]['close']
            d2h=df.iloc[hi]['high']; d2l=df.iloc[hi]['low']
            d3o=df.iloc[ei]['open']
            tp1=bp*1.03; tp2=bp*1.05; sl=bp*0.98; hsl=bp*0.97
            rem=1.0; ret=0.0; reason=""

            if close_sl:
                if d1l<=hsl:
                    ret+=rem*(-3.0); rem=0; reason="D1硬止损-3%"
                else:
                    if d1h>=tp2: ret+=0.5*3+0.5*5; rem=0; reason="D1止盈+5%"
                    elif d1h>=tp1: ret+=0.5*3; rem=0.5
                    if rem>0 and d1c<bp*0.985:
                        d1r=(d1c-bp)/bp*100; ret+=rem*d1r; rem=0
                        reason=(reason+"+D1收盘止损" if reason else f"D1收盘止损{d1r:.1f}%")
            else:
                if d1l<=sl: ret+=rem*(-2.0); rem=0; reason="D1止损-2%"
                else:
                    if d1h>=tp2: ret+=0.5*3+0.5*5; rem=0; reason="D1止盈+5%"
                    elif d1h>=tp1: ret+=0.5*3; rem=0.5

            if rem>0:
                if d2l<=sl:
                    ret+=rem*((sl-bp)/bp*100); rem=0
                    reason=("D2止损" if not reason else reason+"+D2止损")
                else:
                    if d2h>=tp2:
                        if rem==1.0: ret+=0.5*3+0.5*5
                        else: ret+=rem*5
                        rem=0; reason=("D2止盈" if not reason else reason+"+D2止盈")
                    elif d2h>=tp1 and rem==1.0:
                        ret+=0.5*3; rem=0.5; reason="D2半仓+3%"
            if rem>0:
                d3r=(d3o-bp)/bp*100; ret+=rem*d3r
                reason=(reason+"+D3清仓" if reason else "D3强制清仓")
            trades.append({'buy_date':bd,'code':code,'grade':s['grade'],'score':s['score'],
                          'return_pct':ret,'exit_reason':reason,'dd60':s['dd60'],'vr520':s['vr520']})
    return trades, skip


# 旧基线
print("\n运行组合对比...")
from db_cache import get_connection as _gc
# 用旧规则的screen_old复用之前逻辑
def screen_old(df, idx):
    if idx<60: return None
    data=df.iloc[:idx+1]
    cls=data['close'].values; ops=data['open'].values
    his=data['high'].values; los=data['low'].values
    vols=data['volume'].values; turns=data['turn'].values
    pcts=data['pctChg'].values; amts=data['amount'].values
    n=len(data); last=cls[-1]
    if last<3 or last>200: return None
    if np.mean(amts[-20:])/10000<1000: return None
    ma60=np.mean(cls[-60:])
    if last<=ma60: return None
    c60=cls[-60:]; pct60=(last-c60[0])/c60[0]*100
    if not(10<=pct60<=60): return None
    max60=np.max(c60); dd60=(last-max60)/max60*100
    if not(-35<=dd60<=-5): return None
    if np.sum(pcts[-60:]>=9.5)<1: return None
    if np.any(pcts[-5:]<-5): return None
    if np.any(turns[-5:]>8): return None
    ma5=np.mean(cls[-5:]); ma10=np.mean(cls[-10:]); ma20=np.mean(cls[-20:])
    score=0
    vol5=np.mean(vols[-5:]); vol20=np.mean(vols[-20:]); vol60=np.mean(vols[-60:])
    vr520=vol5/vol20 if vol20>0 else 999; vr560=vol5/vol60 if vol60>0 else 999
    turn5=np.mean(turns[-5:]); vol_dec=vols[-1]<vols[-2]<vols[-3] if n>=3 else False
    floor_vol=vols[-1]<=np.min(vols[-60:])*1.2
    sc1=sum([vr520<=0.6,vr560<=0.5,turn5<=2,vol_dec,floor_vol])
    if sc1>=3: score+=5
    elif sc1>=1: score+=2
    rng5=(np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
    cs=(np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
    sc2=sum([rng5<=5,abs(cs)<=1,last>ma60])
    if sc2>=3: score+=4
    elif sc2>=2: score+=2
    ma_sp=(max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
    sc3=sum([ma_sp<=3,last>ma60,ma5>ma10 or ma5/ma10>0.995])
    if sc3>=3: score+=4
    elif sc3>=2: score+=2
    bodies=np.abs(cls-ops)
    br=np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
    amp3=np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
    pct_abs5=np.mean(np.abs(pcts[-5:]))
    sc4=sum([br<=0.5,amp3<=3,pct_abs5<=1.5])
    if sc4>=2: score+=3
    elif sc4>=1: score+=1
    lsb=sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0 and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
    if lsb>=1: score+=3
    doji=sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/ops[i]*100<=0.5 and abs(cls[i]-ops[i])>0 and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
    if doji>=2: score+=2
    elif doji>=1: score+=1
    colors=['R' if cls[i]>=ops[i] else 'G' for i in range(-5,0)]
    no3=all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
    pct5r=all(-2<=pcts[i]<=2 for i in range(-5,0))
    pct5s=np.sum(pcts[-5:])
    if no3 and pct5r and -2<=pct5s<=3: score+=2
    grade='A' if score>=18 else 'B' if score>=12 else 'C'
    if grade in ('A','B'):
        return {'score':score,'grade':grade,'price':last,'dd60':dd60,'vr520':vr520}
    return None


configs = [
    ("① 旧基线",
     screen_old, 3, False, False, False),
    ("② 最终版=回撤5-20+大盘过滤+收盘止损+B优先",
     screen_final, 3, True, True, True),
    ("③ 对照A=回撤5-20+收盘止损（无大盘过滤）",
     screen_final, 3, False, False, True),
    ("④ 对照B=旧选股+大盘过滤+收盘止损+B优先",
     screen_old, 3, True, True, True),
]

results = {}
for label, sfn, md, mf, bf, csl in configs:
    print(f"  {label}...")
    t, s = sim(sfn, md, mf, bf, csl)
    results[label] = t
    print(f"    {len(t)}笔, 跳过{s}天")

print("\n" + "="*90)
print("  最终对比结果")
print("="*90)
print(f"\n  {'策略':<48s} {'笔':>4s} {'胜率':>6s} {'总收益':>8s} {'均':>7s} {'盈亏比':>6s} {'利润因子':>6s} {'回撤':>7s}")
print("  "+"─"*95)

for label, trades in results.items():
    rets=[t['return_pct'] for t in trades]
    wins=[r for r in rets if r>0]; losses=[r for r in rets if r<0]
    wr=len(wins)/len(rets)*100
    aw=np.mean(wins) if wins else 0; al=np.mean(losses) if losses else 0
    ratio=abs(aw/al) if al!=0 else 999
    pf=sum(wins)/abs(sum(losses)) if losses and sum(losses)!=0 else 999
    cum=0; peak=0; mdd=0
    for t in sorted(trades, key=lambda x: x['buy_date']):
        cum+=t['return_pct']; peak=max(peak,cum); mdd=min(mdd,cum-peak)
    print(f"  {label:<48s} {len(trades):>4d}  {wr:>5.1f}%  {sum(rets):>+7.2f}%  {np.mean(rets):>+6.3f}%  {ratio:>5.2f}  {pf:>6.2f}  {mdd:>+6.2f}%")

# 详细分析最终版
best_label = "② 最终版=回撤5-20+大盘过滤+收盘止损+B优先"
best_label2 = "④ 对照B=旧选股+大盘过滤+收盘止损+B优先"
for lbl in [best_label, best_label2]:
    trades = results[lbl]
    print(f"\n{'─'*70}")
    print(f"  {lbl} 详细")
    print(f"{'─'*70}")

    # 评级
    for g in ['A','B']:
        gt=[t for t in trades if t['grade']==g]
        if gt:
            gr=[t['return_pct'] for t in gt]; gw=[r for r in gr if r>0]
            gl=[r for r in gr if r<0]
            aw=np.mean(gw) if gw else 0; al=np.mean(gl) if gl else 0
            ratio=abs(aw/al) if al!=0 else 999
            print(f"  {g}级: {len(gt):>3d}笔  胜率{len(gw)/len(gt)*100:>5.1f}%  收益{sum(gr):>+7.2f}%  盈亏比{ratio:.2f}")

    # 按月
    months=sorted(set(t['buy_date'][:7] for t in trades))
    cum=0
    for m in months:
        mt=[t for t in trades if t['buy_date'].startswith(m)]
        mr=[t['return_pct'] for t in mt]
        mw=[r for r in mr if r>0]
        wr=len(mw)/len(mt)*100 if mt else 0; cum+=sum(mr)
        bars=int(abs(cum)/3); bar="█" if cum>=0 else "▒"
        print(f"  {m}: {len(mt):>3d}笔 {wr:>5.1f}% {sum(mr):>+7.2f}% 累计{cum:>+7.2f}% {bar*bars}")

    # 出场
    exit_map={}
    for t in trades:
        k=t['exit_reason'].split('+')[0] if '+' in t['exit_reason'] else t['exit_reason']
        exit_map.setdefault(k,[]).append(t['return_pct'])
    print("  出场方式:")
    for r,rs in sorted(exit_map.items(), key=lambda x:-len(x[1])):
        wr2=len([x for x in rs if x>0])/len(rs)*100
        print(f"    {r:<24s}: {len(rs):>3d}笔  胜率{wr2:>5.1f}%  总{sum(rs):>+7.2f}%")
