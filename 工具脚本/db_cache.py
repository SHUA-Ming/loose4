#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票数据缓存 - MySQL 数据库模块（V2, 迁移自 SQLite）
替代原有的 SQLite 文件缓存，接口保持不变，存储后端改为 MySQL。

用法:
    from db_cache import init_db, read_kline, upsert_kline, get_last_date, get_all_codes

数据库: MySQL stock_local (localhost:3306)

表结构:
    kline_daily(code, date, open, high, low, close, volume, amount, turn, pctChg)
    stock_industry(code, code_name, industry, industry_class, update_date)
    sector_daily(industry, date, avg_pct, up_count, down_count, flat_count,
                 total_amount, avg_turn, top_gainer, top_gainer_pct, stock_count)
    主键: 各表见建表语句
"""

import datetime as _dt
import mysql.connector
import pandas as pd

# MySQL 连接配置
MYSQL_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': '123456',
    'database': 'stock_local',
    'charset': 'utf8mb4',
    'autocommit': False,
    'use_pure': True,
}

# 向后兼容别名（原 SQLite 路径，现已废弃，保留避免 ImportError）
DB_PATH = 'mysql://root@localhost:3306/stock_local'

KLINE_COLS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg']
NUMERIC_COLS = ['open', 'high', 'low', 'close', 'volume', 'amount', 'turn', 'pctChg']

MAX_ROWS_PER_STOCK = 400   # 保留，不再强制截断（MySQL 有分区）


# ─────────────────────────────────────────────────────────────
# SQLite 兼容层：让下游脚本无需修改即可使用 MySQL
# ─────────────────────────────────────────────────────────────

def _cvt_row(row):
    """将 MySQL 返回的 datetime.date / datetime.datetime 转为 ISO 字符串，模拟 SQLite 行为"""
    if row is None:
        return None
    return tuple(
        v.isoformat() if isinstance(v, (_dt.date, _dt.datetime)) else v
        for v in row
    )


class _CompatCursor:
    """MySQL cursor 包装器，接受 SQLite 风格的 ? 占位符"""

    def __init__(self, real_cursor):
        self._c = real_cursor

    def execute(self, sql, params=None):
        sql = sql.replace('?', '%s')
        if params is not None:
            self._c.execute(sql, list(params))
        else:
            self._c.execute(sql)
        return self

    def executemany(self, sql, params_seq):
        sql = sql.replace('?', '%s')
        self._c.executemany(sql, params_seq)
        return self

    def fetchone(self):
        return _cvt_row(self._c.fetchone())

    def fetchall(self):
        return [_cvt_row(r) for r in self._c.fetchall()]

    def fetchmany(self, size=None):
        rows = self._c.fetchmany(size) if size else self._c.fetchmany()
        return [_cvt_row(r) for r in rows]

    @property
    def description(self):
        return self._c.description

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def lastrowid(self):
        return self._c.lastrowid

    def close(self):
        self._c.close()

    def __iter__(self):
        for row in self._c:
            yield _cvt_row(row)


class _MysqlCompatConn:
    """MySQL connection 包装器，模拟 sqlite3.Connection 接口"""

    def __init__(self, real_conn):
        self._conn = real_conn

    def cursor(self):
        return _CompatCursor(self._conn.cursor())

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql, params_seq):
        cur = self.cursor()
        cur.executemany(sql, params_seq)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_connection():
    """获取数据库连接（返回 MySQL 兼容包装器，接口与 sqlite3.Connection 一致）"""
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    return _MysqlCompatConn(conn)


def init_db():
    """初始化数据库表（幂等，可重复调用）"""
    conn = get_connection()

    creates = [
        # ═══ K线日数据表 ═══
        """CREATE TABLE IF NOT EXISTS kline_daily (
            code    VARCHAR(16)  NOT NULL,
            date    DATE         NOT NULL,
            open    DOUBLE,
            high    DOUBLE,
            low     DOUBLE,
            close   DOUBLE,
            volume  DOUBLE,
            amount  DOUBLE,
            turn    DOUBLE,
            pctChg  DOUBLE,
            PRIMARY KEY (code, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # ═══ 股票行业映射表 ═══
        """CREATE TABLE IF NOT EXISTS stock_industry (
            code            VARCHAR(16)  NOT NULL,
            code_name       VARCHAR(64),
            industry        VARCHAR(128),
            industry_class  VARCHAR(128),
            update_date     DATE,
            PRIMARY KEY (code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # ═══ 行业板块每日统计表 ═══
        """CREATE TABLE IF NOT EXISTS sector_daily (
            industry        VARCHAR(128)  NOT NULL,
            date            DATE          NOT NULL,
            avg_pct         DOUBLE,
            up_count        INT,
            down_count      INT,
            flat_count      INT,
            total_amount    DOUBLE,
            avg_turn        DOUBLE,
            top_gainer      VARCHAR(16),
            top_gainer_pct  DOUBLE,
            stock_count     INT,
            PRIMARY KEY (industry, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # ═══ 交易记录表 ═══
        """CREATE TABLE IF NOT EXISTS trade_log (
            id           BIGINT       AUTO_INCREMENT PRIMARY KEY,
            code         VARCHAR(16)  NOT NULL,
            name         VARCHAR(64),
            mode         VARCHAR(16),
            grade        VARCHAR(16),
            score        INT,
            buy_date     DATE         NOT NULL,
            buy_price    DOUBLE,
            sell_date    DATE,
            sell_price   DOUBLE,
            pnl_pct      DOUBLE,
            stop_price   DOUBLE,
            target_price DOUBLE,
            `position`   DOUBLE,
            follow_rule  INT,
            remark       TEXT,
            created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]

    for sql in creates:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            # 表若已存在则跳过（正常情况）
            try:
                conn.rollback()
            except Exception:
                pass

    conn.close()


def read_kline(code, start_date=None, end_date=None):
    """
    读取某只股票的K线数据，返回 DataFrame。
    code: 如 'sh.600000'
    start_date / end_date: 可选，格式 'YYYY-MM-DD'
    """
    conn = get_connection()
    sql = "SELECT date, open, high, low, close, volume, amount, turn, pctChg FROM kline_daily WHERE code = ?"
    params = [code]
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    sql += " ORDER BY date"

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    if df.empty:
        return df

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def upsert_kline(code, df):
    """
    插入或更新K线数据（自动去重，date相同则覆盖）。
    code: 如 'sh.600000'
    df: DataFrame，至少包含 KLINE_COLS 中的列
    """
    if df is None or df.empty:
        return 0

    conn = get_connection()
    cursor = conn.cursor()

    inserted = 0
    for _, row in df.iterrows():
        cursor.execute("""
            INSERT INTO kline_daily (code, date, open, high, low, close, volume, amount, turn, pctChg)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open), high=VALUES(high), low=VALUES(low),
                close=VALUES(close), volume=VALUES(volume), amount=VALUES(amount),
                turn=VALUES(turn), pctChg=VALUES(pctChg)
        """, (
            code,
            str(row['date'])[:10],
            _to_float(row.get('open')),
            _to_float(row.get('high')),
            _to_float(row.get('low')),
            _to_float(row.get('close')),
            _to_float(row.get('volume')),
            _to_float(row.get('amount')),
            _to_float(row.get('turn')),
            _to_float(row.get('pctChg')),
        ))
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def upsert_kline_batch(code, df):
    """
    批量插入（比 upsert_kline 快很多，适合迁移用）。
    """
    if df is None or df.empty:
        return 0

    conn = get_connection()
    rows = []
    for _, row in df.iterrows():
        rows.append((
            code,
            str(row['date'])[:10],
            _to_float(row.get('open')),
            _to_float(row.get('high')),
            _to_float(row.get('low')),
            _to_float(row.get('close')),
            _to_float(row.get('volume')),
            _to_float(row.get('amount')),
            _to_float(row.get('turn')),
            _to_float(row.get('pctChg')),
        ))

    conn.executemany("""
        INSERT INTO kline_daily (code, date, open, high, low, close, volume, amount, turn, pctChg)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            open=VALUES(open), high=VALUES(high), low=VALUES(low),
            close=VALUES(close), volume=VALUES(volume), amount=VALUES(amount),
            turn=VALUES(turn), pctChg=VALUES(pctChg)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def get_last_date(code):
    """获取某只股票缓存中最新的日期，无数据返回 None"""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT MAX(date) FROM kline_daily WHERE code = ?", (code,)
    )
    result = cursor.fetchone()[0]
    conn.close()
    return result


def get_all_codes():
    """获取数据库中所有股票代码列表"""
    conn = get_connection()
    cursor = conn.execute("SELECT DISTINCT code FROM kline_daily ORDER BY code")
    codes = [row[0] for row in cursor.fetchall()]
    conn.close()
    return codes


def get_row_count(code=None):
    """获取记录数。code=None 时返回总数。"""
    conn = get_connection()
    if code:
        cursor = conn.execute("SELECT COUNT(*) FROM kline_daily WHERE code = ?", (code,))
    else:
        cursor = conn.execute("SELECT COUNT(*) FROM kline_daily")
    result = cursor.fetchone()[0]
    conn.close()
    return result


def _to_float(val):
    """安全转 float"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════
# 行业映射相关函数
# ═══════════════════════════════════════════

def upsert_industry(rows):
    """
    批量写入行业映射。
    rows: list of (code, code_name, industry, industry_class, update_date)
    """
    if not rows:
        return 0
    conn = get_connection()
    conn.executemany("""
        INSERT INTO stock_industry (code, code_name, industry, industry_class, update_date)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            code_name=VALUES(code_name),
            industry=VALUES(industry),
            industry_class=VALUES(industry_class),
            update_date=VALUES(update_date)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def get_industry_map():
    """获取全部股票→行业映射，返回 dict: code → industry"""
    conn = get_connection()
    cursor = conn.execute("SELECT code, industry FROM stock_industry WHERE industry IS NOT NULL")
    result = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return result


def get_industry_list():
    """获取所有行业名称列表"""
    conn = get_connection()
    cursor = conn.execute("SELECT DISTINCT industry FROM stock_industry WHERE industry IS NOT NULL ORDER BY industry")
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result


def get_stocks_in_industry(industry):
    """获取某个行业下所有股票代码"""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT code FROM stock_industry WHERE industry = ?", (industry,)
    )
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result


# ═══════════════════════════════════════════
# 板块每日统计相关函数
# ═══════════════════════════════════════════

def upsert_sector_daily(rows):
    """
    批量写入板块每日统计。
    rows: list of (industry, date, avg_pct, up_count, down_count, flat_count,
                   total_amount, avg_turn, top_gainer, top_gainer_pct, stock_count)
    """
    if not rows:
        return 0
    conn = get_connection()
    conn.executemany("""
        INSERT INTO sector_daily (industry, date, avg_pct, up_count, down_count,
            flat_count, total_amount, avg_turn, top_gainer, top_gainer_pct, stock_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            avg_pct=VALUES(avg_pct), up_count=VALUES(up_count),
            down_count=VALUES(down_count), flat_count=VALUES(flat_count),
            total_amount=VALUES(total_amount), avg_turn=VALUES(avg_turn),
            top_gainer=VALUES(top_gainer), top_gainer_pct=VALUES(top_gainer_pct),
            stock_count=VALUES(stock_count)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def read_sector_daily(industry=None, start_date=None, end_date=None):
    """
    读取板块日统计数据，返回 DataFrame。
    industry: 可选，指定行业名
    """
    conn = get_connection()
    sql = "SELECT * FROM sector_daily WHERE 1=1"
    params = []
    if industry:
        sql += " AND industry = ?"
        params.append(industry)
    if start_date:
        sql += " AND date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND date <= ?"
        params.append(end_date)
    sql += " ORDER BY industry, date"
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def get_sector_snapshot(date):
    """获取某日所有板块的表现快照，按涨跌幅排序"""
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM sector_daily WHERE date = ? ORDER BY avg_pct DESC",
        conn, params=[date]
    )
    conn.close()
    return df


# ═══════════════════════════════════════════
# 交易记录相关函数
# ═══════════════════════════════════════════

def add_trade(code, name, buy_date, buy_price, mode='A', grade='A', score=0,
              stop_price=None, target_price=None, position=None, remark=None):
    """新增一条买入记录"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trade_log (code, name, mode, grade, score, buy_date, buy_price,
                               stop_price, target_price, position, remark)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (code, name, mode, grade, score, buy_date, buy_price,
          stop_price, target_price, position, remark))
    conn.commit()
    trade_id = cur.lastrowid
    cur.close()
    conn.close()
    return trade_id


def close_trade(trade_id, sell_date, sell_price, follow_rule=1, remark=None):
    """平仓：填入卖出信息并自动计算盈亏"""
    conn = get_connection()
    cur = conn.execute("SELECT buy_price FROM trade_log WHERE id = %s", (trade_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    buy_price = row[0]
    pnl_pct = round((sell_price - buy_price) / buy_price * 100, 2) if buy_price else None
    if remark:
        conn.execute("""
            UPDATE trade_log SET sell_date=%s, sell_price=%s, pnl_pct=%s,
                follow_rule=%s, remark=CONCAT(IFNULL(remark,''), '; ', %s) WHERE id=%s
        """, (sell_date, sell_price, pnl_pct, follow_rule, remark, trade_id))
    else:
        conn.execute("""
            UPDATE trade_log SET sell_date=%s, sell_price=%s, pnl_pct=%s,
                follow_rule=%s WHERE id=%s
        """, (sell_date, sell_price, pnl_pct, follow_rule, trade_id))
    conn.commit()
    conn.close()
    return True


def get_open_trades():
    """获取所有未平仓记录"""
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM trade_log WHERE sell_date IS NULL ORDER BY buy_date DESC", conn)
    conn.close()
    return df


def get_trade_history(limit=50):
    """获取最近的交易记录"""
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM trade_log ORDER BY buy_date DESC LIMIT ?", conn, params=[limit])
    conn.close()
    return df


def get_trade_stats():
    """统计已平仓交易的胜率和盈亏比"""
    conn = get_connection()
    cur = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
               ROUND(AVG(pnl_pct), 2) as avg_pnl,
               ROUND(AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END), 2) as avg_win,
               ROUND(AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct END), 2) as avg_loss,
               SUM(CASE WHEN follow_rule = 1 THEN 1 ELSE 0 END) as rule_follow,
               SUM(CASE WHEN follow_rule = 0 THEN 1 ELSE 0 END) as rule_break
        FROM trade_log WHERE sell_date IS NOT NULL
    """)
    row = cur.fetchone()
    conn.close()
    if not row or row[0] == 0:
        return None
    cols = ['total', 'wins', 'losses', 'avg_pnl', 'avg_win', 'avg_loss', 'rule_follow', 'rule_break']
    return dict(zip(cols, row))


# 首次导入自动建表
init_db()
