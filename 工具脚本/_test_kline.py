import requests, json

# 测试腾讯K线接口 - 不同代码格式
for code in ['sh600519', '600519', 'SH600519']:
    url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,120,qfq'
    resp = requests.get(url, timeout=10)
    data = resp.json()
    has_data = len(data.get('data', [])) > 0 if isinstance(data.get('data'), list) else bool(data.get('data'))
    print(f"{code}: msg={data.get('msg')}, has_data={has_data}")
    if has_data and isinstance(data['data'], dict):
        for k in list(data['data'].keys())[:3]:
            v = data['data'][k]
            if isinstance(v, list) and len(v) > 0:
                print(f"  {k}: {len(v)} items")
            elif isinstance(v, dict):
                sub = list(v.keys())[:5]
                print(f"  {k}: dict keys={sub}")
                for sk in sub:
                    sv = v[sk]
                    if isinstance(sv, list) and len(sv) > 0:
                        print(f"    {sk}: {len(sv)} items, first={sv[0][:3] if len(sv[0])>=3 else sv[0]}")

# 测试新浪接口
print("\n--- Sina API ---")
url2 = 'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh600519&scale=240&ma=no&datalen=120'
try:
    resp2 = requests.get(url2, timeout=10)
    print(f"sina status: {resp2.status_code}, len={len(resp2.text)}")
    if resp2.text and len(resp2.text) > 10:
        print(resp2.text[:400])
except Exception as e:
    print(f"sina error: {e}")

# 测试腾讯另一个K线接口
print("\n--- QQ proxy kline ---")
url3 = 'http://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh600519,day,,120,qfq'
try:
    resp3 = requests.get(url3, timeout=10)
    print(f"qq proxy status: {resp3.status_code}, len={len(resp3.text)}")
    if resp3.text:
        d3 = resp3.json()
        print(f"  code={d3.get('code')}, msg={d3.get('msg')}")
        if isinstance(d3.get('data'), dict):
            print(f"  data keys: {list(d3['data'].keys())}")
except Exception as e:
    print(f"qq proxy error: {e}")
