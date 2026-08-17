#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import unittest
import datetime as dt
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from premarket_reversal_radar import (
    DEFAULT_PARAMS,
    bs_to_yahoo,
    compute_overnight_features,
    evaluate_auction,
    evaluate_intraday,
    evaluate_overnight,
    evaluate_setup,
    evaluate_feature_row,
)


class RadarRuleTests(unittest.TestCase):
    def test_symbol_mapping(self):
        self.assertEqual(bs_to_yahoo("sz.300308"), "300308.SZ")
        self.assertEqual(bs_to_yahoo("sh.688012"), "688012.SS")

    def test_setup_needs_drawdown_and_breadth(self):
        good = {
            "coverage": 1.0,
            "median_ret20": -22.0,
            "median_ret10": -13.0,
            "below_ma20_ratio": 0.82,
            "median_dist_ma20": -11.0,
            "prior_day_median_pct": -2.2,
        }
        passed, score, _ = evaluate_setup(good, DEFAULT_PARAMS)
        self.assertTrue(passed)
        self.assertGreaterEqual(score, DEFAULT_PARAMS.setup_min_score)

        narrow = dict(good, below_ma20_ratio=0.30)
        self.assertFalse(evaluate_setup(narrow, DEFAULT_PARAMS)[0])

    def test_macro_without_tech_cannot_trigger(self):
        macro_only = {
            "available": True,
            "nasdaq_pct": 0.2,
            "sox_pct": 0.4,
            "tnx_bp": -10.0,
            "vix_pct": -12.0,
            "brent_pct": -5.0,
            "brent_prior5_pct": 8.0,
        }
        self.assertFalse(evaluate_overnight(macro_only, DEFAULT_PARAMS)[0])

        today_like = dict(macro_only, nasdaq_pct=2.1)
        self.assertTrue(evaluate_overnight(today_like, DEFAULT_PARAMS)[0])

    def test_today_like_trigger_needs_rate_help_not_demand_slump_oil(self):
        today_like = {
            "available": True,
            "nasdaq_pct": 2.1,
            "sox_pct": 1.1,
            "tnx_bp": -5.9,
            "vix_pct": -0.8,
            "brent_pct": -6.1,
            "brent_prior5_pct": -4.2,
        }
        passed, score, reasons = evaluate_overnight(today_like, DEFAULT_PARAMS)
        self.assertTrue(passed)
        self.assertEqual(score, 2.5)
        self.assertFalse(any("油价冲击逆转" in reason for reason in reasons))

        no_rate_help = dict(today_like, tnx_bp=0.0)
        self.assertFalse(evaluate_overnight(no_rate_help, DEFAULT_PARAMS)[0])

    def test_tnx_yahoo_yield_change_is_converted_to_basis_points(self):
        dates = [dt.date(2026, 8, 2), dt.date(2026, 8, 3)]

        def frame(closes):
            return pd.DataFrame({"date": dates, "close": closes})

        features = compute_overnight_features(
            {
                "nasdaq": frame([100.0, 102.1]),
                "sox": frame([100.0, 101.1]),
                "tnx": frame([4.745, 4.686]),
                "vix": frame([20.0, 19.8]),
                "brent": frame([70.0, 66.0]),
            },
            dt.date(2026, 8, 4),
        )
        self.assertAlmostEqual(features["tnx_bp"], -5.9, places=6)

    def test_auction_requires_two_core_stocks(self):
        one_core = {
            "core_gaps": [2.2, 0.8, -0.2],
            "core_median_gap": 0.8,
            "cyb_gap": 1.0,
            "style_gap": 1.2,
        }
        self.assertFalse(evaluate_auction(one_core, DEFAULT_PARAMS)[0])

        linked = dict(one_core, core_gaps=[2.2, 1.8, 0.4], core_median_gap=1.8)
        self.assertTrue(evaluate_auction(linked, DEFAULT_PARAMS)[0])

    def test_intraday_rejects_narrow_leader_pull(self):
        narrow = {
            "coverage": 1.0,
            "breadth": 0.35,
            "median_pct": -0.2,
            "core_above_open": 3,
            "style_spread": 1.5,
            "volume_speed": 1.4,
        }
        self.assertFalse(evaluate_intraday(narrow, DEFAULT_PARAMS)[0])

        broad = dict(narrow, breadth=0.82, median_pct=1.6)
        self.assertTrue(evaluate_intraday(broad, DEFAULT_PARAMS)[0])

    def test_overheated_signal_is_identified_but_not_actionable(self):
        row = {
            "setup": {
                "coverage": 1.0, "median_ret20": -22.0, "median_ret10": -13.0,
                "below_ma20_ratio": 0.82, "median_dist_ma20": -11.0,
                "prior_day_median_pct": -2.2,
            },
            "overnight": {
                "available": True, "nasdaq_pct": 2.2, "sox_pct": 3.2,
                "tnx_bp": -6.0, "vix_pct": -6.0,
                "brent_pct": 0.0, "brent_prior5_pct": 0.0,
            },
            "auction": {
                "core_gaps": [12.0, 13.0, 11.0], "core_median_gap": 12.0,
                "cyb_gap": 3.0, "style_gap": 2.0,
            },
            "intraday": {
                "coverage": 1.0, "breadth": 0.95, "median_pct": 8.0,
                "core_above_open": 3, "style_spread": 3.0, "volume_speed": 1.5,
            },
        }
        verdict = evaluate_feature_row(row, DEFAULT_PARAMS)
        self.assertTrue(verdict["signal"])
        self.assertTrue(verdict["auction_overheated"])
        self.assertFalse(verdict["actionable"])


if __name__ == "__main__":
    unittest.main()
