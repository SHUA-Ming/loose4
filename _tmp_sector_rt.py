import requests

# Get RT quotes for energy sector peers to estimate sector strength
# Energy/coal stocks: 000600建投能源, 600863英大证券(不对), 
# let's try: SH coal power stocks
# 华能国际600011, 大唐发电601991, 国电电力600795, 华电国际600027, 建投能源000600
# 甘肃能源000899, 内蒙华电600863

codes = [
    'sz000600',   # 建投能源
    'sh600011',   # 华能国际
    'sh601991',   # 大唐发电
    'sh600795',   # 国电电力
    'sh600027',   # 华电国际
    'sh600863',   # 内蒙华电
    'sz000899',   # 甘肃能源
    'sh601699',   # 潞安化工(煤)
    'sh601225',   # 陕西煤业
]

url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
r.encoding = 'gbk'

print('=== 煤电/能源板块实时行情 ===')
for line in r.text.strip().split('\n'):
    if line.strip() and '~' in line:
        parts = line.split('~')
        if len(parts) > 37:
            name = parts[1]
            code = parts[2]
            close_today = parts[3]
            prev_close = parts[4]
            open_p = parts[5]
            hi = parts[33]
            lo = parts[34]
            chg_pct = parts[32]
            print(f'{name}({code}): 现={close_today} vs 昨={prev_close} | 涨跌={chg_pct}% | 开={open_p}')

# Also check the smart money cache if available
import sqlite3, os
db = '数据缓存/stock_cache.db'
if os.path.exists(db):
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print('\nDB Tables:', tables)
    if 'lhb_log' in tables:
        cur.execute("SELECT * FROM lhb_log WHERE code LIKE '%000600%' ORDER BY date DESC LIMIT 5")
        lhb = cur.fetchall()
        print('LHB data for 000600:', lhb)
    conn.close()
