#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票数据缓存 - MySQL 数据库模块（V2, 迁移自 SQLite）
替代原有的 SQLite 文件缓存，接口保持不变，存储后端改为 MySQL。

用法:
    from db_cache import init_db, get_connection, upsert_kline_batch, get_last_date

数据库: MySQL stock_local (localhost:3306)

表结构:
    kline_daily(code, date, open, high, low, close, volume, amount, turn, pctChg)
    stock_industry(code, code_name, update_date)  # 股票池/名称保留表
    em_board_l1/em_board_l2/em_board_l3/em_stock_board_l3/em_board_daily
    trade_log(...)
    主键: 各表见建表语句
"""

import datetime as _dt
import mysql.connector
import pandas as pd

# MySQL 连接配置
MYSQL_CONFIG = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': '123456',
    'database': 'stock_local',
    'charset': 'utf8mb4',
    'autocommit': False,
    'use_pure': True,
}

# 向后兼容别名（原 SQLite 路径，现已废弃，保留避免 ImportError）
DB_PATH = 'mysql://root@127.0.0.1:3306/stock_local'

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


# 会话级锁等待上限（秒）。服务器默认被设成 31536000(365天)，
# 一旦只读长事务挡住 DDL，等待方会"卡死一年"。统一压到 60 秒快速失败。
SESSION_LOCK_WAIT_TIMEOUT = 60


def get_connection(readonly=False):
    """获取数据库连接（返回 MySQL 兼容包装器，接口与 sqlite3.Connection 一致）。

    readonly=True：开 autocommit，每条 SELECT 立即结束事务，不积累 MDL，
    避免只读脚本（选股/分析）的长事务把后续 DDL 卡死。纯查询脚本一律传 True。
    """
    config = dict(MYSQL_CONFIG)
    if readonly:
        config['autocommit'] = True
    conn = mysql.connector.connect(**config)
    # 护栏：无论服务器全局怎么设，本会话锁等待都不超过 60 秒，宁可报错也不挂死。
    try:
        cur = conn.cursor()
        cur.execute(f"SET SESSION lock_wait_timeout = {SESSION_LOCK_WAIT_TIMEOUT}")
        cur.execute(f"SET SESSION innodb_lock_wait_timeout = {min(SESSION_LOCK_WAIT_TIMEOUT, 50)}")
        cur.close()
        if not readonly:
            conn.commit()
    except Exception:
        pass
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

        # ═══ 东方财富三层行业板块：一级/二级/三级 + 个股三级绑定 ═══
        """CREATE TABLE IF NOT EXISTS em_board_l1 (
            id              INT          NOT NULL AUTO_INCREMENT,
            board_code      VARCHAR(16)  NOT NULL,
            board_name      VARCHAR(64)  NOT NULL,
            board_market    VARCHAR(8),
            source_index    INT,
            update_date     DATE,
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_em_l1_code (board_code),
            KEY idx_em_l1_name (board_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS em_board_l2 (
            id              INT          NOT NULL AUTO_INCREMENT,
            l1_id           INT          NOT NULL,
            board_code      VARCHAR(16)  NOT NULL,
            board_name      VARCHAR(64)  NOT NULL,
            board_market    VARCHAR(8),
            source_index    INT,
            update_date     DATE,
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_em_l2_code (board_code),
            KEY idx_em_l2_l1 (l1_id),
            KEY idx_em_l2_name (board_name),
            CONSTRAINT fk_em_l2_l1 FOREIGN KEY (l1_id) REFERENCES em_board_l1(id)
                ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS em_board_l3 (
            id              INT          NOT NULL AUTO_INCREMENT,
            l1_id           INT          NOT NULL,
            l2_id           INT          NOT NULL,
            board_code      VARCHAR(16)  NOT NULL,
            board_name      VARCHAR(64)  NOT NULL,
            board_market    VARCHAR(8),
            source_index    INT,
            update_date     DATE,
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_em_l3_code (board_code),
            KEY idx_em_l3_l1 (l1_id),
            KEY idx_em_l3_l2 (l2_id),
            KEY idx_em_l3_name (board_name),
            CONSTRAINT fk_em_l3_l1 FOREIGN KEY (l1_id) REFERENCES em_board_l1(id)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT fk_em_l3_l2 FOREIGN KEY (l2_id) REFERENCES em_board_l2(id)
                ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS em_stock_board_l3 (
            code            VARCHAR(16)  NOT NULL,
            code_name       VARCHAR(64),
            raw_code        VARCHAR(16),
            raw_market      VARCHAR(8),
            l3_id           INT          NOT NULL,
            labels          VARCHAR(128),
            update_date     DATE,
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (code),
            KEY idx_em_stock_l3 (l3_id),
            KEY idx_em_stock_raw (raw_market, raw_code),
            CONSTRAINT fk_em_stock_l3 FOREIGN KEY (l3_id) REFERENCES em_board_l3(id)
                ON UPDATE CASCADE ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS em_board_daily (
            board_code      VARCHAR(16)  NOT NULL,
            level           TINYINT      NOT NULL,
            date            DATE         NOT NULL,
            open            DOUBLE,
            high            DOUBLE,
            low             DOUBLE,
            close           DOUBLE,
            volume          DOUBLE,
            amount          DOUBLE,
            amplitude       DOUBLE,
            pctChg          DOUBLE,
            change_amount   DOUBLE,
            turn            DOUBLE,
            data_source     VARCHAR(32)  NOT NULL DEFAULT 'legacy_unknown',
            quality_status  VARCHAR(16)  NOT NULL DEFAULT 'unverified',
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (board_code, date),
            KEY idx_em_board_daily_level_date (level, date),
            KEY idx_em_board_daily_date (date),
            KEY idx_em_board_daily_source (data_source, quality_status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # 聚合/派生板块行情必须与东财官方板块指数物理隔离，禁止再混写。
        """CREATE TABLE IF NOT EXISTS em_board_daily_proxy (
            board_code      VARCHAR(16)  NOT NULL,
            level           TINYINT      NOT NULL,
            date            DATE         NOT NULL,
            open            DOUBLE,
            high            DOUBLE,
            low             DOUBLE,
            close           DOUBLE,
            volume          DOUBLE,
            amount          DOUBLE,
            amplitude       DOUBLE,
            pctChg          DOUBLE,
            change_amount   DOUBLE,
            turn            DOUBLE,
            proxy_method    VARCHAR(48)  NOT NULL,
            updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (board_code, date),
            KEY idx_em_board_proxy_level_date (level, date),
            KEY idx_em_board_proxy_method (proxy_method)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # 历史污染行先隔离备份，再从正式表移除/重建，确保可追溯与可恢复。
        """CREATE TABLE IF NOT EXISTS em_board_daily_quarantine (
            id                  BIGINT       NOT NULL AUTO_INCREMENT,
            repair_batch        VARCHAR(32)  NOT NULL,
            board_code          VARCHAR(16)  NOT NULL,
            level               TINYINT      NOT NULL,
            date                DATE         NOT NULL,
            open                DOUBLE,
            high                DOUBLE,
            low                 DOUBLE,
            close               DOUBLE,
            volume              DOUBLE,
            amount              DOUBLE,
            amplitude           DOUBLE,
            pctChg              DOUBLE,
            change_amount       DOUBLE,
            turn                DOUBLE,
            original_source     VARCHAR(32),
            original_quality    VARCHAR(16),
            quarantine_reason   VARCHAR(128) NOT NULL,
            original_updated_at TIMESTAMP    NULL,
            quarantined_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_em_board_quarantine_batch (repair_batch, board_code, date),
            KEY idx_em_board_quarantine_code_date (board_code, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # ═══ 交易记录表 ═══
        """CREATE TABLE IF NOT EXISTS trade_log (
            id           BIGINT       AUTO_INCREMENT PRIMARY KEY,
            code         VARCHAR(16)  NOT NULL,
            name         VARCHAR(64),
            trade_date   DATE,
            action       VARCHAR(16)  DEFAULT 'buy',
            status       VARCHAR(16)  DEFAULT 'open',
            strategy     VARCHAR(16),
            mode         VARCHAR(16),
            grade        VARCHAR(16),
            score        INT,
            concept_stage VARCHAR(64),
            concept_name  VARCHAR(128),
            industry      VARCHAR(128),
            entry_low     DOUBLE,
            entry_high    DOUBLE,
            buy_date     DATE         NOT NULL,
            buy_price    DOUBLE,
            shares        INT,
            remaining_shares INT,
            amount        DOUBLE,
            sell_date    DATE,
            sell_price   DOUBLE,
            sell_reason   VARCHAR(64),
            pnl_pct      DOUBLE,
            pnl_amount    DOUBLE,
            stop_price   DOUBLE,
            soft_stop     DOUBLE,
            target_price DOUBLE,
            target2_price DOUBLE,
            `position`   DOUBLE,
            plan_source   VARCHAR(64),
            buy_status    VARCHAR(32),
            emotion_phase VARCHAR(32),
            market_mode   VARCHAR(16),
            confidence_level VARCHAR(16),
            evidence_summary TEXT,
            invalidation_condition TEXT,
            risk_notes TEXT,
            expected_horizon VARCHAR(32),
            hold_status VARCHAR(16) DEFAULT 'short',
            hold_auth_date DATE,
            hold_auth_until DATE,
            hold_auth_score TINYINT,
            hold_cashout_done TINYINT DEFAULT 0,
            first_cashout_date DATE,
            first_cashout_price DOUBLE,
            first_cashout_shares INT,
            realized_pnl_amount DOUBLE DEFAULT 0,
            hold_auth_checks VARCHAR(160),
            hold_auth_price DOUBLE,
            hold_protect_price DOUBLE,
            hold_next_target DOUBLE,
            hold_reward_risk DOUBLE,
            hold_auth_evidence TEXT,
            hold_auth_invalidation TEXT,
            hold_auth_count INT DEFAULT 0,
            hold_auth_sysver VARCHAR(16),
            review_result VARCHAR(32),
            review_date DATE,
            pnl_1d DOUBLE,
            pnl_3d DOUBLE,
            pnl_5d DOUBLE,
            review_notes TEXT,
            follow_rule  INT,
            remark       TEXT,
            sysver       VARCHAR(16),
            created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_trade_status (status, trade_date),
            KEY idx_trade_code (code),
            KEY idx_trade_strategy (strategy, market_mode)
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

    _ensure_em_board_daily_columns(conn)
    _ensure_trade_log_columns(conn)

    conn.close()


def _ensure_em_board_daily_columns(conn):
    """Add source provenance to older em_board_daily installations."""
    columns = {
        row[0] for row in conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='em_board_daily'"
        ).fetchall()
    }
    additions = [
        (
            "data_source",
            "ALTER TABLE em_board_daily ADD COLUMN data_source VARCHAR(32) "
            "NOT NULL DEFAULT 'legacy_unknown' AFTER turn",
        ),
        (
            "quality_status",
            "ALTER TABLE em_board_daily ADD COLUMN quality_status VARCHAR(16) "
            "NOT NULL DEFAULT 'unverified' AFTER data_source",
        ),
    ]
    for name, sql in additions:
        if name in columns:
            continue
        conn.execute(sql)
        conn.commit()
    index_names = {
        row[0] for row in conn.execute(
            "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='em_board_daily'"
        ).fetchall()
    }
    if "idx_em_board_daily_source" not in index_names:
        conn.execute(
            "CREATE INDEX idx_em_board_daily_source "
            "ON em_board_daily(data_source, quality_status)"
        )
        conn.commit()


def _ensure_trade_log_columns(conn):
    """Older local DBs may have a smaller trade_log schema; extend it in place."""
    columns = {
        row[0] for row in conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'trade_log'"
        ).fetchall()
    }
    additions = [
        ('trade_date', "ALTER TABLE trade_log ADD COLUMN trade_date DATE AFTER name"),
        ('action', "ALTER TABLE trade_log ADD COLUMN action VARCHAR(16) DEFAULT 'buy' AFTER trade_date"),
        ('status', "ALTER TABLE trade_log ADD COLUMN status VARCHAR(16) DEFAULT 'open' AFTER action"),
        ('strategy', "ALTER TABLE trade_log ADD COLUMN strategy VARCHAR(16) AFTER status"),
        ('concept_stage', "ALTER TABLE trade_log ADD COLUMN concept_stage VARCHAR(64) AFTER score"),
        ('concept_name', "ALTER TABLE trade_log ADD COLUMN concept_name VARCHAR(128) AFTER concept_stage"),
        ('industry', "ALTER TABLE trade_log ADD COLUMN industry VARCHAR(128) AFTER concept_name"),
        ('entry_low', "ALTER TABLE trade_log ADD COLUMN entry_low DOUBLE AFTER industry"),
        ('entry_high', "ALTER TABLE trade_log ADD COLUMN entry_high DOUBLE AFTER entry_low"),
        ('shares', "ALTER TABLE trade_log ADD COLUMN shares INT AFTER buy_price"),
        ('remaining_shares', "ALTER TABLE trade_log ADD COLUMN remaining_shares INT AFTER shares"),
        ('amount', "ALTER TABLE trade_log ADD COLUMN amount DOUBLE AFTER remaining_shares"),
        ('sell_reason', "ALTER TABLE trade_log ADD COLUMN sell_reason VARCHAR(64) AFTER sell_price"),
        ('pnl_amount', "ALTER TABLE trade_log ADD COLUMN pnl_amount DOUBLE AFTER pnl_pct"),
        ('soft_stop', "ALTER TABLE trade_log ADD COLUMN soft_stop DOUBLE AFTER stop_price"),
        ('target2_price', "ALTER TABLE trade_log ADD COLUMN target2_price DOUBLE AFTER target_price"),
        ('plan_source', "ALTER TABLE trade_log ADD COLUMN plan_source VARCHAR(64) AFTER `position`"),
        ('buy_status', "ALTER TABLE trade_log ADD COLUMN buy_status VARCHAR(32) AFTER plan_source"),
        ('emotion_phase', "ALTER TABLE trade_log ADD COLUMN emotion_phase VARCHAR(32) AFTER buy_status"),
        ('market_mode', "ALTER TABLE trade_log ADD COLUMN market_mode VARCHAR(16) AFTER emotion_phase"),
        ('confidence_level', "ALTER TABLE trade_log ADD COLUMN confidence_level VARCHAR(16) AFTER market_mode"),
        ('evidence_summary', "ALTER TABLE trade_log ADD COLUMN evidence_summary TEXT AFTER confidence_level"),
        ('invalidation_condition', "ALTER TABLE trade_log ADD COLUMN invalidation_condition TEXT AFTER evidence_summary"),
        ('risk_notes', "ALTER TABLE trade_log ADD COLUMN risk_notes TEXT AFTER invalidation_condition"),
        ('expected_horizon', "ALTER TABLE trade_log ADD COLUMN expected_horizon VARCHAR(32) AFTER risk_notes"),
        ('hold_status', "ALTER TABLE trade_log ADD COLUMN hold_status VARCHAR(16) DEFAULT 'short' AFTER expected_horizon"),
        ('hold_auth_date', "ALTER TABLE trade_log ADD COLUMN hold_auth_date DATE AFTER hold_status"),
        ('hold_auth_until', "ALTER TABLE trade_log ADD COLUMN hold_auth_until DATE AFTER hold_auth_date"),
        ('hold_auth_score', "ALTER TABLE trade_log ADD COLUMN hold_auth_score TINYINT AFTER hold_auth_until"),
        ('hold_cashout_done', "ALTER TABLE trade_log ADD COLUMN hold_cashout_done TINYINT DEFAULT 0 AFTER hold_auth_score"),
        ('first_cashout_date', "ALTER TABLE trade_log ADD COLUMN first_cashout_date DATE AFTER hold_cashout_done"),
        ('first_cashout_price', "ALTER TABLE trade_log ADD COLUMN first_cashout_price DOUBLE AFTER first_cashout_date"),
        ('first_cashout_shares', "ALTER TABLE trade_log ADD COLUMN first_cashout_shares INT AFTER first_cashout_price"),
        ('realized_pnl_amount', "ALTER TABLE trade_log ADD COLUMN realized_pnl_amount DOUBLE DEFAULT 0 AFTER first_cashout_shares"),
        ('hold_auth_checks', "ALTER TABLE trade_log ADD COLUMN hold_auth_checks VARCHAR(160) AFTER realized_pnl_amount"),
        ('hold_auth_price', "ALTER TABLE trade_log ADD COLUMN hold_auth_price DOUBLE AFTER hold_auth_checks"),
        ('hold_protect_price', "ALTER TABLE trade_log ADD COLUMN hold_protect_price DOUBLE AFTER hold_auth_price"),
        ('hold_next_target', "ALTER TABLE trade_log ADD COLUMN hold_next_target DOUBLE AFTER hold_protect_price"),
        ('hold_reward_risk', "ALTER TABLE trade_log ADD COLUMN hold_reward_risk DOUBLE AFTER hold_next_target"),
        ('hold_auth_evidence', "ALTER TABLE trade_log ADD COLUMN hold_auth_evidence TEXT AFTER hold_reward_risk"),
        ('hold_auth_invalidation', "ALTER TABLE trade_log ADD COLUMN hold_auth_invalidation TEXT AFTER hold_auth_evidence"),
        ('hold_auth_count', "ALTER TABLE trade_log ADD COLUMN hold_auth_count INT DEFAULT 0 AFTER hold_auth_invalidation"),
        ('hold_auth_sysver', "ALTER TABLE trade_log ADD COLUMN hold_auth_sysver VARCHAR(16) AFTER hold_auth_count"),
        ('review_result', "ALTER TABLE trade_log ADD COLUMN review_result VARCHAR(32) AFTER hold_auth_sysver"),
        ('review_date', "ALTER TABLE trade_log ADD COLUMN review_date DATE AFTER review_result"),
        ('pnl_1d', "ALTER TABLE trade_log ADD COLUMN pnl_1d DOUBLE AFTER review_date"),
        ('pnl_3d', "ALTER TABLE trade_log ADD COLUMN pnl_3d DOUBLE AFTER pnl_1d"),
        ('pnl_5d', "ALTER TABLE trade_log ADD COLUMN pnl_5d DOUBLE AFTER pnl_3d"),
        ('review_notes', "ALTER TABLE trade_log ADD COLUMN review_notes TEXT AFTER pnl_5d"),
        ('updated_at', "ALTER TABLE trade_log ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at"),
        ('sysver', "ALTER TABLE trade_log ADD COLUMN sysver VARCHAR(16) AFTER remark"),
    ]
    for column, sql in additions:
        if column in columns:
            continue
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            conn.rollback()
    try:
        conn.execute("UPDATE trade_log SET trade_date = buy_date WHERE trade_date IS NULL")
        conn.execute("UPDATE trade_log SET status = CASE WHEN sell_date IS NULL THEN 'open' ELSE 'closed' END WHERE status IS NULL")
        conn.execute("UPDATE trade_log SET remaining_shares = shares WHERE remaining_shares IS NULL AND shares IS NOT NULL AND sell_date IS NULL")
        conn.execute("UPDATE trade_log SET remaining_shares = 0 WHERE remaining_shares IS NULL AND sell_date IS NOT NULL")
        conn.commit()
    except Exception:
        conn.rollback()


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


def _to_float(val):
    """安全转 float"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════
# 东方财富三层行业板块相关函数
# ═══════════════════════════════════════════

def upsert_em_board_l1(rows):
    """
    批量写入东方财富一级行业板块。
    rows: list of (board_code, board_name, board_market, source_index, update_date)
    """
    if not rows:
        return 0
    conn = get_connection()
    conn.executemany("""
        INSERT INTO em_board_l1 (board_code, board_name, board_market, source_index, update_date)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            board_name=VALUES(board_name),
            board_market=VALUES(board_market),
            source_index=VALUES(source_index),
            update_date=VALUES(update_date)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def upsert_em_board_l2(rows):
    """
    批量写入东方财富二级行业板块。
    rows: list of (l1_id, board_code, board_name, board_market, source_index, update_date)
    """
    if not rows:
        return 0
    conn = get_connection()
    conn.executemany("""
        INSERT INTO em_board_l2 (l1_id, board_code, board_name, board_market, source_index, update_date)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            l1_id=VALUES(l1_id),
            board_name=VALUES(board_name),
            board_market=VALUES(board_market),
            source_index=VALUES(source_index),
            update_date=VALUES(update_date)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def upsert_em_board_l3(rows):
    """
    批量写入东方财富三级行业板块。
    rows: list of (l1_id, l2_id, board_code, board_name, board_market, source_index, update_date)
    """
    if not rows:
        return 0
    conn = get_connection()
    conn.executemany("""
        INSERT INTO em_board_l3 (l1_id, l2_id, board_code, board_name, board_market, source_index, update_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            l1_id=VALUES(l1_id),
            l2_id=VALUES(l2_id),
            board_name=VALUES(board_name),
            board_market=VALUES(board_market),
            source_index=VALUES(source_index),
            update_date=VALUES(update_date)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def get_em_board_id_map(level):
    """获取东方财富指定层级板块 code -> id 映射。level: 1/2/3。"""
    table = {1: "em_board_l1", 2: "em_board_l2", 3: "em_board_l3"}.get(int(level))
    if not table:
        raise ValueError("level must be 1, 2, or 3")
    conn = get_connection(readonly=True)
    try:
        rows = conn.execute(f"SELECT board_code, id FROM {table}").fetchall()
        return {row[0]: row[1] for row in rows}
    finally:
        conn.close()


def replace_em_stock_board_l3(rows):
    """
    替换东方财富个股→三级行业绑定。
    rows: list of (code, code_name, raw_code, raw_market, l3_id, labels, update_date)
    """
    conn = get_connection()
    try:
        conn.execute("DELETE FROM em_stock_board_l3")
        if rows:
            conn.executemany("""
                INSERT INTO em_stock_board_l3 (
                    code, code_name, raw_code, raw_market, l3_id, labels, update_date
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    code_name=VALUES(code_name),
                    raw_code=VALUES(raw_code),
                    raw_market=VALUES(raw_market),
                    l3_id=VALUES(l3_id),
                    labels=VALUES(labels),
                    update_date=VALUES(update_date)
            """, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return len(rows)


def upsert_em_board_daily(
    rows,
    data_source="eastmoney_official",
    quality_status="verified",
):
    """
    批量写入东方财富三层行业板块日K。
    rows: list of (board_code, level, date, open, high, low, close,
                   volume, amount, amplitude, pctChg, change_amount, turn)
    """
    if not rows:
        return 0
    values = [tuple(row) + (data_source, quality_status) for row in rows]
    conn = get_connection()
    conn.executemany("""
        INSERT INTO em_board_daily (
            board_code, level, date, open, high, low, close,
            volume, amount, amplitude, pctChg, change_amount, turn,
            data_source, quality_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            level=VALUES(level),
            open=VALUES(open),
            high=VALUES(high),
            low=VALUES(low),
            close=VALUES(close),
            volume=VALUES(volume),
            amount=VALUES(amount),
            amplitude=VALUES(amplitude),
            pctChg=VALUES(pctChg),
            change_amount=VALUES(change_amount),
            turn=VALUES(turn),
            data_source=VALUES(data_source),
            quality_status=VALUES(quality_status)
    """, values)
    conn.commit()
    conn.close()
    return len(rows)


def upsert_em_board_daily_proxy(rows, proxy_method="constituent_average_price"):
    """Write non-official board aggregates to the physically separate proxy table."""
    if not rows:
        return 0
    values = [tuple(row) + (proxy_method,) for row in rows]
    conn = get_connection()
    conn.executemany("""
        INSERT INTO em_board_daily_proxy (
            board_code, level, date, open, high, low, close,
            volume, amount, amplitude, pctChg, change_amount, turn, proxy_method
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            level=VALUES(level), open=VALUES(open), high=VALUES(high),
            low=VALUES(low), close=VALUES(close), volume=VALUES(volume),
            amount=VALUES(amount), amplitude=VALUES(amplitude),
            pctChg=VALUES(pctChg), change_amount=VALUES(change_amount),
            turn=VALUES(turn), proxy_method=VALUES(proxy_method)
    """, values)
    conn.commit()
    conn.close()
    return len(rows)


# ═══════════════════════════════════════════
# 交易记录相关函数
# ═══════════════════════════════════════════

def add_trade(code, name, buy_date, buy_price, mode='A', grade='A', score=0,
              stop_price=None, target_price=None, position=None, remark=None,
              strategy=None, concept_stage=None, concept_name=None, industry=None,
              entry_low=None, entry_high=None, shares=None, amount=None,
              soft_stop=None, target2_price=None, plan_source=None, buy_status=None,
              emotion_phase=None, market_mode=None, confidence_level=None,
              evidence_summary=None, invalidation_condition=None, risk_notes=None,
              expected_horizon=None, sysver=None):
    """新增一条买入记录"""
    init_db()
    if amount is None and shares and buy_price:
        amount = round(float(shares) * float(buy_price), 2)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trade_log (
            code, name, trade_date, action, status, strategy, mode, grade, score,
            concept_stage, concept_name, industry, entry_low, entry_high,
            buy_date, buy_price, shares, remaining_shares, amount, stop_price, soft_stop,
            target_price, target2_price, position, plan_source, buy_status,
            emotion_phase, market_mode, confidence_level, evidence_summary,
            invalidation_condition, risk_notes, expected_horizon, follow_rule, remark, sysver
        ) VALUES (
            %s, %s, %s, 'buy', 'open', %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            NULL, %s, %s
        )
    """, (
        code, name, buy_date, strategy, mode, grade, score,
        concept_stage, concept_name, industry, entry_low, entry_high,
        buy_date, buy_price, shares, shares, amount, stop_price, soft_stop,
        target_price, target2_price, position, plan_source, buy_status,
        emotion_phase, market_mode, confidence_level, evidence_summary,
        invalidation_condition, risk_notes, expected_horizon, remark, sysver,
    ))
    conn.commit()
    trade_id = cur.lastrowid
    cur.close()
    conn.close()
    return trade_id


def calculate_trade_pnl(buy_price, original_shares, remaining_shares, realized_pnl, sell_price):
    """合并部分兑现与最终卖出，返回整笔(pnl_pct, pnl_amount)。"""
    if not buy_price:
        return None, None
    if original_shares:
        remaining = remaining_shares if remaining_shares is not None else original_shares
        final_pnl = (sell_price - buy_price) * remaining if remaining else 0
        pnl_amount = round((realized_pnl or 0) + final_pnl, 2)
        pnl_pct = round(pnl_amount / (buy_price * original_shares) * 100, 2)
        return pnl_pct, pnl_amount
    return round((sell_price - buy_price) / buy_price * 100, 2), None


def close_trade(trade_id, sell_date, sell_price, follow_rule=1, remark=None, sell_reason=None):
    """平仓：填入卖出信息并自动计算盈亏"""
    init_db()
    conn = get_connection()
    cur = conn.execute(
        "SELECT buy_price, shares, COALESCE(remaining_shares, shares), COALESCE(realized_pnl_amount,0) "
        "FROM trade_log WHERE id = %s",
        (trade_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    buy_price, shares, remaining_shares, realized_pnl = row[0], row[1], row[2], row[3] or 0
    pnl_pct, pnl_amount = calculate_trade_pnl(
        buy_price, shares, remaining_shares, realized_pnl, sell_price
    )
    if remark:
        conn.execute("""
            UPDATE trade_log SET trade_date=%s, action='sell', status='closed', sell_date=%s,
                sell_price=%s, sell_reason=%s, pnl_pct=%s, pnl_amount=%s,
                remaining_shares=0, follow_rule=%s,
                remark=CONCAT(IFNULL(remark,''), '; ', %s) WHERE id=%s
        """, (sell_date, sell_date, sell_price, sell_reason, pnl_pct, pnl_amount, follow_rule, remark, trade_id))
    else:
        conn.execute("""
            UPDATE trade_log SET trade_date=%s, action='sell', status='closed', sell_date=%s,
                sell_price=%s, sell_reason=%s, pnl_pct=%s, pnl_amount=%s,
                remaining_shares=0, follow_rule=%s WHERE id=%s
        """, (sell_date, sell_date, sell_price, sell_reason, pnl_pct, pnl_amount, follow_rule, trade_id))
    conn.commit()
    conn.close()
    return True


def get_open_trades():
    """获取所有未平仓记录"""
    init_db()
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM trade_log WHERE status = 'open' OR sell_date IS NULL ORDER BY buy_date DESC, id DESC", conn)
    conn.close()
    return df


def get_trade_history(limit=50):
    """获取最近的交易记录"""
    init_db()
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM trade_log ORDER BY COALESCE(sell_date, buy_date) DESC, id DESC LIMIT ?", conn, params=[limit])
    conn.close()
    return df


def get_trade_stats():
    """统计已平仓交易的胜率和盈亏比"""
    init_db()
    conn = get_connection()
    cur = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
               ROUND(AVG(pnl_pct), 2) as avg_pnl,
               ROUND(AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END), 2) as avg_win,
               ROUND(AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct END), 2) as avg_loss,
               ROUND(SUM(pnl_amount), 2) as total_pnl_amount,
               SUM(CASE WHEN follow_rule = 1 THEN 1 ELSE 0 END) as rule_follow,
               SUM(CASE WHEN follow_rule = 0 THEN 1 ELSE 0 END) as rule_break
        FROM trade_log WHERE status = 'closed' OR sell_date IS NOT NULL
    """)
    row = cur.fetchone()
    conn.close()
    if not row or row[0] == 0:
        return None
    cols = ['total', 'wins', 'losses', 'avg_pnl', 'avg_win', 'avg_loss', 'total_pnl_amount', 'rule_follow', 'rule_break']
    return dict(zip(cols, row))

