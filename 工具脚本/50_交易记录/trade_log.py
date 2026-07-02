#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单交易记录工具。

最常用：
    python 工具脚本/50_交易记录/trade_log.py buy 002015 协鑫能科 23.02 --strategy S4 --grade A --score 8 --shares 400 --entry 22.54 23.14 --stop 22.31 --soft-stop 22.66 --target 24.38 --target2 25.07 --pos 1/16 --confidence 中 --evidence "M3+主线发酵+S4达标" --invalidation "跌破MA5且概念转弱" --remark "按计划竞价买入"
    python 工具脚本/50_交易记录/trade_log.py sell 1 24.38 --reason 止盈1 --rule 1 --remark "到+6%卖1/3"
    python 工具脚本/50_交易记录/trade_log.py review 1 --result 买点正确 --pnl-1d 3.2 --pnl-3d 5.8 --notes "次日冲高到目标位，未触发反证"
    python 工具脚本/50_交易记录/trade_log.py calibration --by confidence
    python 工具脚本/50_交易记录/trade_log.py open
    python 工具脚本/50_交易记录/trade_log.py history
    python 工具脚本/50_交易记录/trade_log.py stats
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))

from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import add_trade, close_trade, get_connection, get_open_trades, get_trade_history, get_trade_stats, init_db

# 当前系统版本号：每次改动选股/风控/情绪逻辑就 +1，并在《交易体系/系统变更日志.md》记一条。
# buy 不显式传 --sysver 时自动打这个版本，保证每笔都带版本、月末能按版本分段校准。
CURRENT_SYSVER = 'v1'


def today_str():
    return dt.date.today().strftime('%Y-%m-%d')


def normalize_code(code):
    raw = str(code).strip()
    if raw.startswith(('sh.', 'sz.')):
        return raw
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        return raw
    return f"sh.{digits}" if digits.startswith('6') else f"sz.{digits}"


def parse_position(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if '/' in text:
        left, right = text.split('/', 1)
        try:
            return round(float(left) / float(right), 6)
        except Exception:
            return None
    try:
        number = float(text)
    except Exception:
        return None
    return round(number / 100, 6) if number > 1 else number


def money_text(value):
    if value is None or value == '':
        return '-'
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value)


def pct_text(value):
    if value is None or value == '':
        return '-'
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return str(value)


def cmd_init(_args):
    init_db()
    print("OK: trade_log 表已就绪。")
    print("字段够用：策略、评分、买入区间、止损止盈、仓位、置信度、证据、反证条件、复盘结果都会记录。")


def cmd_buy(args):
    init_db()
    buy_date = args.date or today_str()
    entry_low = args.entry[0] if args.entry else None
    entry_high = args.entry[1] if args.entry else None
    position = parse_position(args.pos)
    trade_id = add_trade(
        code=normalize_code(args.code),
        name=args.name,
        buy_date=buy_date,
        buy_price=args.price,
        mode=args.mode,
        grade=args.grade,
        score=args.score,
        stop_price=args.stop,
        soft_stop=args.soft_stop,
        target_price=args.target,
        target2_price=args.target2,
        position=position,
        strategy=args.strategy,
        concept_stage=args.concept_stage,
        concept_name=args.concept,
        industry=args.industry,
        entry_low=entry_low,
        entry_high=entry_high,
        shares=args.shares,
        amount=args.amount,
        plan_source=args.source,
        buy_status=args.status,
        emotion_phase=args.emotion,
        market_mode=args.market_mode,
        confidence_level=args.confidence,
        evidence_summary=args.evidence,
        invalidation_condition=args.invalidation,
        risk_notes=args.risk,
        expected_horizon=args.expected,
        remark=args.remark,
        sysver=args.sysver,
    )
    amount = args.amount if args.amount is not None else (args.shares * args.price if args.shares else None)
    print(f"OK: 买入已记录 ID={trade_id}")
    print(f"  {normalize_code(args.code)} {args.name}  买入价 {args.price:.2f}  数量 {args.shares or '-'}  金额 {money_text(amount)}")
    print(f"  策略 {args.strategy or '-'}  评分 {args.score or '-'}/{args.grade or '-'}  仓位 {args.pos or '-'}  买入区间 {money_text(entry_low)}~{money_text(entry_high)}")
    print(f"  硬止损 {money_text(args.stop)}  软止损 {money_text(args.soft_stop)}  止盈 {money_text(args.target)} / {money_text(args.target2)}")
    print(f"  置信度 {args.confidence or '-'}  证据 {args.evidence or '-'}")
    print(f"  失效条件 {args.invalidation or '-'}  风险 {args.risk or '-'}  验证周期 {args.expected or '-'}")
    print(f"  系统版本 {args.sysver}")


def cmd_sell(args):
    init_db()
    sell_date = args.date or today_str()
    ok = close_trade(
        args.id,
        sell_date,
        args.price,
        follow_rule=args.rule,
        remark=args.remark,
        sell_reason=args.reason,
    )
    if ok:
        print(f"OK: 卖出已记录 ID={args.id}  卖出价 {args.price:.2f}  原因 {args.reason or '-'}  纪律{'遵守' if args.rule else '未遵守'}")
    else:
        print(f"未找到 ID={args.id} 的记录。")


def cmd_open(_args):
    init_db()
    df = get_open_trades()
    if df.empty:
        print("当前无持仓记录。")
        return
    cols = [
        'id', 'code', 'name', 'strategy', 'sysver', 'grade', 'score', 'buy_date', 'buy_price',
        'shares', 'amount', 'entry_low', 'entry_high', 'stop_price', 'soft_stop',
        'target_price', 'target2_price', 'position', 'confidence_level',
        'invalidation_condition', 'remark'
    ]
    cols = [col for col in cols if col in df.columns]
    print(df[cols].to_string(index=False))


def cmd_history(args):
    init_db()
    df = get_trade_history(limit=args.n)
    if df.empty:
        print("暂无交易记录。")
        return
    cols = [
        'id', 'code', 'name', 'strategy', 'sysver', 'grade', 'score', 'buy_date', 'buy_price',
        'sell_date', 'sell_price', 'sell_reason', 'pnl_pct', 'pnl_amount',
        'confidence_level', 'review_result', 'follow_rule', 'remark'
    ]
    cols = [col for col in cols if col in df.columns]
    print(df[cols].to_string(index=False))


def cmd_review(args):
    init_db()
    review_date = args.date or today_str()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM trade_log WHERE id = %s", (args.id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        print(f"未找到 ID={args.id} 的记录。")
        return
    cur.execute("""
        UPDATE trade_log
        SET review_result=%s,
            review_date=%s,
            pnl_1d=%s,
            pnl_3d=%s,
            pnl_5d=%s,
            review_notes=CONCAT(IFNULL(review_notes, ''), IF(review_notes IS NULL OR review_notes = '', '', '; '), %s)
        WHERE id=%s
    """, (args.result, review_date, args.pnl_1d, args.pnl_3d, args.pnl_5d, args.notes, args.id))
    conn.commit()
    cur.close()
    conn.close()
    print(f"OK: 复盘已记录 ID={args.id}  日期 {review_date}  结果 {args.result}")
    print(f"  后验表现 1日 {pct_text(args.pnl_1d)}  3日 {pct_text(args.pnl_3d)}  5日 {pct_text(args.pnl_5d)}")
    print(f"  备注 {args.notes}")


def cmd_calibration(args):
    init_db()
    group_map = {
        'confidence': 'confidence_level',
        'strategy': 'strategy',
        'mode': 'market_mode',
        'version': 'sysver',
    }
    group_col = group_map[args.by]
    conn = get_connection()
    cur = conn.execute(f"""
        SELECT COALESCE({group_col}, '') as bucket,
               COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(AVG(pnl_pct), 2) as avg_pnl,
               ROUND(AVG(pnl_1d), 2) as avg_1d,
               ROUND(AVG(pnl_3d), 2) as avg_3d,
               ROUND(AVG(pnl_5d), 2) as avg_5d,
               SUM(CASE WHEN follow_rule = 0 THEN 1 ELSE 0 END) as rule_breaks
        FROM trade_log
        WHERE status = 'closed' OR sell_date IS NOT NULL OR review_result IS NOT NULL
        GROUP BY COALESCE({group_col}, '')
        ORDER BY total DESC, avg_pnl DESC
    """)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        print("暂无可校准记录。")
        return
    print(f"预测校准统计（按 {args.by} 分组）")
    print("分组 | 样本 | 胜率 | 平均平仓 | 平均1日 | 平均3日 | 平均5日 | 破纪律")
    for bucket, total, wins, avg_pnl, avg_1d, avg_3d, avg_5d, rule_breaks in rows:
        win_rate = round((wins or 0) / total * 100, 1) if total else 0
        label = bucket or '未填写'
        print(f"{label} | {total} | {win_rate:.1f}% | {pct_text(avg_pnl)} | {pct_text(avg_1d)} | {pct_text(avg_3d)} | {pct_text(avg_5d)} | {rule_breaks or 0}")


def cmd_stats(_args):
    init_db()
    stats = get_trade_stats()
    if not stats:
        print("暂无已平仓记录，先积累几笔交易再看统计。")
        return
    win_rate = round(stats['wins'] / stats['total'] * 100, 1) if stats['total'] else 0
    print("交易统计")
    print(f"  总交易: {stats['total']}  胜: {stats['wins']}  负: {stats['losses']}  胜率: {win_rate}%")
    print(f"  平均盈亏: {pct_text(stats['avg_pnl'])}  平均盈利: {pct_text(stats['avg_win'])}  平均亏损: {pct_text(stats['avg_loss'])}")
    print(f"  累计盈亏金额: {money_text(stats.get('total_pnl_amount'))}")
    if stats['avg_win'] and stats['avg_loss'] and stats['avg_loss'] != 0:
        print(f"  盈亏比: {abs(round(stats['avg_win'] / stats['avg_loss'], 2))}")
    print(f"  守纪律: {stats['rule_follow']}  破纪律: {stats['rule_break']}")


def build_parser():
    parser = argparse.ArgumentParser(description='简单交易记录工具')
    sub = parser.add_subparsers(dest='cmd')

    init_parser = sub.add_parser('init', help='初始化/升级交易记录表')
    init_parser.set_defaults(func=cmd_init)

    buy_parser = sub.add_parser('buy', help='记录买入')
    buy_parser.add_argument('code', help='股票代码，如 002015 或 sz.002015')
    buy_parser.add_argument('name', help='股票名称')
    buy_parser.add_argument('price', type=float, help='实际买入价')
    buy_parser.add_argument('--shares', type=int, help='买入股数')
    buy_parser.add_argument('--amount', type=float, help='买入金额，不填则用 price*shares')
    buy_parser.add_argument('--strategy', default='', help='策略 S1/S2/S3/S4')
    buy_parser.add_argument('--score', type=int, default=0, help='评分')
    buy_parser.add_argument('--grade', default='', help='等级 A/B')
    buy_parser.add_argument('--mode', default='', help='方案或模式备注，如 B')
    buy_parser.add_argument('--market-mode', default='', help='市场模式 M1-M5')
    buy_parser.add_argument('--emotion', default='', help='情绪周期，如 分歧/发酵')
    buy_parser.add_argument('--concept-stage', default='', help='概念阶段，如 主线/新晋发酵')
    buy_parser.add_argument('--concept', default='', help='概念名称')
    buy_parser.add_argument('--industry', default='', help='行业/板块')
    buy_parser.add_argument('--entry', nargs=2, type=float, metavar=('LOW', 'HIGH'), help='计划买入区间')
    buy_parser.add_argument('--stop', type=float, help='硬止损')
    buy_parser.add_argument('--soft-stop', type=float, help='软止损')
    buy_parser.add_argument('--target', type=float, help='止盈1')
    buy_parser.add_argument('--target2', type=float, help='止盈2/移动止盈参考')
    buy_parser.add_argument('--pos', help='仓位，如 1/16、0.0625、6.25')
    buy_parser.add_argument('--source', default='manual', help='来源，如 offline_screener/manual')
    buy_parser.add_argument('--status', default='', help='买点状态，如 可买/小仓/等回踩')
    buy_parser.add_argument('--confidence', default='', help='置信度：高/中/低/不可判断')
    buy_parser.add_argument('--evidence', default='', help='推荐时的核心证据，便于复盘校准')
    buy_parser.add_argument('--invalidation', default='', help='逻辑失效/反证条件，触发后不再坚持原判断')
    buy_parser.add_argument('--risk', default='', help='主要不确定因素或风险点')
    buy_parser.add_argument('--expected', default='', help='验证周期，如 次日/3日/5日/D2')
    buy_parser.add_argument('--date', help='买入日期 YYYY-MM-DD')
    buy_parser.add_argument('--remark', default='', help='备注')
    buy_parser.add_argument('--sysver', default=CURRENT_SYSVER, help=f'系统版本号，默认当前 {CURRENT_SYSVER}（改逻辑后 bump CURRENT_SYSVER 即可）')
    buy_parser.set_defaults(func=cmd_buy)

    sell_parser = sub.add_parser('sell', help='记录卖出/平仓')
    sell_parser.add_argument('id', type=int, help='买入记录ID')
    sell_parser.add_argument('price', type=float, help='实际卖出价')
    sell_parser.add_argument('--reason', default='', help='卖出原因，如 止盈1/止损/D2清仓/手动')
    sell_parser.add_argument('--rule', type=int, default=1, help='是否遵守纪律 1=是 0=否')
    sell_parser.add_argument('--date', help='卖出日期 YYYY-MM-DD')
    sell_parser.add_argument('--remark', default='', help='备注')
    sell_parser.set_defaults(func=cmd_sell)

    open_parser = sub.add_parser('open', help='查看当前未平仓记录')
    open_parser.set_defaults(func=cmd_open)

    history_parser = sub.add_parser('history', help='查看最近交易记录')
    history_parser.add_argument('-n', type=int, default=20, help='显示条数')
    history_parser.set_defaults(func=cmd_history)

    review_parser = sub.add_parser('review', help='记录一次预测/交易复盘结果')
    review_parser.add_argument('id', type=int, help='买入记录ID')
    review_parser.add_argument('--result', required=True, help='复盘结果，如 方向正确/买点错误/反证触发/执行变形')
    review_parser.add_argument('--notes', required=True, help='复盘备注：次日/3日/5日表现和归因')
    review_parser.add_argument('--date', help='复盘日期 YYYY-MM-DD')
    review_parser.add_argument('--pnl-1d', type=float, help='建议后1个交易日收益率，如 3.2 表示 +3.2%%')
    review_parser.add_argument('--pnl-3d', type=float, help='建议后3个交易日收益率')
    review_parser.add_argument('--pnl-5d', type=float, help='建议后5个交易日收益率')
    review_parser.set_defaults(func=cmd_review)

    calibration_parser = sub.add_parser('calibration', help='按置信度/策略/模式统计预测校准表现')
    calibration_parser.add_argument('--by', choices=['confidence', 'strategy', 'mode', 'version'], default='confidence', help='分组字段')
    calibration_parser.set_defaults(func=cmd_calibration)

    stats_parser = sub.add_parser('stats', help='查看胜率/盈亏/纪律统计')
    stats_parser.set_defaults(func=cmd_stats)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
