import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '工具脚本'))
import requests

def fetch(sym):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    try:
        idx = text.index('="') + 2
        payload = text[idx:].rstrip('";')
        items = payload.split("~")
        def sf(i):
            try: return float(items[i])
            except: return 0.0
        name = items[1]; cur = sf(3); pre = sf(4); opn = sf(5)
        pct = sf(32)
        return name, cur, pre, opn, pct
    except:
        return sym, 0, 0, 0, 0

candidates = [
    ('sz.000066', '长城电脑', 'S2-9分', '19.90~20.22'),
    ('sz.001211', '大自然家居', 'S2-8分', '39.20~40.80'),
    ('sh.600455', '博通股份', 'S2-7分', '27.57~28.13'),
    ('sh.603329', '上海科技', 'S2-7分', '待确认'),
    ('sh.603682', '海普瑞', 'S2-7分', '待确认'),
    ('sz.000782', '美达股份', 'S2-7分', '待确认'),
    ('sh.603719', '海峡环保', 'S2-7分', '待确认'),
    ('sz.002191', '劲嘉股份', 'S2-7分', '待确认'),
    ('sh.601500', '通联支付', 'S2-7分', '待确认'),
    ('sz.002135', '东南网架', 'S3-6分', '8.94~9.22'),
    ('sh.603557', '苏奥传感', 'S3-5分', '4.40~4.54'),
    ('sz.002348', '高乐股份', 'S3-5分', '11.67~12.03'),
    ('sz.002458', '益生股份', 'S3-5分', '待确认'),
    ('sz.000668', '荣盛房地产', 'S3-5分', '待确认'),
]

# map to tencent format
def to_qq(code):
    if code.startswith('sz.'): return 'sz' + code[3:]
    if code.startswith('sh.'): return 'sh' + code[3:]
    return code

print(f"{'代码':<12}{'名称':<12}{'策略':>10}{'昨收':>8}{'现价':>8}{'涨幅':>8}{'买入区间':>14}  状态")
print('-'*80)
for code, hint_name, strat, buy_zone in candidates:
    qq = to_qq(code)
    name, cur, pre, opn, pct = fetch(qq)
    if not name or name == qq:
        name = hint_name
    status = ''
    if pct > 3:
        status = '❌超追高线(>3%)放弃'
    elif pct > 0:
        status = '✅涨幅合理'
    elif pct < -1:
        status = '⚠️低开过多先观察'
    else:
        status = '✅平开/微跌'
    print(f"{code:<12}{name[:10]:<12}{strat:>10}{pre:>8.2f}{cur:>8.2f}{pct:>7.2f}%{buy_zone:>14}  {status}")

