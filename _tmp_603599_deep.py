#!/usr/bin/env python3
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '工具脚本'))
import requests
from db_cache import get_connection, init_db
import pandas as pd, numpy as np

# 实时数据
def fetch(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8); resp.encoding='gbk'
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split('~')
    def sf(i):
        try: return float(items[i])
        except: return 0.0
    name=items[1]; cur=sf(3); pre=sf(4); opn=sf(5); hi=sf(33); lo=sf(34)
    pct=sf(32); vol=sf(36); amt=sf(37); turn=sf(38); outer=sf(7); inner=sf(8)
    print(f"{name} cur={cur} pre={pre} open={opn} hi={hi} lo={lo} pct={pct:+.2f}% vol={vol}手 amt={amt}万 turn={turn}% outer={outer} inner={inner} ratio={outer/max(inner,1):.2f}")
    for i in range(5,0,-1):
        sp=sf(19+i*2); sv=sf(18+i*2)
        print(f"  卖{i}: {sp:.2f} x {sv:.0f}")
    for i in range(1,6):
        bp=sf(9+i*2); bv=sf(8+i*2)
        print(f"  买{i}: {bp:.2f} x {bv:.0f}")

print("=== 实时数据 ===")
fetch('sh603599')
print()

# K线分析
init_db(); conn=get_connection()
df=pd.read_sql("SELECT * FROM kline_daily WHERE code='sh.603599' ORDER BY date", conn)
conn.close()
for c in ['open','high','low','close','volume','amount','turn','pctChg']:
    df[c]=pd.to_numeric(df[c], errors='coerce')
df=df.dropna(subset=['close','volume'])
print(f"K线数据: {len(df)}条, {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

cls=df['close'].values; ops=df['open'].values; his=df['high'].values; los=df['low'].values
vols=df['volume'].values; turns=df['turn'].values; pcts=df['pctChg'].values; amts=df['amount'].values
n=len(df); last=cls[-1]

ma5=np.mean(cls[-5:]); ma10=np.mean(cls[-10:]); ma20=np.mean(cls[-20:]); ma60=np.mean(cls[-60:])
vol5=np.mean(vols[-5:]); vol20=np.mean(vols[-20:]); vol60=np.mean(vols[-60:])
vr520=vol5/vol20; vr560=vol5/vol60
turn5=np.mean(turns[-5:]); turn20=np.mean(turns[-20:])
avg_amt20=np.mean(amts[-20:])/10000
c60=cls[-60:]; pct60=(last-c60[0])/c60[0]*100
max60=np.max(c60); dd60=(last-max60)/max60*100
max20=np.max(his[-20:]); min20=np.min(los[-20:])
rng5=(np.max(cls[-5:])-np.min(cls[-5:]))/np.mean(cls[-5:])*100
cs=(np.mean(cls[-5:])-np.mean(cls[-10:]))/np.mean(cls[-10:])*100
bodies=np.abs(cls-ops)
br=np.mean(bodies[-5:])/np.mean(bodies[-20:]) if np.mean(bodies[-20:])>0 else 999
pct_abs5=np.mean(np.abs(pcts[-5:]))

print(f"\n=== 技术指标 ===")
print(f"收盘价: {last:.2f}  今日涨跌: {pcts[-1]:+.2f}%")
print(f"MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60:.2f}")
if ma5>ma10>ma20>ma60: print("MA排列: 完美多头")
elif ma5>ma10>ma20: print("MA排列: 短期多头(MA60上方)" if ma20>ma60 else "MA排列: 短期多头(MA60下方)")
elif ma5<ma10<ma20: print("MA排列: 空头")
else: print("MA排列: 交叉纠缠")
print(f"现价vs MA5: {(last/ma5-1)*100:+.2f}%  vs MA10: {(last/ma10-1)*100:+.2f}%  vs MA20: {(last/ma20-1)*100:+.2f}%  vs MA60: {(last/ma60-1)*100:+.2f}%")
ma_sp=(max(ma5,ma10,ma20)-min(ma5,ma10,ma20))/((ma5+ma10+ma20)/3)*100
print(f"均线间距: {ma_sp:.2f}%")
print(f"量比5/20={vr520:.2f} 量比5/60={vr560:.2f}")
print(f"5日换手={turn5:.2f}% 20日换手={turn20:.2f}%")
print(f"20日均额={avg_amt20:.0f}万")
print(f"60日涨幅={pct60:+.1f}% 高点回撤={dd60:.1f}%")
print(f"20日高={max20:.2f} 20日低={min20:.2f}")
print(f"5日波幅={rng5:.2f}% 重心偏移={cs:+.2f}%")
print(f"实体缩小比={br:.2f} 5日绝对波动={pct_abs5:.2f}%")

# 近5日涨幅
chg5=(cls[-1]/cls[-6]-1)*100 if n>=6 else 0
chg10=(cls[-1]/cls[-11]-1)*100 if n>=11 else 0
print(f"5日涨幅={chg5:+.2f}%  10日涨幅={chg10:+.2f}%")

print(f"\n=== 近15日K线 ===")
for i in range(-15,0):
    d=df['date'].iloc[i]; o=ops[i]; h=his[i]; l=los[i]; c_val=cls[i]; v=vols[i]; p=pcts[i]; t=turns[i]
    tag='阳' if c_val>=o else '阴'
    body_pct=abs(c_val-o)/o*100
    lower_shadow=min(o,c_val)-l
    upper_shadow=h-max(o,c_val)
    body_size=abs(c_val-o)
    print(f"  {d} O={o:.2f} H={h:.2f} L={l:.2f} C={c_val:.2f} {p:+5.2f}% 量={v/10000:.0f}万 换手={t:.2f}% {tag} 体{body_pct:.1f}% 下影{lower_shadow:.2f} 上影{upper_shadow:.2f}")

# S2检查
print(f"\n=== 大阳线检查(S2) ===")
vol20_val=np.mean(vols[-20:])
found_bc = False
for di in range(1,11):
    i2=n-di
    if i2<1: break
    day_pct=(cls[i2]/cls[i2-1]-1)*100; day_vr=vols[i2]/vol20_val if vol20_val>0 else 0
    is_yang=cls[i2]>ops[i2]
    if day_pct>=4 and is_yang and day_vr>=1.5:
        bc_date=df['date'].iloc[i2]; bc_close=cls[i2]; bc_open=ops[i2]; bc_vol=vols[i2]
        days_after=n-1-i2
        print(f"  大阳线: {bc_date} 涨{day_pct:+.1f}% 量比{day_vr:.1f}x 收{bc_close:.2f} 开{bc_open:.2f}")
        print(f"  后续天数: {days_after}")
        if days_after > 0:
            post_vols=vols[i2+1:]; avg_post=np.mean(post_vols); shrk=avg_post/bc_vol
            print(f"  缩量比: {shrk:.2f}")
            print(f"  价格守住: {last/bc_close:.4f} (>={bc_open:.2f}? {last>=bc_open})")
            post_pcts=pcts[i2+1:]
            print(f"  后续最大跌幅: {np.min(post_pcts):+.2f}%")
            # 放量砸盘检查
            for pi in range(len(post_vols)):
                if post_vols[pi] > bc_vol*0.8 and pcts[i2+1+pi] < 0:
                    print(f"  ⚠ 放量砸盘: {df['date'].iloc[i2+1+pi]} vol_ratio={post_vols[pi]/bc_vol:.2f} pct={pcts[i2+1+pi]:+.2f}%")
        found_bc = True
        break
if not found_bc:
    print("  近10天无大阳线")

# S3检查
print(f"\n=== 突破检查(S3) ===")
if n>=21:
    h20=np.max(his[-21:-1])
    brk=(last/h20-1)*100
    vr_last=vols[-1]/vol20_val if vol20_val>0 else 0
    is_yang=cls[-1]>ops[-1]
    print(f"  20日最高: {h20:.2f}  现价: {last:.2f}  突破: {brk:+.2f}%")
    print(f"  今日量比20d: {vr_last:.2f}x  收阳: {is_yang}  MA20>MA60: {ma20>ma60}")
    if last<=h20: print("  ❌ 未突破20日高点")
    if vr_last<1.5: print(f"  ❌ 量比不够(需>=1.5x, 当前{vr_last:.2f}x)")
    if not is_yang: print("  ❌ 收阴线")

# S1详细评分
print(f"\n=== S1详细评分 ===")
score=0

# F前置
f_pass=True
print(f"  F1 价格3-200: {last:.2f} {'✅' if 3<=last<=200 else '❌'}")
print(f"  F2 20日均额>=1000万: {avg_amt20:.0f}万 {'✅' if avg_amt20>=1000 else '❌'}")
print(f"  F3 60日涨幅10-60%: {pct60:+.1f}% {'✅' if 10<=pct60<=60 else '❌'}")
if not(10<=pct60<=60): f_pass=False; print("    ⚠ F3不满足!")
print(f"  F4 高点回撤-5~-20%: {dd60:.1f}% {'✅' if -20<=dd60<=-5 else '❌'}")
if not(-20<=dd60<=-5): f_pass=False; print("    ⚠ F4不满足!")
print(f"  F5 现价>MA60: {last:.2f}>{ma60:.2f} {'✅' if last>ma60 else '❌'}")
if last<=ma60: f_pass=False

# ① 缩量
vol_dec=vols[-1]<vols[-2]<vols[-3] if n>=3 else False
floor_vol=vols[-1]<=np.min(vols[-60:])*1.2
scs=[0.4<=vr520<=0.8, vr560<=0.7, turn5<=2, vol_dec, floor_vol]
sc1=sum(scs)
s1=3 if sc1>=4 else (2 if sc1>=3 else (1 if sc1>=1 else 0))
score+=s1
print(f"  ① 缩量: VR5/20={vr520:.2f}({scs[0]}) VR5/60={vr560:.2f}({scs[1]}) 换手{turn5:.2f}%({scs[2]}) 递减({scs[3]}) 地量({scs[4]}) → {sc1}/5 = {s1}分")

# ② 横盘
hp_days=0
for hi2 in range(1,min(21,n)):
    if abs(pcts[-hi2])<=1.5: hp_days+=1
    else: break
scs2=[rng5<=5, abs(cs)<=1, last>ma60, hp_days>=5]
sc2=sum(scs2)
s2=4 if sc2>=4 else (3 if sc2>=3 else (2 if sc2>=2 else (1 if sc2>=1 else 0)))
score+=s2
print(f"  ② 横盘: 5日幅{rng5:.2f}%({scs2[0]}) 偏移{cs:+.2f}%({scs2[1]}) >MA60({scs2[2]}) 横盘{hp_days}d({scs2[3]}) → {sc2}/4 = {s2}分")

# ③ 均线
scs3=[ma_sp<=3, last>ma60, ma5>ma10 or ma5/ma10>0.995]
sc3=sum(scs3)
s3=4 if sc3>=3 else (3 if sc3>=2 else (2 if sc3>=1 else 0))
score+=s3
print(f"  ③ 均线: 间距{ma_sp:.2f}%({scs3[0]}) >MA60({scs3[1]}) MA5>MA10({scs3[2]}) → {sc3}/3 = {s3}分")

# ④ 实体
amp3=np.max((his[-3:]-los[-3:])/cls[-4:-1]*100) if n>=4 else 999
scs4=[br<=0.5, amp3<=3, pct_abs5<=1.5]
sc4=sum(scs4)
s4=3 if sc4>=2 else (2 if sc4>=1 else 1)
score+=s4
print(f"  ④ 实体: 缩比{br:.2f}({scs4[0]}) 振幅{amp3:.2f}%({scs4[1]}) 波动{pct_abs5:.2f}%({scs4[2]}) → {sc4}/3 = {s4}分")

# ⑤ 下影线
lsb=sum(1 for i in range(-5,0) if cls[i]>ops[i] and abs(cls[i]-ops[i])>0 and (min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]) and pcts[i]<=2)
s5=2 if lsb>=1 else 0
score+=s5
print(f"  ⑤ 下影: {lsb}根 → {s5}分")

# ⑥ 十字
doji=sum(1 for i in range(-5,0) if abs(cls[i]-ops[i])/max(ops[i],0.01)*100<=0.5 and abs(cls[i]-ops[i])>0 and max(his[i]-max(ops[i],cls[i]),min(ops[i],cls[i])-los[i])>=2*abs(cls[i]-ops[i]))
s6=2 if doji>=2 else (1 if doji>=1 else 0)
score+=s6
print(f"  ⑥ 十字: {doji}根 → {s6}分")

# ⑦ 交替
colors=['R' if cls[i]>=ops[i] else 'G' for i in range(-5,0)]
no3=all(not(colors[i]==colors[i+1]==colors[i+2]) for i in range(3))
pct5r=all(-2<=pcts[i]<=2 for i in range(-5,0))
pct5s=np.sum(pcts[-5:])
cond7=no3 and pct5r and -2<=pct5s<=3
s7=2 if cond7 else 0
score+=s7
print(f"  ⑦ 交替: 颜色{colors} 无3连({no3}) 全<2%({pct5r}) 总{pct5s:+.2f}%({-2<=pct5s<=3}) → {s7}分")

grade='A' if score>=16 else ('B' if score>=15 else 'C')
print(f"\n  S1总分: {score}/20 → {grade}级 {'✅达标' if grade in ('A','B') else '❌不达标'}")
if not f_pass:
    print("  ⚠ 前置过滤未全部通过，即使评分达标也不推荐")
