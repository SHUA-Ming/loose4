#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sniper_execution import (
    apply_tail_confirmation,
    continuation_score,
    is_execution_eligible,
    select_execution_targets,
)


class TailConfirmationTests(unittest.TestCase):
    def test_three_of_three_keeps_actionable_status(self):
        self.assertEqual(apply_tail_confirmation("可买", 3), "可买")
        self.assertEqual(apply_tail_confirmation("小仓", 3), "小仓")

    def test_two_of_three_downgrades_to_small(self):
        self.assertEqual(apply_tail_confirmation("可买", 2), "小仓")

    def test_fewer_than_two_is_watch_only(self):
        self.assertEqual(apply_tail_confirmation("可买", 1), "仅观察")

    def test_price_wait_status_is_not_overridden(self):
        self.assertEqual(apply_tail_confirmation("等回踩", 3), "等回踩")


class ExecutionSelectionTests(unittest.TestCase):
    def candidate(self, code, score, tail=3, tier="龙头", status="可买", exception=False):
        return {
            "code": code,
            "score": 8,
            "grade": "A",
            "tier": tier,
            "concept_stage": "持续主线",
            "tomorrow_bucket": "明日优先",
            "_buy_status": status,
            "_tail_confirm_count": tail,
            "_m5_exception_eligible": exception,
            "_strategy_priority": 0,
            "_continuation_score": score,
        }

    def test_position_zero_blocks_everything(self):
        c = self.candidate("A", 100)
        self.assertFalse(is_execution_eligible(c, "M3", 0))

    def test_m5_requires_explicit_exception_and_full_confirmation(self):
        ordinary = self.candidate("A", 90, exception=False)
        exception = self.candidate("B", 90, exception=True)
        self.assertFalse(is_execution_eligible(ordinary, "M5", 1 / 3))
        self.assertTrue(is_execution_eligible(exception, "M5", 1 / 3))

    def test_selects_only_main_and_backup_by_continuation_score(self):
        weak = self.candidate("A", 0, tail=2, tier="跟风", status="小仓")
        strong = self.candidate("B", 0, tail=3, tier="龙头")
        middle = self.candidate("C", 0, tail=3, tier="跟风")
        selected = select_execution_targets([weak, middle, strong], "M3", 1.0)
        self.assertEqual([c["code"] for c in selected], ["B", "C"])
        self.assertGreater(continuation_score(strong), continuation_score(weak))


if __name__ == "__main__":
    unittest.main()
