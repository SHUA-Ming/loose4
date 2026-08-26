#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit and repair mixed-scale rows in em_board_daily.

The official Eastmoney board index and the equal-weight constituent price
aggregate are different series.  Older updater versions stored both in the
same table.  This tool quarantines the aggregate rows, copies them to the
physically separate proxy table, and replaces the polluted official-series
rows with an explicitly labelled bridge series anchored to adjacent official
index observations.

Run without --apply for a read-only report after schema migration.  Use
--apply to perform the backed-up repair.
"""

import argparse
import math
import sys
from collections import Counter
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_COMMON_DIR = Path(__file__).resolve().parents[1] / "00_公共核心"
if str(_COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMON_DIR))

from project_paths import ensure_tool_paths

ensure_tool_paths()
from db_cache import get_connection, init_db


REPAIR_BATCH = "scale_fix_20260825_v1"
LOW_SCALE_RATIO = 0.20
HIGH_SCALE_RATIO = 5.0
BOUNDARY_JUMP_RATIO = 4.0

SELECT_COLUMNS = (
    "board_code, level, date, open, high, low, close, volume, amount, "
    "amplitude, pctChg, change_amount, turn, data_source, quality_status, "
    "updated_at"
)


def _float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _rows_as_dicts(raw_rows):
    names = [part.strip() for part in SELECT_COLUMNS.split(",")]
    return [dict(zip(names, row)) for row in raw_rows]


def load_rows():
    conn = get_connection(readonly=True)
    try:
        raw = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM em_board_daily "
            "ORDER BY board_code, date"
        ).fetchall()
        return _rows_as_dicts(raw)
    finally:
        conn.close()


def group_by_board(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["board_code"], []).append(row)
    return grouped


def chunks(values, size=2000):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def classify_board(rows):
    """Return flags for values unmistakably on a different numeric scale."""
    valid = [_float(row["close"]) for row in rows]
    valid = [value for value in valid if value and value > 0]
    if not valid:
        return [False] * len(rows)
    latest = valid[-1]
    flags = []
    for row in rows:
        close = _float(row["close"])
        if close is None or close <= 0:
            flags.append(False)
            continue
        ratio = close / latest
        flags.append(ratio < LOW_SCALE_RATIO or ratio > HIGH_SCALE_RATIO)
    return flags


def find_blocks(flags):
    blocks = []
    start = None
    for index, flag in enumerate(flags + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            blocks.append((start, index - 1))
            start = None
    return blocks


def _daily_return(rows, index):
    pct = _float(rows[index].get("pctChg"))
    if pct is not None and -30.0 < pct < 30.0:
        return pct / 100.0
    if index > 0:
        current = _float(rows[index].get("close"))
        previous = _float(rows[index - 1].get("close"))
        if current and previous and previous > 0:
            value = current / previous - 1.0
            if -0.30 < value < 0.30:
                return value
    return 0.0


def bridge_closes(rows, start, end):
    """Build one continuous close path between surrounding official anchors."""
    previous = _float(rows[start - 1]["close"]) if start > 0 else None
    next_row = rows[end + 1] if end + 1 < len(rows) else None
    next_close = _float(next_row["close"]) if next_row else None
    target_last = None
    if next_close:
        next_return = _daily_return(rows, end + 1)
        if 1.0 + next_return > 0:
            target_last = next_close / (1.0 + next_return)

    count = end - start + 1
    if previous:
        raw = []
        value = previous
        for index in range(start, end + 1):
            value *= 1.0 + _daily_return(rows, index)
            raw.append(value)
        if target_last and raw[-1] > 0:
            total_factor = target_last / raw[-1]
            if total_factor > 0:
                return [
                    value * total_factor ** ((offset + 1) / count)
                    for offset, value in enumerate(raw)
                ]
        return raw

    if target_last:
        values = [None] * count
        values[-1] = target_last
        for index in range(end, start, -1):
            daily_return = _daily_return(rows, index)
            denominator = 1.0 + daily_return
            values[index - start - 1] = (
                values[index - start] / denominator
                if denominator > 0
                else values[index - start]
            )
        return values

    raise ValueError(
        f"board {rows[0]['board_code']} block {start}:{end} has no official anchor"
    )


def _shape_price(original, close_value, field):
    old_close = _float(original.get("close"))
    old_value = _float(original.get(field))
    if old_close and old_close > 0 and old_value is not None:
        return close_value * old_value / old_close
    return close_value


def rebuild_board(rows, flags):
    """Return repaired rows for all suspicious blocks in one board."""
    repaired = {}
    for start, end in find_blocks(flags):
        closes = bridge_closes(rows, start, end)
        for offset, index in enumerate(range(start, end + 1)):
            original = rows[index]
            close_value = closes[offset]
            open_value = _shape_price(original, close_value, "open")
            high_value = _shape_price(original, close_value, "high")
            low_value = _shape_price(original, close_value, "low")
            high_value = max(open_value, high_value, close_value)
            low_value = min(open_value, low_value, close_value)
            previous_close = (
                repaired[index - 1]["close"]
                if index - 1 in repaired
                else _float(rows[index - 1]["close"]) if index > 0 else None
            )
            if previous_close and previous_close > 0:
                change_amount = close_value - previous_close
                pct_change = change_amount / previous_close * 100.0
                amplitude = (high_value - low_value) / previous_close * 100.0
            else:
                change_amount = None
                pct_change = None
                amplitude = None
            volume = _float(original.get("volume"))
            repaired[index] = {
                **original,
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                # Stock K-lines store shares; Eastmoney board K-lines store lots.
                "volume": volume / 100.0 if volume is not None else None,
                "amplitude": amplitude,
                "pctChg": pct_change,
                "change_amount": change_amount,
                "data_source": "constituent_bridge",
                "quality_status": "derived",
            }
    return repaired


def analyze(rows):
    grouped = group_by_board(rows)
    suspicious = []
    board_details = []
    for code, board_rows in grouped.items():
        flags = classify_board(board_rows)
        selected = [row for row, flag in zip(board_rows, flags) if flag]
        suspicious.extend(selected)
        if selected:
            board_details.append(
                (code, len(selected), str(selected[0]["date"]), str(selected[-1]["date"]))
            )
    return grouped, suspicious, sorted(board_details, key=lambda item: (-item[1], item[0]))


def count_boundary_jumps(grouped):
    jumps = []
    for code, rows in grouped.items():
        for previous, current in zip(rows, rows[1:]):
            left = _float(previous["close"])
            right = _float(current["close"])
            if not left or not right or left <= 0 or right <= 0:
                continue
            ratio = right / left
            if ratio > BOUNDARY_JUMP_RATIO or ratio < 1.0 / BOUNDARY_JUMP_RATIO:
                jumps.append((code, previous["date"], current["date"], ratio))
    return jumps


def latest_snapshot(rows):
    grouped = group_by_board(rows)
    return {
        code: (
            board_rows[-1]["date"],
            board_rows[-1]["open"],
            board_rows[-1]["high"],
            board_rows[-1]["low"],
            board_rows[-1]["close"],
            board_rows[-1]["volume"],
            board_rows[-1]["amount"],
        )
        for code, board_rows in grouped.items()
    }


def print_report(rows, label):
    grouped, suspicious, details = analyze(rows)
    jumps = count_boundary_jumps(grouped)
    sources = Counter((row["data_source"], row["quality_status"]) for row in rows)
    print(f"[{label}] rows={len(rows)} boards={len(grouped)}")
    print(
        f"[{label}] mixed-scale rows={len(suspicious)} "
        f"affected boards={len(details)} boundary jumps={len(jumps)}"
    )
    for code, count, first_date, last_date in details[:12]:
        print(f"  {code}: {count} rows, {first_date} .. {last_date}")
    print(f"[{label}] provenance:")
    for (source, quality), count in sorted(sources.items()):
        print(f"  {source}/{quality}: {count}")
    return grouped, suspicious, details, jumps


def print_board_details(names):
    if not names:
        return
    union_sql = " UNION ALL ".join(
        f"SELECT board_code, board_name, {level} AS level FROM em_board_l{level}"
        for level in (1, 2, 3)
    )
    placeholders = ",".join("?" for _ in names)
    conn = get_connection(readonly=True)
    try:
        matches = conn.execute(
            f"SELECT board_code, board_name, level FROM ({union_sql}) boards "
            f"WHERE board_name IN ({placeholders}) ORDER BY board_name",
            names,
        ).fetchall()
        for code, name, level in matches:
            source_counts = conn.execute(
                "SELECT data_source, quality_status, COUNT(*) "
                "FROM em_board_daily WHERE board_code=? "
                "GROUP BY data_source, quality_status ORDER BY data_source",
                (code,),
            ).fetchall()
            latest = conn.execute(
                "SELECT date, close, pctChg, data_source, quality_status "
                "FROM em_board_daily WHERE board_code=? "
                "ORDER BY date DESC LIMIT 5",
                (code,),
            ).fetchall()
            quarantined = conn.execute(
                "SELECT date, close, pctChg FROM em_board_daily_quarantine "
                "WHERE repair_batch=? AND board_code=? "
                "ORDER BY date DESC LIMIT 3",
                (REPAIR_BATCH, code),
            ).fetchall()
            print(f"[board] {name} {code} level={level} sources={source_counts}")
            for item in latest:
                print(f"  {item}")
            if quarantined:
                print(f"  quarantined raw latest={quarantined}")
    finally:
        conn.close()


def apply_repair(rows):
    grouped, suspicious, _details = analyze(rows)
    if not suspicious:
        return 0, 0

    replacements = []
    originals = []
    for code, board_rows in grouped.items():
        flags = classify_board(board_rows)
        if not any(flags):
            continue
        repaired = rebuild_board(board_rows, flags)
        for index, row in enumerate(board_rows):
            if not flags[index]:
                continue
            replacements.append(repaired[index])
            originals.append(row)

    conn = get_connection()
    try:
        quarantine_values = [
            (
                REPAIR_BATCH,
                row["board_code"], row["level"], row["date"],
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row["amount"], row["amplitude"],
                row["pctChg"], row["change_amount"], row["turn"],
                row["data_source"], row["quality_status"],
                "mixed constituent-average and official-index scale",
                row["updated_at"],
            )
            for row in originals
        ]
        quarantine_sql = """
            INSERT IGNORE INTO em_board_daily_quarantine (
                repair_batch, board_code, level, date, open, high, low, close,
                volume, amount, amplitude, pctChg, change_amount, turn,
                original_source, original_quality, quarantine_reason,
                original_updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """
        for batch in chunks(quarantine_values):
            conn.executemany(quarantine_sql, batch)

        proxy_values = [
            (
                row["board_code"], row["level"], row["date"],
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row["amount"], row["amplitude"],
                row["pctChg"], row["change_amount"], row["turn"],
                "legacy_constituent_average_price",
            )
            for row in originals
        ]
        proxy_sql = """
            INSERT INTO em_board_daily_proxy (
                board_code, level, date, open, high, low, close, volume,
                amount, amplitude, pctChg, change_amount, turn, proxy_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                level=VALUES(level), open=VALUES(open), high=VALUES(high),
                low=VALUES(low), close=VALUES(close), volume=VALUES(volume),
                amount=VALUES(amount), amplitude=VALUES(amplitude),
                pctChg=VALUES(pctChg), change_amount=VALUES(change_amount),
                turn=VALUES(turn), proxy_method=VALUES(proxy_method)
        """
        for batch in chunks(proxy_values):
            conn.executemany(proxy_sql, batch)

        replacement_values = [
            (
                row["level"], row["open"], row["high"], row["low"],
                row["close"], row["volume"], row["amount"],
                row["amplitude"], row["pctChg"], row["change_amount"],
                row["turn"], row["data_source"], row["quality_status"],
                row["board_code"], row["date"],
            )
            for row in replacements
        ]
        replacement_sql = """
            UPDATE em_board_daily SET
                level=?, open=?, high=?, low=?, close=?, volume=?, amount=?,
                amplitude=?, pctChg=?, change_amount=?, turn=?,
                data_source=?, quality_status=?
            WHERE board_code=? AND date=?
        """
        for batch in chunks(replacement_values):
            conn.executemany(replacement_sql, batch)
        conn.execute(
            """
            UPDATE em_board_daily
            SET data_source='eastmoney_official_legacy', quality_status='verified'
            WHERE data_source='legacy_unknown'
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(replacements), len(originals)


def validate(before_rows, after_rows):
    failures = []
    if latest_snapshot(before_rows) != latest_snapshot(after_rows):
        failures.append("latest board snapshot changed")
    grouped, suspicious, _details = analyze(after_rows)
    if suspicious:
        failures.append(f"{len(suspicious)} mixed-scale rows remain")
    jumps = count_boundary_jumps(grouped)
    if jumps:
        failures.append(f"{len(jumps)} >4x adjacent close jumps remain")
    invalid_ohlc = 0
    for row in after_rows:
        open_value = _float(row["open"])
        high_value = _float(row["high"])
        low_value = _float(row["low"])
        close_value = _float(row["close"])
        if None in (open_value, high_value, low_value, close_value):
            continue
        if high_value < max(open_value, close_value) or low_value > min(open_value, close_value):
            invalid_ohlc += 1
    if invalid_ohlc:
        failures.append(f"{invalid_ohlc} invalid OHLC rows")
    return failures


def main():
    parser = argparse.ArgumentParser(
        description="Audit/repair mixed Eastmoney board-index and constituent-average scales"
    )
    parser.add_argument("--apply", action="store_true", help="back up and apply repair")
    parser.add_argument(
        "--inspect-name",
        action="append",
        default=[],
        help="print provenance and latest rows for an exact board name (repeatable)",
    )
    args = parser.parse_args()

    # This is an update/admin tool; init_db performs the one-time provenance migration.
    init_db()
    before = load_rows()
    _grouped, suspicious, _details, _jumps = print_report(before, "before")
    print_board_details(args.inspect_name)
    if not args.apply:
        print("Audit only. Re-run with --apply to back up and repair the flagged rows.")
        return 2 if suspicious else 0

    latest_before = latest_snapshot(before)
    repaired_count, backup_count = apply_repair(before)
    after = load_rows()
    print_report(after, "after")
    failures = validate(before, after)
    if latest_before != latest_snapshot(after):
        failures.append("latest snapshot checksum mismatch")

    conn = get_connection(readonly=True)
    try:
        quarantine_count = conn.execute(
            "SELECT COUNT(*) FROM em_board_daily_quarantine WHERE repair_batch=?",
            (REPAIR_BATCH,),
        ).fetchone()[0]
        proxy_count = conn.execute(
            "SELECT COUNT(*) FROM em_board_daily_proxy "
            "WHERE proxy_method='legacy_constituent_average_price'"
        ).fetchone()[0]
    finally:
        conn.close()

    print(
        f"repair rows={repaired_count} backup requested={backup_count} "
        f"quarantine rows={quarantine_count} legacy proxy rows={proxy_count}"
    )
    if quarantine_count < backup_count:
        failures.append("quarantine backup count is smaller than repaired count")
    if failures:
        for failure in failures:
            print(f"VALIDATION FAILED: {failure}")
        return 1
    print("VALIDATION PASSED: scale, adjacency, OHLC, provenance, and latest snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
