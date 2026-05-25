import requests

def fetch(sym, show_chip=False):
    url = f"https://qt.gtimg.cn/q={sym}"
    resp = requests.get(url, timeout=8)
    resp.encoding = "gbk"
    text = resp.text.strip()
    idx = text.index('="') + 2
    payload = text[idx:].rstrip('";')
    items = payload.split("~")
    def sf(i):
        try:
            return float(items[i])
        except:
            return 0.0

    name = items[1]
    code = items[2]
    cur = sf(3)
    pre = sf(4)
    opn = sf(5)
    hi = sf(33)
    lo = sf(34)
    amp = sf(43)
    chg = sf(31)
    pct = sf(32)
    vol = sf(36)
    amt = sf(37)
    turn = sf(38)
    outer = sf(7)
    inner = sf(8)
    upd = items[30] if len(items) > 30 else ""

    # ═══ 五档盘口数据提取 ═══
    sell_prices = []; sell_vols = []
    buy_prices = [];  buy_vols = []
    for i in range(5, 0, -1):
        sp = sf(19 + i*2); sv = sf(18 + i*2)
        sell_prices.append(sp); sell_vols.append(sv)
    for i in range(1, 6):
        bp = sf(9 + i*2); bv = sf(8 + i*2)
        buy_prices.append(bp); buy_vols.append(bv)

    total_buy_vol = sum(buy_vols)
    total_sell_vol = sum(sell_vols)

    print(f"{'='*60}")
    print(f"  {name} ({code})")
    print(f"  更新时间: {upd}")
    print(f"{'='*60}")
    print(f"  当前价: {cur:.2f}   昨收: {pre:.2f}   开盘: {opn:.2f}")
    print(f"  最高: {hi:.2f}   最低: {lo:.2f}   振幅: {amp:.2f}%")
    print(f"  涨跌额: {chg:+.2f}   涨跌幅: {pct:+.2f}%")
    print(f"  成交量: {vol:,.0f}手   成交额: {amt:,.0f}万")
    print(f"  换手率: {turn:.2f}%")
    print(f"  外盘: {outer:,.0f}手  内盘: {inner:,.0f}手  外/内比: {outer/max(inner,1):.2f}")

    # ═══ 五档盘口 + 增强分析 ═══
    print(f"  --- 五档盘口 ---")
    for i in range(len(sell_prices)):
        sv = sell_vols[i]; sp = sell_prices[i]
        bar_len = int(sv / max(max(sell_vols + buy_vols), 1) * 20)
        print(f"  卖{5-i}: {sp:>8.2f} x {sv:>8,.0f}  {'▓' * bar_len}")
    print(f"  {'─'*40}")
    for i in range(len(buy_prices)):
        bv = buy_vols[i]; bp = buy_prices[i]
        bar_len = int(bv / max(max(sell_vols + buy_vols), 1) * 20)
        print(f"  买{i+1}: {bp:>8.2f} x {bv:>8,.0f}  {'█' * bar_len}")

    # ═══ 盘口增强分析 (非指数) ═══
    is_index = code.startswith('000') or code.startswith('399')
    if not is_index and cur > 0:
        print(f"  --- 盘口增强分析 ---")

        # 1. 买卖压力比
        if total_sell_vol > 0:
            pressure_ratio = total_buy_vol / total_sell_vol
            if pressure_ratio > 1.5:
                pressure_signal = "🟢买盘强势(多头主导)"
            elif pressure_ratio > 1.1:
                pressure_signal = "🟢买盘偏强"
            elif pressure_ratio > 0.9:
                pressure_signal = "⚪买卖均衡"
            elif pressure_ratio > 0.6:
                pressure_signal = "🔴卖盘偏强"
            else:
                pressure_signal = "🔴卖盘强势(空头主导)"
            print(f"  买卖压力比: {pressure_ratio:.2f}  (买{total_buy_vol:,.0f} vs 卖{total_sell_vol:,.0f})  {pressure_signal}")
        else:
            print(f"  买卖压力比: N/A (卖盘量为0)")

        # 2. 大单托底/压制检测
        if buy_vols:
            max_buy = max(buy_vols)
            max_buy_idx = buy_vols.index(max_buy)
            avg_buy = total_buy_vol / len(buy_vols) if buy_vols else 1
            if max_buy > avg_buy * 2:
                print(f"  🛡️ 买{max_buy_idx+1}档有大单托底: {buy_prices[max_buy_idx]:.2f} x {max_buy:,.0f}手 (是均值的{max_buy/avg_buy:.1f}倍)")
        if sell_vols:
            max_sell = max(sell_vols)
            max_sell_idx = sell_vols.index(max_sell)
            avg_sell = total_sell_vol / len(sell_vols) if sell_vols else 1
            if max_sell > avg_sell * 2:
                print(f"  🧱 卖{5-max_sell_idx}档有大单压制: {sell_prices[max_sell_idx]:.2f} x {max_sell:,.0f}手 (是均值的{max_sell/avg_sell:.1f}倍)")

        # 3. 外内盘趋势信号
        if outer > 0 or inner > 0:
            oi_ratio = outer / max(inner, 1)
            if oi_ratio > 1.3:
                oi_signal = "🟢主动性买入强(资金在抢筹)"
            elif oi_ratio > 1.05:
                oi_signal = "🟢偏买入"
            elif oi_ratio > 0.95:
                oi_signal = "⚪多空均衡"
            elif oi_ratio > 0.7:
                oi_signal = "🔴偏卖出"
            else:
                oi_signal = "🔴主动性卖出强(资金在出逃)"
            print(f"  外/内盘信号: {oi_signal}")

        # 4. 分时位置判断（当前价在今日K线中的位置）
        if hi > lo and hi > 0:
            intraday_pos = (cur - lo) / (hi - lo)
            if intraday_pos > 0.8:
                pos_signal = "🟢高位运行(强势)"
            elif intraday_pos > 0.5:
                pos_signal = "🟢中上位"
            elif intraday_pos > 0.2:
                pos_signal = "🔴中下位"
            else:
                pos_signal = "🔴低位运行(弱势)"
            print(f"  日内位置: {intraday_pos:.0%} {pos_signal}")

        # 5. 委比（买卖力量对比的百分比指标）
        if total_buy_vol + total_sell_vol > 0:
            weibi = (total_buy_vol - total_sell_vol) / (total_buy_vol + total_sell_vol) * 100
            if weibi > 30:
                wb_signal = "🟢买方优势明显"
            elif weibi > 10:
                wb_signal = "🟢偏多"
            elif weibi > -10:
                wb_signal = "⚪均衡"
            elif weibi > -30:
                wb_signal = "🔴偏空"
            else:
                wb_signal = "🔴卖方优势明显"
            print(f"  委比: {weibi:+.1f}%  {wb_signal}")

        # 6. 买一~卖一价差（流动性）
        if buy_prices and sell_prices and buy_prices[0] > 0 and sell_prices[-1] > 0:
            spread = sell_prices[-1] - buy_prices[0]
            spread_pct = spread / cur * 100
            if spread_pct > 0.5:
                spread_signal = "⚠️价差偏大(流动性差)"
            elif spread_pct > 0.2:
                spread_signal = "正常"
            else:
                spread_signal = "✅流动性好"
            print(f"  买卖价差: {spread:.2f} ({spread_pct:.2f}%)  {spread_signal}")

    print()

    # ═══ 筹码分析（仅对个股且开启时） ═══
    if show_chip and not is_index and cur > 0:
        try:
            # 构建baostock代码格式
            if code.startswith('6'):
                bs_code = f"sh.{code}"
            elif code.startswith('0') or code.startswith('3'):
                bs_code = f"sz.{code}"
            else:
                bs_code = sym.replace('sh', 'sh.').replace('sz', 'sz.')
            from chip_cost import analyze_chip_from_db, print_chip_report, chip_entry_check
            result = analyze_chip_from_db(bs_code, current_price=cur)
            if result:
                print_chip_report(result, name=f"{name}")
        except Exception as e:
            print(f"  筹码分析失败: {e}")
            print()

# === 实时行情 + 盘口增强 ===
fetch("sh000001")   # 上证
fetch("sz399001")   # 深证
fetch("sz399006")   # 创业板
# S2 候选
fetch("sh605598")   # S2#2 上海港湾(土木工程) 8分A龙头
fetch("sh603477")   # S2#4 振静股份(畜牧) 7分A龙头
fetch("sh600841")   # S2#6 上柴股份(通用设备) 8分A跟风
fetch("sh603496")   # S2#9 恒信东方(计算机) 7分A跟风
fetch("sh603991")   # S2#5 至正股份(橡胶) 7分A龙头
fetch("sh600510")   # S2#3 黑牡丹(房地产) 7分A龙头
fetch("sh603730")   # S2#16 岱美股份(汽车) 7分A龙头
# S3 候选
fetch("sz000595")   # S3#1 西北轴承(通用设备) 6分A龙头
fetch("sh600360")   # S3#2 华微电子(计算机) 6分A跟风
