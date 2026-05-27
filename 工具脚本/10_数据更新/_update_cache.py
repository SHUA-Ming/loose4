#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据缓存更新入口（已迁移至新浪K线接口，不再依赖 baostock）
等同于直接运行 _update_cache_ak.py
"""
import os, sys

_dir = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('NO_PROXY', '*')

# 执行新浪版更新脚本
_ak_path = os.path.join(_dir, '_update_cache_ak.py')
with open(_ak_path, encoding='utf-8') as _f:
    exec(compile(_f.read(), _ak_path, 'exec'), {'__file__': _ak_path, '__name__': '__main__'})

