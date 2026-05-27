#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared import path bootstrap for the local tool system."""
from pathlib import Path
import sys

TOOL_ROOT = Path(__file__).resolve().parents[1]
TOOL_SUBDIRS = (
    "00_公共核心",
    "10_数据更新",
    "20_市场环境",
    "30_候选短股",
    "40_个股分析",
    "50_交易记录",
    "60_候选ETF",
    "70_长期持有股票",
)


def ensure_tool_paths():
    for subdir in reversed(TOOL_SUBDIRS):
        folder = TOOL_ROOT / subdir
        if folder.exists():
            folder_str = str(folder)
            if folder_str not in sys.path:
                sys.path.insert(0, folder_str)
