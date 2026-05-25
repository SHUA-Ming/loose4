"""深度分析TOP S2候选"""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
from db_cache import get_connection

conn = get_connection()

targets = ['sh.603989','sz.002158','sz.002559','sh.603091','sz.002522']

for code in targets:
    sk = pd.read_sql(
        "SELECT date,open,close,high,low,volume,pctChg as pct,turn "
        "FROM kline_daily WHERE code=? ORDER BY date DESC LIMIT 30",
        conn, params=[code])
    if len(sk)==0: continue
    sk = sk.iloc[::-1].reset_index(drop=True)
    info = pd.read_sql('SELECT code_name,industry FROM stock_industry WHERE code=?', conn, params=[code])
    name = info.iloc[0]['code_name'] if len(info)>0 else '?'
    ind = info.iloc[0]['industry'] if len(info)>0 else '?'
    
    sk['ma5'] = sk['close'].rolling(5).mean()
    sk['ma10'] = sk['close'].rolling(10).mean()
    sk['ma20'] = sk['close'].rolling(20).mean()
    sk['vol5'] = sk['volume'].rolling(5).mean()
    sk['vol20'] = sk['volume'].rolling(20).mean()
    
    cur = sk.iloc[-1]
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  {code} {name} | {ind}")
    print(sep)
    print(f"  现价: {cur['close']:.2f}  今日: {cur['pct']:+.2f}%  换手: {cur['turn']:.2f}%")
    
    ma5 = sk['ma5'].iloc[-1] if not pd.isna(sk['ma5'].iloc[-1]) else 0
    ma10 = sk['ma10'].iloc[-1] if not pd.isna(sk['ma10'].iloc[-1]) else 0
    ma20 = sk['ma20'].iloc[-1] if not pd.isna(sk['ma20'].iloc[-1]) else 0
    align = "多头(MA5>MA10>MA20)" if ma5>ma10>ma20 else "非多头"
    print(f"  MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}  [{align}]")
    
    vol5 = sk['vol5'].iloc[-1] if not pd.isna(sk['vol5'].iloc[-1]) else 0
    vol20 = sk['vol20'].iloc[-1] if not pd.isna(sk['vol20'].iloc[-1]) else 0
    v_ratio = vol5/vol20 if vol20>0 else 0
    v_today = cur['volume']/vol5 if vol5>0 else 0
    print(f"  量比(5/20): {v_ratio:.2f}  今日量/5日均量: {v_today:.2f}")
    
    pct5 = (cur['close']/sk.iloc[-6]['close']-1)*100 if len(sk)>=6 else 0
    last5 = sk.tail(5)
    range5 = (last5['high'].max() - last5['low'].min()) / last5['close'].mean() * 100
    print(f"  5日涨幅: {pct5:+.2f}%  5日振幅: {range5:.1f}%")
    
    # 60日数据用于检查F条件
    sk60 = pd.read_sql(
        "SELECT date,close,high,low,volume,pctChg as pct,turn "
        "FROM kline_daily WHERE code=? ORDER BY date DESC LIMIT 65",
        conn, params=[code])
    sk60 = sk60.iloc[::-1].reset_index(drop=True)
    if len(sk60) >= 20:
        ma60 = sk60['close'].tail(60).mean() if len(sk60)>=60 else sk60['close'].mean()
        pct60 = (cur['close']/sk60.iloc[0]['close']-1)*100
        high60 = sk60['high'].max()
        drawdown = (high60 - cur['close'])/high60 * 100
        has_zt = (sk60['pct']>=9.5).any()
        print(f"  MA60(估)={ma60:.2f}  现价{'>' if cur['close']>ma60 else '<'}MA60")
        print(f"  60日涨幅: {pct60:+.1f}%  从高点回撤: {drawdown:.1f}%  60日有涨停: {'是' if has_zt else '否'}")
        # F filters
        f1 = "?"  # need market cap, skip
        f2 = "PASS" if has_zt else "FAIL"
        f3 = "PASS" if 10 <= pct60 <= 60 else f"FAIL({pct60:.0f}%)"
        f4 = "PASS" if 5 <= drawdown <= 20 else f"FAIL({drawdown:.0f}%)"
        f5 = "PASS" if cur['close'] > ma60 else "FAIL"
        print(f"  前置过滤: F2={f2} F3={f3} F4={f4} F5={f5}")
    
    print(f"\n  近7日K线:")
    for i in range(max(0,len(sk)-7), len(sk)):
        r = sk.iloc[i]
        bar = '+' if r['close']>r['open'] else '-'
        vr = r['volume']/sk['vol5'].iloc[i] if not pd.isna(sk['vol5'].iloc[i]) and sk['vol5'].iloc[i]>0 else 0
        print(f"    {r['date']} {bar}{r['pct']:+6.2f}% C:{r['close']:8.2f} V:{r['volume']:12.0f} Vr:{vr:.2f} T:{r['turn']:.1f}%")
    
    # Operation plan
    c = cur['close']
    print(f"\n  操作计划(S2-B级, 仓位1/8 * 高潮0.5 = 1/16):")
    print(f"    买入区间: {c*0.97:.2f} ~ {c*1.00:.2f}")
    print(f"    硬止损(-3%): {c*0.97:.2f}")
    print(f"    软止损(收盘-1.5%): {c*0.985:.2f}")
    print(f"    止盈1(+4%卖50%): {c*1.04:.2f}")
    print(f"    移动止盈: 高点回落2.5%全清")

conn.close()
print("\n分析完成。")
