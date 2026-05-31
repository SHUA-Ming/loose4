#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
兼容入口：原 akshare/东财实时选股器已切换为腾讯实时行情 + 新浪日K。

东财 A 股实时/个股日K 在当前环境经常 RemoteDisconnected，实际筛选逻辑
改由同目录 qq_screener.py 执行。保留本文件名，避免旧提示词或命令失效。
"""
import runpy
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_QQ_SCREENER = Path(__file__).resolve().with_name("qq_screener.py")
runpy.run_path(str(_QQ_SCREENER), run_name="__main__")
