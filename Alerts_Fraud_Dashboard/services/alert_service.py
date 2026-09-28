"""
FEATURE 4 - Alerts & Fraud Dashboard (service layer)
Owner: Member 4

What this module does
---------------------
1. create_alerts()      turns scored (flagged) transactions into alerts (status = Open)
2. update_status()      moves an alert Open -> Investigating -> Closed (+ outcome) and logs it
3. list_alerts()        returns the alert queue (joined with transaction + score data)
4. dashboard_metrics()  KPIs: flagged count, amount at risk, status counts, false-alarm rate ...
5. top_rules()          most-triggered rules (for the dashboard chart)

Contract with the other features
--------------------------------
- Reads : transactions, customers, risk_scores (F3), rule_results (F2)
- Writes: alerts, alert_events   (ONLY this module writes to these tables)
- Feature 5 must change alert status by calling update_status(), never by SQL.

All functions take an open sqlite3 connection as the first argument.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

# Optional override: put ALERT_MIN_SCORE = 40 in config.py to only alert on Medium+ risk.
try:
    import config  # shared config owned by Member 1

    ALERT_MIN_SCORE = int(getattr(config, "ALERT_MIN_SCORE", 0))
except ImportError:
    ALERT_MIN_SCORE = 0

STATUSES = ["Open", "Investigating", "Closed"]
RESOLUTIONS = ["Confirmed Fraud", "False Alarm"]
LEVELS = ["High", "Medium", "Low"]


class AlertError(Exception):
    """Raised for invalid updates: bad status, missing outcome, unknown alert, stale data."""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------
# Schema (identical to the shared data contract; safe to run repeatedly)
# ----------------------------------------------------------------------------
def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create alerts + alert_events if database.py has not created them yet."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            alert_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id       TEXT NOT NULL UNIQUE REFERENCES transactions(txn_id),
            status       TEXT NOT NULL DEFAULT 'Open'
                         CHECK (status IN ('Open','Investigating','Closed')),
            resolution   TEXT
                         CHECK (resolution IS NULL OR resolution IN ('Confirmed Fraud','False Alarm')),
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            analyst_note TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_events (
            event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id   INTEGER NOT NULL REFERENCES alerts(alert_id),
            old_status TEXT,
            new_status TEXT NOT NULL,
            actor      TEXT NOT NULL,
            at         TEXT NOT NULL
        );
        """
    )
    conn.commit()


# ----------------------------------------------------------------------------
# 1. Alert creation
# ----------------------------------------------------------------------------
def create_alerts(conn: sqlite3.Connection, min_score: int | None = None) -> int:
    """
    Create one 'Open' alert for every scored transaction that has no alert yet.
    Safe to call repeatedly: a transaction can never get two alerts.
    Returns the number of NEW alerts created.
    """
    threshold = ALERT_MIN_SCORE if min_score is None else int(min_score)
    try:
        rows = conn.execute(
            """
            SELECT r.txn_id
            FROM risk_scores r
            LEFT JOIN alerts a ON a.txn_id = r.txn_id
            WHERE a.txn_id IS NULL AND r.score >= ?
            ORDER BY r.score DESC
            """,
            (threshold,),
        ).fetchall()

        now = _now()
        created = 0
        for row in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts (txn_id, status, resolution, created_at, updated_at) "
                "VALUES (?, 'Open', NULL, ?, ?)",
                (row[0], now, now),
            )
            if cur.rowcount == 1:
                conn.execute(
                    "INSERT INTO alert_events (alert_id, old_status, new_status, actor, at) "
                    "VALUES (?, NULL, 'Open', 'system', ?)",
                    (cur.lastrowid, now),
                )
                created += 1
        conn.commit()
        return created
    except sqlite3.Error as exc:
        conn.rollback()
        raise AlertError(f"Could not create alerts: {exc}") from exc


# ----------------------------------------------------------------------------
# 2. Status workflow
# ----------------------------------------------------------------------------
def update_status(
    conn: sqlite3.Connection,
    alert_id: int,
    status: str,
    resolution: str | None = None,
    actor: str = "analyst",
    note: str | None = None,
    expected_status: str | None = None,
) -> bool:
    """
    Change an alert's status.

    Rules
    - status must be Open / Investigating / Closed
    - Closed REQUIRES a resolution ('Confirmed Fraud' or 'False Alarm')
    - any non-Closed status clears the resolution (re-opening an alert)
    - note=None keeps the old note; note="" clears it
    - expected_status (optional): the status the caller last saw. If someone else
      changed the alert meanwhile, we refuse instead of silently overwriting.
    Every change is written to alert_events (audit trail).
    Returns True on success, raises AlertError otherwise.
    """
    try:
        alert_id = int(alert_id)  # DataFrame values are numpy ints, which SQLite cannot match
    except (TypeError, ValueError) as exc:
        raise AlertError(f"Invalid alert id: {alert_id!r}") from exc
    if status not in STATUSES:
        raise AlertError(f"Invalid status '{status}'. Use one of {STATUSES}.")
    if status == "Closed":
        if resolution not in RESOLUTIONS:
            raise AlertError(f"Closing an alert needs an outcome: {RESOLUTIONS}.")
    else:
        resolution = None

    try:
        row = conn.execute("SELECT status FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        if row is None:
            raise AlertError(f"Alert {alert_id} does not exist.")
        old_status = row[0]
        if expected_status is not None and old_status != expected_status:
            raise AlertError(
                f"Alert {alert_id} was changed by someone else (now '{old_status}'). "
                "Refresh and try again."
            )

        now = _now()
        # "AND status = old_status" closes the tiny race between SELECT and UPDATE.
        cur = conn.execute(
            "UPDATE alerts SET status = ?, resolution = ?, updated_at = ?, "
            "analyst_note = COALESCE(?, analyst_note) "
            "WHERE alert_id = ? AND status = ?",
            (status, resolution, now, note, alert_id, old_status),
        )
        if cur.rowcount == 0:
            conn.rollback()
            raise AlertError(f"Alert {alert_id} was changed by someone else. Refresh and try again.")

        conn.execute(
            "INSERT INTO alert_events (alert_id, old_status, new_status, actor, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (alert_id, old_status, status, actor, now),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        conn.rollback()
        raise AlertError(f"Database error while updating alert: {exc}") from exc


# ----------------------------------------------------------------------------
# 3. Reading alerts
# ----------------------------------------------------------------------------
_ALERT_SELECT = """
    SELECT a.alert_id, a.txn_id, a.status, a.resolution,
           r.score, r.level,
           t.customer_id, c.name AS customer_name,
           t.timestamp, t.amount, t.merchant, t.merchant_category, t.city, t.channel,
           r.reasons, a.created_at, a.updated_at, a.analyst_note
    FROM alerts a
    JOIN transactions t ON t.txn_id = a.txn_id
    JOIN risk_scores  r ON r.txn_id = a.txn_id
    LEFT JOIN customers c ON c.customer_id = t.customer_id
"""


def list_alerts(
    conn: sqlite3.Connection,
    status: str | list[str] | None = None,
    level: str | list[str] | None = None,
    customer: str | None = None,
    min_score: int | None = None,
) -> pd.DataFrame:
    """
    Alert queue as a DataFrame, ordered like an analyst work queue:
    Open first, then Investigating, then Closed; highest score, then highest amount.
    """
    where, params = [], []
    if status:
        vals = [status] if isinstance(status, str) else list(status)
        where.append(f"a.status IN ({','.join('?' * len(vals))})")
        params += vals
    if level:
        vals = [level] if isinstance(level, str) else list(level)
        where.append(f"r.level COLLATE NOCASE IN ({','.join('?' * len(vals))})")
        params += vals
    if customer and customer.strip():
        like = f"%{customer.strip()}%"
        where.append("(t.customer_id LIKE ? OR c.name LIKE ?)")
        params += [like, like]
    if min_score is not None:
        where.append("r.score >= ?")
        params.append(int(min_score))

    sql = _ALERT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += (
        " ORDER BY CASE a.status WHEN 'Open' THEN 0 WHEN 'Investigating' THEN 1 ELSE 2 END,"
        " r.score DESC, t.amount DESC"
    )
    return pd.read_sql_query(sql, conn, params=params)


def get_alert(conn: sqlite3.Connection, alert_id: int) -> dict | None:
    """One alert as a dict (same columns as list_alerts) or None if not found."""
    df = pd.read_sql_query(_ALERT_SELECT + " WHERE a.alert_id = ?", conn, params=[int(alert_id)])
    if df.empty:
        return None
    return {k: (None if pd.isna(v) else v) for k, v in df.iloc[0].to_dict().items()}


def get_rule_hits(conn: sqlite3.Connection, txn_id: str) -> pd.DataFrame:
    """Which rules fired for this transaction (rule, points, detail) - from Feature 2."""
    return pd.read_sql_query(
        "SELECT rule_name, points, detail FROM rule_results WHERE txn_id = ? ORDER BY points DESC",
        conn,
        params=[txn_id],
    )


def get_alert_events(conn: sqlite3.Connection, alert_id: int) -> pd.DataFrame:
    """Audit trail of status changes for one alert."""
    return pd.read_sql_query(
        "SELECT at, old_status, new_status, actor FROM alert_events WHERE alert_id = ? ORDER BY event_id",
        conn,
        params=[int(alert_id)],
    )


# ----------------------------------------------------------------------------
# 4. Dashboard metrics
# ----------------------------------------------------------------------------
def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> float:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def dashboard_metrics(conn: sqlite3.Connection) -> dict:
    """All KPI numbers for the dashboard in one dict (zeros if there is no data)."""
    total_txn = int(_scalar(conn, "SELECT COUNT(*) FROM transactions"))
    flagged = int(_scalar(conn, "SELECT COUNT(*) FROM alerts"))

    by_status = {s: int(_scalar(conn, "SELECT COUNT(*) FROM alerts WHERE status = ?", (s,))) for s in STATUSES}

    amount_at_risk = float(
        _scalar(
            conn,
            "SELECT SUM(t.amount) FROM alerts a JOIN transactions t ON t.txn_id = a.txn_id "
            "WHERE a.status != 'Closed'",
        )
    )
    total_flagged_amount = float(
        _scalar(conn, "SELECT SUM(t.amount) FROM alerts a JOIN transactions t ON t.txn_id = a.txn_id")
    )
    confirmed = int(_scalar(conn, "SELECT COUNT(*) FROM alerts WHERE resolution = 'Confirmed Fraud'"))
    false_alarms = int(_scalar(conn, "SELECT COUNT(*) FROM alerts WHERE resolution = 'False Alarm'"))
    confirmed_amount = float(
        _scalar(
            conn,
            "SELECT SUM(t.amount) FROM alerts a JOIN transactions t ON t.txn_id = a.txn_id "
            "WHERE a.resolution = 'Confirmed Fraud'",
        )
    )
    high_risk = int(
        _scalar(
            conn,
            "SELECT COUNT(*) FROM alerts a JOIN risk_scores r ON r.txn_id = a.txn_id "
            "WHERE r.level = 'High' COLLATE NOCASE",
        )
    )
    resolved = confirmed + false_alarms

    return {
        "total_transactions": total_txn,
        "flagged_count": flagged,
        "flag_rate_pct": round(100 * flagged / total_txn, 1) if total_txn else 0.0,
        "open_count": by_status["Open"],
        "investigating_count": by_status["Investigating"],
        "closed_count": by_status["Closed"],
        "amount_at_risk": amount_at_risk,  # Open + Investigating alerts
        "total_flagged_amount": total_flagged_amount,
        "high_risk_count": high_risk,
        "confirmed_fraud_count": confirmed,
        "confirmed_fraud_amount": confirmed_amount,
        "false_alarm_count": false_alarms,
        # None until at least one alert has an outcome
        "false_alarm_rate_pct": round(100 * false_alarms / resolved, 1) if resolved else None,
    }


def top_rules(conn: sqlite3.Connection, limit: int = 10) -> pd.DataFrame:
    """Most-triggered rules among alerted transactions (rule_name, times_triggered, total_points)."""
    return pd.read_sql_query(
        """
        SELECT rr.rule_name, COUNT(*) AS times_triggered, SUM(rr.points) AS total_points
        FROM rule_results rr
        JOIN alerts a ON a.txn_id = rr.txn_id
        GROUP BY rr.rule_name
        ORDER BY times_triggered DESC, total_points DESC
        LIMIT ?
        """,
        conn,
        params=[int(limit)],
    )


def alerts_by_day(conn: sqlite3.Connection) -> pd.DataFrame:
    """Number of alerts per transaction date (day, alerts)."""
    return pd.read_sql_query(
        """
        SELECT substr(t.timestamp, 1, 10) AS day, COUNT(*) AS alerts
        FROM alerts a JOIN transactions t ON t.txn_id = a.txn_id
        GROUP BY day ORDER BY day
        """,
        conn,
    )
