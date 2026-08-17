#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unittest.mock import patch

from realtime_guard import (
    decide_buy,
    decide_sell,
    default_exit_targets,
    hold_authorization_active,
    resolve_exit_targets,
)


def sell_args():
    return SimpleNamespace(
        trail_pct=None,
        meltdown_pct=6.0,
        exit_time="14:40",
        close_check_time="14:50",
    )


class ExitTargetTests(unittest.TestCase):
    def test_new_defaults_split_first_cashout_and_strong_target(self):
        self.assertEqual(default_exit_targets("S2", 100), (102.25, 104.0))
        self.assertEqual(default_exit_targets("S4", 100), (102.5, 106.0))

    def test_new_s2_default_sells_half_at_d1_target(self):
        trade = {"buy_price": 100, "strategy": "S2"}
        row = {"price": 102.25, "high": 102.25}
        action, reasons = decide_sell(sell_args(), trade, row, {}, {})
        self.assertEqual(action, "卖50%")
        self.assertIn("102.25", reasons[0])

    def test_new_s4_default_sells_half_at_d1_target(self):
        trade = {"buy_price": 100, "strategy": "S4"}
        row = {"price": 102.5, "high": 102.5}
        action, _ = decide_sell(sell_args(), trade, row, {}, {})
        self.assertEqual(action, "卖50%")

    def test_legacy_s4_explicit_six_percent_target_keeps_one_third(self):
        trade = {"buy_price": 100, "strategy": "S4", "target_price": 106}
        self.assertEqual(resolve_exit_targets(trade, "S4", 100), (106.0, 109.0))
        row = {"price": 106, "high": 106}
        action, _ = decide_sell(sell_args(), trade, row, {}, {})
        self.assertEqual(action, "卖1/3")


class BuyAuthorizationTests(unittest.TestCase):
    def test_backup_requires_main_slot_release(self):
        args = SimpleNamespace(execution_role="backup", main_released=False)
        action, reasons, command = decide_buy(args, {"price": 10}, {}, {})
        self.assertEqual(action, "禁止买入")
        self.assertIn("--main-released", reasons[0])
        self.assertIsNone(command)


class RollingHoldTests(unittest.TestCase):
    def rolling_trade(self, until=None, protect=102.0, auth_date=None):
        auth_date = auth_date or dt.date.today().isoformat()
        until = until or (dt.date.fromisoformat(auth_date) + dt.timedelta(days=3)).isoformat()
        auth_price = 105.0
        next_target = 110.0
        return {
            "buy_date": "2026-08-01",
            "buy_price": 100,
            "strategy": "S2",
            "expected_horizon": "D2",
            "target_price": 110,
            "target2_price": 120,
            "hold_status": "rolling",
            "hold_auth_score": 4,
            "hold_cashout_done": 1,
            "hold_auth_checks": "market,sector,price_volume,reward_risk",
            "hold_auth_date": auth_date,
            "hold_auth_until": until,
            "hold_auth_price": auth_price,
            "hold_protect_price": protect,
            "hold_next_target": next_target,
            "hold_reward_risk": (next_target - auth_price) / (auth_price - protect),
            "hold_auth_evidence": "市场、板块、量价和赔率均通过",
            "hold_auth_invalidation": "板块退潮或保护线失守",
        }

    def test_authorization_requires_rolling_score_and_unexpired_date(self):
        self.assertTrue(hold_authorization_active(self.rolling_trade(auth_date="2026-08-07", until="2026-08-10"), as_of="2026-08-10"))
        self.assertFalse(hold_authorization_active(self.rolling_trade(auth_date="2026-08-07", until="2026-08-09"), as_of="2026-08-10"))
        trade = self.rolling_trade(auth_date="2026-08-07", until="2026-08-10")
        trade["hold_auth_score"] = 3
        self.assertFalse(hold_authorization_active(trade, as_of="2026-08-10"))

    def test_incomplete_authorization_cannot_override_expiry(self):
        trade = self.rolling_trade(auth_date="2026-08-07", until="2026-08-10")
        trade["hold_cashout_done"] = 0
        self.assertFalse(hold_authorization_active(trade, as_of="2026-08-08"))
        trade = self.rolling_trade(auth_date="2026-08-07", until="2026-08-10")
        trade["hold_auth_checks"] = "market,sector,relative,price_volume"
        self.assertFalse(hold_authorization_active(trade, as_of="2026-08-08"))

    def test_active_authorization_overrides_d2_expiry(self):
        with patch("realtime_guard.load_trade_dates", return_value=["2026-08-01", "2026-08-02", "2026-08-03"]), \
             patch("realtime_guard.now_ge", return_value=True):
            action, reasons = decide_sell(
                sell_args(), self.rolling_trade(), {"price": 103, "high": 103}, {}, {}
            )
        self.assertEqual(action, "继续持有")
        self.assertTrue(any("R1授权有效" in reason for reason in reasons))

    def test_expired_authorization_restores_default_expiry(self):
        with patch("realtime_guard.load_trade_dates", return_value=["2026-08-01", "2026-08-02", "2026-08-03"]), \
             patch("realtime_guard.now_ge", return_value=True):
            action, reasons = decide_sell(
                sell_args(), self.rolling_trade(until="2000-01-01"), {"price": 103, "high": 103}, {}, {}
            )
        self.assertEqual(action, "到期全清")
        self.assertIn("无有效R1授权", reasons[0])

    def test_profit_protection_ends_authorization(self):
        with patch("realtime_guard.load_trade_dates", return_value=["2026-08-01", "2026-08-02", "2026-08-03"]), \
             patch("realtime_guard.now_ge", return_value=True):
            action, reasons = decide_sell(
                sell_args(), self.rolling_trade(protect=103), {"price": 102.5, "high": 103}, {}, {}
            )
        self.assertEqual(action, "授权保护全清")
        self.assertIn("利润保护线", reasons[0])

    def test_next_target_realizes_remaining_position(self):
        with patch("realtime_guard.load_trade_dates", return_value=["2026-08-01", "2026-08-02", "2026-08-03"]), \
             patch("realtime_guard.now_ge", return_value=True):
            action, reasons = decide_sell(
                sell_args(), self.rolling_trade(), {"price": 110, "high": 110}, {}, {}
            )
        self.assertEqual(action, "授权目标止盈")
        self.assertIn("R1下一压力", reasons[0])


if __name__ == "__main__":
    unittest.main()
