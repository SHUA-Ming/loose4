#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易记录工具 - 命令行操作
用法:
    python trade_log.py buy  600251 冠农股份 11.15 --score 16 --grade A --mode A --stop 10.93
    python trade_log.py sell 1 11.50 --rule 1 --remark "按计划止盈"
    python trade_log.py open            # 查看持仓
    python trade_log.py history         # 最近交易记录
    python trade_log.py stats           # 胜率统计
"""

import argparse
import sys
from pathlib import Path

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))
from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import add_trade, close_trade, get_open_trades, get_trade_history, get_trade_stats


def cmd_buy(args):
    today = args.date or __import__('datetime').date.today().strftime('%Y-%m-%d')
    tid = add_trade(
        code=args.code, name=args.name, buy_date=today,
        buy_price=args.price, mode=args.mode, grade=args.grade,
        score=args.score, stop_price=args.stop, target_price=args.target,
        position=args.pos, remark=args.remark
    )
    print(f"✅ 买入记录已添加  ID={tid}  {args.code} {args.name}  ¥{args.price}  "
          f"{args.grade}级{args.score}分  方案{args.mode}")


def cmd_sell(args):
    today = args.date or __import__('datetime').date.today().strftime('%Y-%m-%d')
    ok = close_trade(args.id, today, args.price, follow_rule=args.rule, remark=args.remark)
    if ok:
        print(f"✅ 平仓完成  ID={args.id}  卖出价 ¥{args.price}  纪律{'✔' if args.rule else '✘'}")
    else:
        print(f"❌ 未找到 ID={args.id} 的记录")


def cmd_open(args):
    df = get_open_trades()
    if df.empty:
        print("当前无持仓")
        return
    cols = ['id', 'code', 'name', 'mode', 'grade', 'score', 'buy_date', 'buy_price', 'stop_price', 'target_price']
    print(df[cols].to_string(index=False))


def cmd_history(args):
    df = get_trade_history(limit=args.n)
    if df.empty:
        print("暂无交易记录")
        return
    cols = ['id', 'code', 'name', 'grade', 'score', 'buy_date', 'buy_price',
            'sell_date', 'sell_price', 'pnl_pct', 'follow_rule', 'remark']
    print(df[cols].to_string(index=False))


def cmd_stats(args):
    s = get_trade_stats()
    if not s:
        print("暂无已平仓记录，无法统计")
        return
    win_rate = round(s['wins'] / s['total'] * 100, 1) if s['total'] else 0
    print(f"═══ 交易统计 ═══")
    print(f"  总交易: {s['total']}笔  |  胜: {s['wins']}  负: {s['losses']}  |  胜率: {win_rate}%")
    print(f"  平均盈亏: {s['avg_pnl']}%  |  平均盈利: {s['avg_win']}%  |  平均亏损: {s['avg_loss']}%")
    if s['avg_win'] and s['avg_loss'] and s['avg_loss'] != 0:
        ratio = round(abs(s['avg_win'] / s['avg_loss']), 2)
        print(f"  盈亏比: {ratio}")
    print(f"  守纪律: {s['rule_follow']}次  |  破纪律: {s['rule_break']}次")


def main():
    parser = argparse.ArgumentParser(description='交易记录工具')
    sub = parser.add_subparsers(dest='cmd')

    # buy
    p_buy = sub.add_parser('buy', help='记录买入')
    p_buy.add_argument('code', help='股票代码 如 600251')
    p_buy.add_argument('name', help='股票名称')
    p_buy.add_argument('price', type=float, help='买入价')
    p_buy.add_argument('--score', type=int, default=0, help='评分')
    p_buy.add_argument('--grade', default='A', help='等级 A/B15')
    p_buy.add_argument('--mode', default='A', help='方案 A/B')
    p_buy.add_argument('--stop', type=float, help='止损价')
    p_buy.add_argument('--target', type=float, help='止盈价')
    p_buy.add_argument('--pos', type=float, help='仓位比例')
    p_buy.add_argument('--date', help='买入日期 YYYY-MM-DD')
    p_buy.add_argument('--remark', help='备注')
    p_buy.set_defaults(func=cmd_buy)

    # sell
    p_sell = sub.add_parser('sell', help='记录卖出')
    p_sell.add_argument('id', type=int, help='交易ID')
    p_sell.add_argument('price', type=float, help='卖出价')
    p_sell.add_argument('--rule', type=int, default=1, help='是否遵守纪律 1=是 0=否')
    p_sell.add_argument('--date', help='卖出日期 YYYY-MM-DD')
    p_sell.add_argument('--remark', help='备注')
    p_sell.set_defaults(func=cmd_sell)

    # open
    p_open = sub.add_parser('open', help='查看持仓')
    p_open.set_defaults(func=cmd_open)

    # history
    p_hist = sub.add_parser('history', help='交易历史')
    p_hist.add_argument('-n', type=int, default=20, help='显示几条')
    p_hist.set_defaults(func=cmd_history)

    # stats
    p_stats = sub.add_parser('stats', help='胜率统计')
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
