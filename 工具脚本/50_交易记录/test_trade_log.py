#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trade_log import cmd_cashout, validate_hold_authorization


class HoldAuthorizationValidationTests(unittest.TestCase):
    def trade(self, prior_protect=None, cashout_done=1):
        return {
            "buy_price": 100,
            "hold_protect_price": prior_protect,
            "hold_cashout_done": cashout_done,
        }

    def validate(self, **overrides):
        params = {
            "trade": self.trade(),
            "current_price": 105,
            "protect_price": 102,
            "next_target": 110,
            "checks": "market,sector,price_volume,reward_risk",
            "auth_date": "2026-08-07",
            "auth_until": "2026-08-10",
            "evidence": "市场、板块、量价和赔率均通过",
            "invalidation": "板块退潮或保护线失守",
        }
        params.update(overrides)
        return validate_hold_authorization(**params)

    def test_valid_four_of_five_authorization(self):
        errors, checks = self.validate()
        self.assertEqual(errors, [])
        self.assertEqual(len(checks), 4)

    def test_first_cashout_and_two_percent_profit_are_mandatory(self):
        errors, _ = self.validate(trade=self.trade(cashout_done=0), current_price=101.99)
        self.assertTrue(any("首次兑现" in error for error in errors))
        self.assertTrue(any("盈利不足" in error for error in errors))

    def test_core_checks_cannot_be_replaced_by_relative_strength(self):
        errors, _ = self.validate(checks="market,sector,relative,price_volume")
        self.assertTrue(any("reward_risk" in error for error in errors))

    def test_protection_line_locks_profit_and_never_moves_down(self):
        errors, _ = self.validate(protect_price=100.5)
        self.assertTrue(any("成本+1%" in error for error in errors))
        errors, _ = self.validate(trade=self.trade(prior_protect=103), protect_price=102)
        self.assertTrue(any("只能上移" in error for error in errors))

    def test_authorization_is_short_lived(self):
        errors, _ = self.validate(auth_until="2026-08-20")
        self.assertTrue(any("最长7个自然日" in error for error in errors))

    def test_remaining_reward_risk_must_be_at_least_one_point_five(self):
        errors, _ = self.validate(next_target=108)
        self.assertTrue(any("赔率不足1.5" in error for error in errors))


class CashoutRecordingTests(unittest.TestCase):
    def test_cashout_keeps_trade_open_and_reduces_remaining_shares(self):
        conn = MagicMock()
        selected = MagicMock()
        selected.fetchone.return_value = (
            42, "sz.002463", "沪电股份", 115.94, 300, None, "open", None, 0
        )
        conn.execute.side_effect = [selected, MagicMock()]
        args = SimpleNamespace(
            id=42, price=125.87, shares=150, date="2026-08-10",
            reason="D1首次兑现", rule=1, remark="先锁一半利润",
        )
        with patch("trade_log.init_db"), patch("trade_log.get_connection", return_value=conn):
            cmd_cashout(args)
        update_sql, update_params = conn.execute.call_args_list[1].args
        self.assertIn("hold_cashout_done=1", update_sql)
        self.assertEqual(update_params[0], 150)
        self.assertEqual(update_params[3], 150)
        self.assertEqual(update_params[-1], 42)
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
