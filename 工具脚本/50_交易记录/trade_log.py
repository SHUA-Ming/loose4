#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单交易记录工具。

最常用：
    python 工具脚本/50_交易记录/trade_log.py buy 002015 协鑫能科 23.02 --strategy S4 --grade A --score 8 --shares 400 --entry 22.54 23.14 --stop 22.31 --soft-stop 22.66 --target 24.38 --target2 25.07 --pos 1/16 --confidence 中 --evidence "M3+主线发酵+S4达标" --invalidation "跌破MA5且概念转弱" --remark "按计划竞价买入"
    python 工具脚本/50_交易记录/trade_log.py cashout 1 24.38 --shares 200 --reason D1首次兑现 --rule 1 --remark "到压力位/+2%~2.5%卖50%"
    python 工具脚本/50_交易记录/trade_log.py review 1 --result 买点正确 --pnl-1d 3.2 --pnl-3d 5.8 --notes "次日冲高到目标位，未触发反证"
    python 工具脚本/50_交易记录/trade_log.py authorize-hold 1 --price 24.80 --protect 24.10 --next-target 26.00 --until 2026-08-11 --checks market,sector,price_volume,reward_risk --evidence "主线延续且尾盘承接" --invalidation "板块退潮或放量跌破保护线"
    python 工具脚本/50_交易记录/trade_log.py calibration --by confidence
    python 工具脚本/50_交易记录/trade_log.py open
    python 工具脚本/50_交易记录/trade_log.py history
    python 工具脚本/50_交易记录/trade_log.py stats
"""

import argparse
import datetime as dt
import sys
import warnings
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy.*")

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))

from project_paths import ensure_tool_paths
ensure_tool_paths()
from db_cache import add_trade, close_trade, get_connection, get_open_trades, get_trade_history, get_trade_stats, init_db

# 当前系统版本号：每次改动选股/风控/情绪逻辑就 +1，并在《交易体系/系统变更日志.md》记一条。
# buy 不显式传 --sysver 时自动打这个版本，保证每笔都带版本、月末能按版本分段校准。
CURRENT_SYSVER = 'v8'

ROLLING_CHECKS = {"market", "sector", "relative", "price_volume", "reward_risk"}
ROLLING_REQUIRED = {"market", "sector", "price_volume", "reward_risk"}


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


def _is_blank(value):
    if value is None:
        return True
    try:
        if value != value:  # NaN
            return True
    except Exception:
        pass
    return str(value).strip() == ''


def _validate_buy_args(args):
    """新买入必须带完整交易计划；历史补录可显式 --force 绕过。"""
    if args.force:
        return True
    missing = []
    checks = [
        ('strategy', args.strategy, '--strategy 策略'),
        ('market_mode', args.market_mode, '--market-mode 市场M档'),
        ('entry', args.entry, '--entry 买入区间'),
        ('stop', args.stop, '--stop 硬止损'),
        ('soft_stop', args.soft_stop, '--soft-stop 软止损'),
        ('pos', args.pos, '--pos 仓位'),
        ('confidence', args.confidence, '--confidence 置信度'),
        ('invalidation', args.invalidation, '--invalidation 反证/逻辑失效条件'),
    ]
    for _key, value, label in checks:
        if _key == 'entry':
            if not value or len(value) != 2:
                missing.append(label)
        elif _key == 'pos':
            if _is_blank(value) or parse_position(value) is None:
                missing.append(label)
        elif _is_blank(value):
            missing.append(label)
    if not missing:
        return True
    print("买入记录被拦截：缺少完整交易计划。")
    print("缺失字段：")
    for item in missing:
        print(f"  - {item}")
    print("如只是补录历史/拆分仓位，请加 --force，并在 --remark 说明原因。")
    return False


def _load_trade_dates():
    conn = get_connection(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT date
            FROM kline_daily
            WHERE code='sh.000001'
            ORDER BY date
            """
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0])[:10] for row in rows if row and row[0]]


def _trading_age(buy_date, trade_dates):
    if _is_blank(buy_date) or not trade_dates:
        return None
    b = str(buy_date)[:10]
    start_idx = None
    for idx, d in enumerate(trade_dates):
        if d >= b:
            start_idx = idx
            break
    if start_idx is None:
        return None
    return max(0, len(trade_dates) - 1 - start_idx)


def _infer_horizon(row):
    parts = []
    for col in ('expected_horizon', 'plan_source', 'mode', 'remark', 'buy_status'):
        if col in row and not _is_blank(row[col]):
            parts.append(str(row[col]))
    text = ' '.join(parts)
    if 'D3' in text or '方案B' in text or '收盘选股' in text:
        return 3
    if 'D2' in text or '方案A' in text or '方案C' in text or '盘后' in text or '尾盘' in text:
        return 2
    return None


def _parse_iso_date(value):
    try:
        return dt.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _parse_hold_checks(value):
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value or "").split(",")
    return {str(item).strip() for item in items if str(item).strip()}


def validate_hold_authorization(
    trade,
    current_price,
    protect_price,
    next_target,
    checks,
    auth_date,
    auth_until,
    evidence,
    invalidation,
):
    """R1滚动持有授权的纯校验；返回(errors, normalized_checks)。"""
    errors = []
    normalized = _parse_hold_checks(checks)
    unknown = normalized - ROLLING_CHECKS
    missing_required = ROLLING_REQUIRED - normalized
    buy_price = float(trade.get("buy_price") or 0)
    prior_protect = trade.get("hold_protect_price")
    prior_protect = None if _is_blank(prior_protect) else float(prior_protect)
    cashout_done = int(trade.get("hold_cashout_done") or 0) == 1
    auth_day = _parse_iso_date(auth_date)
    until_day = _parse_iso_date(auth_until)

    if not cashout_done:
        errors.append("必须先完成D1首次兑现，再授权剩余仓滚动持有")
    if buy_price <= 0:
        errors.append("交易记录缺买入价")
    elif current_price < buy_price * 1.02:
        errors.append(f"当前盈利不足+2%（授权线 {buy_price * 1.02:.2f}）")
    if unknown:
        errors.append("未知检查项: " + ",".join(sorted(unknown)))
    if len(normalized) < 4:
        errors.append("五项检查至少通过4项")
    if missing_required:
        errors.append("必过项缺失: " + ",".join(sorted(missing_required)))
    if buy_price > 0 and protect_price < buy_price * 1.01:
        errors.append(f"保护线必须至少锁定成本+1%（最低 {buy_price * 1.01:.2f}）")
    if protect_price >= current_price:
        errors.append("保护线必须低于当前价")
    if prior_protect is not None and protect_price < prior_protect:
        errors.append(f"保护线只能上移，不能低于上次 {prior_protect:.2f}")
    if next_target <= current_price:
        errors.append("下一目标/压力位必须高于当前价")
    elif protect_price < current_price:
        reward_risk = (next_target - current_price) / (current_price - protect_price)
        if reward_risk < 1.5:
            errors.append(f"剩余赔率不足1.5（当前 {reward_risk:.2f}）")
    if _is_blank(evidence):
        errors.append("必须写明R1事实证据")
    if _is_blank(invalidation):
        errors.append("必须写明R1失效条件")
    if auth_day is None or until_day is None:
        errors.append("授权日期格式必须为 YYYY-MM-DD")
    elif until_day <= auth_day:
        errors.append("授权截止日必须晚于授权日")
    elif (until_day - auth_day).days > 7:
        errors.append("单次授权最长7个自然日；原则上只覆盖下一交易日")
    return errors, normalized


def _rolling_auth_active(row, as_of):
    if str(row.get("hold_status") or "") != "rolling":
        return False
    checks = _parse_hold_checks(row.get("hold_auth_checks"))
    score = 0 if _is_blank(row.get("hold_auth_score")) else int(row.get("hold_auth_score"))
    cashout_done = int(row.get("hold_cashout_done") or 0) == 1
    buy_price = 0 if _is_blank(row.get("buy_price")) else float(row.get("buy_price"))
    auth_price = 0 if _is_blank(row.get("hold_auth_price")) else float(row.get("hold_auth_price"))
    protect = 0 if _is_blank(row.get("hold_protect_price")) else float(row.get("hold_protect_price"))
    next_target = 0 if _is_blank(row.get("hold_next_target")) else float(row.get("hold_next_target"))
    reward_risk = 0 if _is_blank(row.get("hold_reward_risk")) else float(row.get("hold_reward_risk"))
    auth_day = _parse_iso_date(row.get("hold_auth_date"))
    until_day = _parse_iso_date(row.get("hold_auth_until"))
    as_of_day = _parse_iso_date(as_of)
    return all([
        cashout_done,
        score >= 4,
        ROLLING_REQUIRED.issubset(checks),
        buy_price > 0,
        auth_price >= buy_price * 1.02,
        protect >= buy_price * 1.01,
        protect < auth_price < next_target,
        reward_risk >= 1.5,
        auth_day is not None,
        until_day is not None,
        as_of_day is not None,
        until_day > auth_day,
        until_day >= as_of_day,
        not _is_blank(row.get("hold_auth_evidence")),
        not _is_blank(row.get("hold_auth_invalidation")),
    ])


def _current_mode_snapshot():
    try:
        from market_mode import get_mode_params
        mode = get_mode_params()
        return mode.get('mode'), mode.get('cycle_phase'), None
    except Exception as exc:
        return None, None, str(exc)


def cmd_init(_args):
    init_db()
    print("OK: trade_log 表已就绪。")
    print("字段够用：策略、评分、买入区间、止损止盈、仓位、置信度、证据、反证条件、复盘结果都会记录。")


def cmd_buy(args):
    if not _validate_buy_args(args):
        sys.exit(2)
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


def cmd_cashout(args):
    """记录首次部分兑现；保留交易为open，供剩余仓继续管理。"""
    init_db()
    cashout_date = args.date or today_str()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT id,code,name,buy_price,shares,remaining_shares,status,sell_date,hold_cashout_done
        FROM trade_log WHERE id=%s
        """,
        (args.id,),
    ).fetchone()
    if not row:
        conn.close()
        raise SystemExit(f"未找到交易 ID={args.id}")
    trade_id, code, name, buy_price, original_shares, remaining_shares, status, sell_date, cashout_done = row
    if str(status or "") != "open" or sell_date is not None:
        conn.close()
        raise SystemExit(f"ID={args.id} 已平仓，不能记录部分兑现")
    if int(cashout_done or 0) == 1:
        conn.close()
        raise SystemExit(f"ID={args.id} 已记录首次兑现；后续卖完请用 sell，不能重复记首次兑现")
    remaining = remaining_shares if remaining_shares is not None else original_shares
    if remaining is None or int(remaining) <= 0:
        conn.close()
        raise SystemExit(f"ID={args.id} 缺持仓股数，先补齐 shares 后再记录部分兑现")
    if args.shares <= 0 or args.shares >= int(remaining):
        conn.close()
        raise SystemExit(f"部分兑现股数必须在1~{int(remaining)-1}之间；全部卖完请用 sell")
    if not buy_price or buy_price <= 0:
        conn.close()
        raise SystemExit(f"ID={args.id} 缺买入价，无法计算已实现利润")

    new_remaining = int(remaining) - args.shares
    realized = round((args.price - float(buy_price)) * args.shares, 2)
    note = args.reason or "D1首次兑现"
    if args.remark:
        note += f"·{args.remark}"
    conn.execute(
        """
        UPDATE trade_log
        SET remaining_shares=%s, hold_cashout_done=1,
            first_cashout_date=%s, first_cashout_price=%s, first_cashout_shares=%s,
            realized_pnl_amount=COALESCE(realized_pnl_amount,0)+%s,
            remark=CONCAT(IFNULL(remark,''), IF(remark IS NULL OR remark='', '', '; '), %s),
            follow_rule=%s
        WHERE id=%s
        """,
        (new_remaining, cashout_date, args.price, args.shares, realized, note, args.rule, args.id),
    )
    conn.commit()
    conn.close()
    print(f"OK: 首次部分兑现已记录 ID={trade_id}  {code} {name or ''}")
    print(f"  {cashout_date} 卖出 {args.shares}股 @ {args.price:.2f}  已实现盈亏 {realized:+.2f}")
    print(f"  原始 {int(original_shares or remaining)}股  剩余 {new_remaining}股  交易保持open")


def cmd_authorize_hold(args):
    """给已首次兑现的盈利剩余仓签发一次R1滚动持有授权。"""
    init_db()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT id,code,name,buy_price,status,sell_date,hold_protect_price,hold_auth_count,
               hold_cashout_done,remaining_shares
        FROM trade_log WHERE id=%s
        """,
        (args.id,),
    ).fetchone()
    if not row:
        conn.close()
        raise SystemExit(f"未找到交易 ID={args.id}")
    trade = {
        "id": row[0], "code": row[1], "name": row[2], "buy_price": row[3],
        "status": row[4], "sell_date": row[5], "hold_protect_price": row[6],
        "hold_auth_count": row[7] or 0, "hold_cashout_done": row[8] or 0,
        "remaining_shares": row[9],
    }
    if str(trade["status"] or "") != "open" or trade["sell_date"] is not None:
        conn.close()
        raise SystemExit(f"ID={args.id} 已平仓，不能授权持有")

    auth_date = args.as_of or today_str()
    errors, checks = validate_hold_authorization(
        trade=trade,
        current_price=args.price,
        protect_price=args.protect,
        next_target=args.next_target,
        checks=args.checks,
        auth_date=auth_date,
        auth_until=args.until,
        evidence=args.evidence,
        invalidation=args.invalidation,
    )
    if errors:
        conn.close()
        print("R1滚动持有授权被拦截：")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(2)

    score = len(checks)
    check_text = ",".join(sorted(checks))
    reward_risk = (args.next_target - args.price) / (args.price - args.protect)
    evidence = f"checks={check_text}; cashout_done=yes; {args.evidence}".strip()
    note = f"R1授权{auth_date}→{args.until};保护线{args.protect:.2f};目标{args.next_target:.2f};RR{reward_risk:.2f};{score}/5"
    conn.execute(
        """
        UPDATE trade_log
        SET hold_status='rolling', hold_auth_date=%s, hold_auth_until=%s,
            hold_auth_score=%s, hold_cashout_done=1, hold_auth_checks=%s,
            hold_auth_price=%s, hold_protect_price=%s, hold_next_target=%s,
            hold_reward_risk=%s, hold_auth_evidence=%s,
            hold_auth_invalidation=%s, hold_auth_count=COALESCE(hold_auth_count,0)+1,
            hold_auth_sysver=%s,
            remark=CONCAT(IFNULL(remark,''), IF(remark IS NULL OR remark='', '', '; '), %s)
        WHERE id=%s
        """,
        (
            auth_date, args.until, score, check_text, args.price, args.protect,
            args.next_target, reward_risk, evidence,
            args.invalidation, CURRENT_SYSVER, note, args.id,
        ),
    )
    conn.commit()
    conn.close()
    print(f"OK: R1滚动持有已授权 ID={args.id}  {trade['code']} {trade['name'] or ''}")
    print(f"  授权: {auth_date} → {args.until}  得分 {score}/5  当前价 {args.price:.2f}")
    print(f"  利润保护线: {args.protect:.2f}  下一目标: {args.next_target:.2f}  剩余赔率: {reward_risk:.2f}")
    print(f"  检查项: {check_text}")
    print(f"  证据: {args.evidence}")
    print(f"  失效: {args.invalidation}")
    print("  到期前必须重新授权；未续签则恢复D2/D3默认退出。")


def cmd_open(_args):
    init_db()
    df = get_open_trades()
    if df.empty:
        print("当前无持仓记录。")
        return
    cols = [
        'id', 'code', 'name', 'strategy', 'sysver', 'grade', 'score', 'buy_date', 'buy_price',
        'shares', 'remaining_shares', 'amount', 'entry_low', 'entry_high', 'stop_price', 'soft_stop',
        'target_price', 'target2_price', 'position', 'confidence_level',
        'invalidation_condition', 'hold_cashout_done', 'first_cashout_date',
        'first_cashout_price', 'first_cashout_shares', 'realized_pnl_amount',
        'hold_status', 'hold_auth_until', 'hold_auth_score',
        'hold_auth_price', 'hold_protect_price', 'hold_next_target', 'hold_reward_risk',
        'hold_auth_count', 'remark'
    ]
    cols = [col for col in cols if col in df.columns]
    print(df[cols].to_string(index=False))


def cmd_audit_open(_args):
    init_db()
    df = get_open_trades()
    if df.empty:
        print("当前无持仓记录。")
        return

    trade_dates = _load_trade_dates()
    latest_trade_date = trade_dates[-1] if trade_dates else today_str()
    current_mode, current_phase, mode_error = _current_mode_snapshot()
    print(f"持仓审计  数据最新交易日={latest_trade_date}")
    if mode_error:
        print(f"当前M档读取失败：{mode_error}")
    else:
        print(f"当前市场环境：{current_mode or '-'} / {current_phase or '-'}")

    required_cols = [
        ('strategy', '策略'),
        ('market_mode', '买入M档'),
        ('stop_price', '硬止损'),
        ('soft_stop', '软止损'),
        ('position', '仓位'),
        ('confidence_level', '置信度'),
        ('invalidation_condition', '反证条件'),
    ]

    total_issues = 0
    for _, row in df.iterrows():
        issues = []
        buy_status = str(row.get('buy_status') or '').strip()
        no_valid_entry = any(tag in buy_status for tag in ('不合规', '无策略匹配', '应放弃', '放弃', '仅观察'))
        row_required_cols = list(required_cols)
        if not no_valid_entry:
            row_required_cols.extend([
                ('entry_low', '买入区下沿'),
                ('entry_high', '买入区上沿'),
            ])
        missing = [label for col, label in row_required_cols if col not in row or _is_blank(row[col])]
        if missing:
            issues.append("缺字段: " + "/".join(missing))

        age = _trading_age(row.get('buy_date'), trade_dates)
        horizon = _infer_horizon(row)
        rolling_active = _rolling_auth_active(row, latest_trade_date)
        if horizon is not None and age is not None and age >= horizon:
            if rolling_active:
                print(
                    f"R1有效 ID={row.get('id')} {row.get('code')} {row.get('name')}  "
                    f"授权至{str(row.get('hold_auth_until'))[:10]}  "
                    f"得分{int(row.get('hold_auth_score'))}/5  "
                    f"保护线{money_text(row.get('hold_protect_price'))}"
                )
            else:
                suffix = "，滚动授权已过期/无效" if str(row.get('hold_status') or '') == 'rolling' else "，未获R1授权"
                issues.append(f"D{horizon}到期: 已持有D{age}{suffix}")
        elif horizon is None and age is not None and age >= 2:
            issues.append(f"未写D2/D3周期: 已持有D{age}, 需人工确认是否到期")

        row_mode = str(row.get('market_mode') or row.get('mode') or '').strip()
        if row_mode == 'M5':
            issues.append("买入环境为M5")
        if current_mode == 'M5':
            issues.append("当前环境M5, 只处理风险不开新仓")

        if not issues:
            continue

        total_issues += 1
        print()
        print(f"ID={row.get('id')} {row.get('code')} {row.get('name')}  买入={row.get('buy_date')}@{money_text(row.get('buy_price'))}")
        for issue in issues:
            print(f"  - {issue}")

    if total_issues == 0:
        print("审计通过：当前持仓未发现缺计划、到期或M5环境风险。")
    else:
        print(f"\n审计完成：{total_issues} 条持仓需要处理/补字段。")


def cmd_history(args):
    init_db()
    df = get_trade_history(limit=args.n)
    if df.empty:
        print("暂无交易记录。")
        return
    cols = [
        'id', 'code', 'name', 'strategy', 'sysver', 'grade', 'score', 'buy_date', 'buy_price',
        'sell_date', 'sell_price', 'sell_reason', 'pnl_pct', 'pnl_amount',
        'confidence_level', 'first_cashout_date', 'first_cashout_price',
        'first_cashout_shares', 'realized_pnl_amount', 'hold_status',
        'hold_auth_count', 'hold_auth_sysver',
        'review_result', 'follow_rule', 'remark'
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
        'hold': 'hold_status',
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
    buy_parser.add_argument('--force', action='store_true', help='仅用于历史补录/拆分仓位；跳过完整交易计划校验')
    buy_parser.set_defaults(func=cmd_buy)

    sell_parser = sub.add_parser('sell', help='记录卖出/平仓')
    sell_parser.add_argument('id', type=int, help='买入记录ID')
    sell_parser.add_argument('price', type=float, help='实际卖出价')
    sell_parser.add_argument('--reason', default='', help='卖出原因，如 止盈1/止损/D2清仓/手动')
    sell_parser.add_argument('--rule', type=int, default=1, help='是否遵守纪律 1=是 0=否')
    sell_parser.add_argument('--date', help='卖出日期 YYYY-MM-DD')
    sell_parser.add_argument('--remark', default='', help='备注')
    sell_parser.set_defaults(func=cmd_sell)

    cashout_parser = sub.add_parser('cashout', help='记录D1首次部分兑现；不关闭交易，剩余仓可申请R1')
    cashout_parser.add_argument('id', type=int, help='未平仓交易ID')
    cashout_parser.add_argument('price', type=float, help='实际兑现价格')
    cashout_parser.add_argument('--shares', type=int, required=True, help='本次实际卖出股数；必须小于当前剩余股数')
    cashout_parser.add_argument('--reason', default='D1首次兑现', help='兑现原因')
    cashout_parser.add_argument('--rule', type=int, default=1, help='是否遵守纪律 1=是 0=否')
    cashout_parser.add_argument('--date', help='兑现日期 YYYY-MM-DD')
    cashout_parser.add_argument('--remark', default='', help='备注')
    cashout_parser.set_defaults(func=cmd_cashout)

    hold_parser = sub.add_parser('authorize-hold', help='给已首次兑现的盈利剩余仓签发一次R1滚动持有授权')
    hold_parser.add_argument('id', type=int, help='未平仓交易ID')
    hold_parser.add_argument('--price', type=float, required=True, help='授权时当前价/收盘价，必须至少盈利2%%')
    hold_parser.add_argument('--protect', type=float, required=True, help='新的利润保护线，至少成本+1%%且只能上移')
    hold_parser.add_argument('--next-target', type=float, required=True, help='下一压力位/兑现目标；相对保护线的剩余赔率必须≥1.5')
    hold_parser.add_argument('--until', required=True, help='授权截止日 YYYY-MM-DD，原则上为下一交易日')
    hold_parser.add_argument('--as-of', help='授权日期 YYYY-MM-DD，默认今天')
    hold_parser.add_argument(
        '--checks', required=True,
        help='通过项，逗号分隔：market,sector,relative,price_volume,reward_risk；至少4项，且除relative外四项必过',
    )
    hold_parser.add_argument('--evidence', required=True, help='延长持有的事实证据')
    hold_parser.add_argument('--invalidation', required=True, help='授权失效条件')
    hold_parser.set_defaults(func=cmd_authorize_hold)

    open_parser = sub.add_parser('open', help='查看当前未平仓记录')
    open_parser.set_defaults(func=cmd_open)

    audit_parser = sub.add_parser('audit-open', help='审计当前持仓缺字段、D2/D3到期和M5风险')
    audit_parser.set_defaults(func=cmd_audit_open)

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
    calibration_parser.add_argument('--by', choices=['confidence', 'strategy', 'mode', 'version', 'hold'], default='confidence', help='分组字段')
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
