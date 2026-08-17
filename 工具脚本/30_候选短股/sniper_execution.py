#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短线狙击执行层。

该模块不负责 S1~S4 形态选股，只把已经入池的候选转成可审计的尾盘执行结论：

1. 尾盘三项确认决定“可买 / 小仓 / 仅观察”；
2. D1 延续分只用于从可执行票中选“主狙击 + 备用”；
3. 市场闸门只在这一层授权，不改写上游形态候选池。

函数保持纯函数，方便单元测试和后续 walk-forward 校准。
"""

ACTIONABLE_STATUSES = ("可买", "小仓")


def apply_tail_confirmation(base_status, confirmation_count):
    """将价格/盈亏比买点与尾盘三项确认合并。

    - 3/3：基础为“可买”才保留“可买”；
    - 2/3：最多“小仓”；
    - <2/3：只能“仅观察”；
    - 等回踩/等确认等价格状态不被尾盘确认覆盖。
    """
    if base_status not in ACTIONABLE_STATUSES:
        return base_status
    count = max(0, min(3, int(confirmation_count or 0)))
    if count < 2:
        return "仅观察"
    if count == 2 or base_status == "小仓":
        return "小仓"
    return "可买"


def continuation_score(candidate):
    """计算 0~100 的 D1 延续排序分，不替代任何硬否决。"""
    score = 15 * max(0, min(3, int(candidate.get("_tail_confirm_count", 0) or 0)))

    score += {"龙头": 15, "跟风": 8, "补涨": 0}.get(candidate.get("tier"), 0)
    score += {
        "持续主线": 15,
        "上升轮动": 12,
        "强势板块": 10,
        "普通轮动": 3,
        "退潮": 0,
    }.get(candidate.get("concept_stage"), 0)
    score += {"A": 10, "B": 5}.get(candidate.get("grade"), 0)
    score += {"可买": 10, "小仓": 5}.get(candidate.get("_buy_status"), 0)
    score += {"明日优先": 5, "降权行业": 2, "未分级": 0, "禁入行业": -25}.get(
        candidate.get("tomorrow_bucket"), 0
    )
    return max(0, min(100, int(round(score))))


def is_execution_eligible(candidate, mode, position_modifier):
    """判断候选是否能进入“主狙击/备用”实盘短名单。"""
    if position_modifier is None or float(position_modifier) <= 0:
        return False
    if candidate.get("tomorrow_bucket") == "禁入行业":
        return False
    if candidate.get("_buy_status") not in ACTIONABLE_STATUSES:
        return False
    if mode == "M5":
        return bool(candidate.get("_m5_exception_eligible")) and candidate.get("_buy_status") == "可买"
    return True


def select_execution_targets(candidates, mode, position_modifier, limit=2):
    """返回最多一只主狙击和一只备用，不修改原候选顺序。"""
    eligible = [c for c in candidates if is_execution_eligible(c, mode, position_modifier)]
    for candidate in eligible:
        candidate["_continuation_score"] = continuation_score(candidate)

    def sort_key(candidate):
        return (
            -candidate.get("_continuation_score", 0),
            0 if candidate.get("_buy_status") == "可买" else 1,
            int(candidate.get("_strategy_priority", 9)),
            0 if candidate.get("tier") == "龙头" else 1,
            -float(candidate.get("score", 0) or 0),
            str(candidate.get("code", "")),
        )

    return sorted(eligible, key=sort_key)[: max(0, int(limit))]
