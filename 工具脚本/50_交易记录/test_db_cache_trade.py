#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
sys.path.insert(0, str(COMMON_DIR))

import db_cache


class PartialCashoutAccountingTests(unittest.TestCase):
    def test_final_pnl_combines_cashout_and_remaining_position(self):
        pnl_pct, pnl_amount = db_cache.calculate_trade_pnl(
            buy_price=100,
            original_shares=300,
            remaining_shares=150,
            realized_pnl=3750,
            sell_price=130,
        )
        self.assertEqual(pnl_amount, 8250)
        self.assertEqual(pnl_pct, 27.5)

    def test_trade_without_cashout_keeps_original_calculation(self):
        pnl_pct, pnl_amount = db_cache.calculate_trade_pnl(100, 300, 300, 0, 104)
        self.assertEqual(pnl_amount, 1200)
        self.assertEqual(pnl_pct, 4.0)

    def test_add_trade_sql_keeps_placeholder_and_parameter_counts_equal(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.lastrowid = 7
        conn.cursor.return_value = cursor
        with patch.object(db_cache, "init_db"), patch.object(db_cache, "get_connection", return_value=conn):
            trade_id = db_cache.add_trade(
                code="sz.000001", name="测试", buy_date="2026-08-07", buy_price=10,
                shares=300, expected_horizon="D2", sysver="v8",
            )
        sql, params = cursor.execute.call_args.args
        self.assertEqual(sql.count("%s"), len(params))
        self.assertEqual(trade_id, 7)
        self.assertEqual(params[13:17], (10, 300, 300, 3000.0))


if __name__ == "__main__":
    unittest.main()
