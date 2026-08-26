#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import importlib.util
import math
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("em_board_daily_repair.py")
SPEC = importlib.util.spec_from_file_location("em_board_daily_repair", MODULE_PATH)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def row(date, close, pct, volume=1_000_000):
    return {
        "board_code": "BKTEST",
        "level": 3,
        "date": date,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": volume,
        "amount": 123.0,
        "amplitude": 4.0,
        "pctChg": pct,
        "change_amount": None,
        "turn": 1.0,
        "data_source": "legacy_unknown",
        "quality_status": "unverified",
        "updated_at": "2026-08-25 15:00:00",
    }


class RepairTests(unittest.TestCase):
    def test_classifies_only_the_low_scale_block(self):
        rows = [
            row("2026-08-20", 10_000, 0.0),
            row("2026-08-21", 100, 1.0),
            row("2026-08-22", 101, 1.0),
            row("2026-08-25", 10_300, 1.0),
        ]
        self.assertEqual(repair.classify_board(rows), [False, True, True, False])

    def test_bridge_matches_both_official_anchors(self):
        rows = [
            row("2026-08-20", 10_000, 0.0),
            row("2026-08-21", 100, 1.0),
            row("2026-08-22", 101, 1.0),
            row("2026-08-25", 10_300, 1.0),
        ]
        closes = repair.bridge_closes(rows, 1, 2)
        expected_last = 10_300 / 1.01
        self.assertTrue(math.isclose(closes[-1], expected_last, rel_tol=1e-12))
        self.assertGreater(closes[0], 9_000)
        self.assertLess(closes[0], 11_000)

    def test_rebuild_labels_derivation_and_converts_volume_to_lots(self):
        rows = [
            row("2026-08-20", 10_000, 0.0),
            row("2026-08-21", 100, 1.0),
            row("2026-08-22", 101, 1.0),
            row("2026-08-25", 10_300, 1.0),
        ]
        rebuilt = repair.rebuild_board(rows, [False, True, True, False])
        self.assertEqual(rebuilt[1]["data_source"], "constituent_bridge")
        self.assertEqual(rebuilt[1]["quality_status"], "derived")
        self.assertEqual(rebuilt[1]["volume"], 10_000)
        self.assertGreaterEqual(rebuilt[1]["high"], rebuilt[1]["close"])
        self.assertLessEqual(rebuilt[1]["low"], rebuilt[1]["close"])


if __name__ == "__main__":
    unittest.main()
