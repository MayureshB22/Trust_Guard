"""
Feature 2 Backend — core/rules_engine.py
Dynamic fraud condition checks & threshold configurations.

Implements 7 configurable rules:
  Rule 1: High Amount vs Customer Average (multiplier-based)
  Rule 2: Late Night Transaction (configurable hour window)
  Rule 3: City Mismatch (txn city ≠ customer registered city)
  Rule 4: Rapid-Fire Transactions (N txns within M minutes)
  Rule 5: Absolute High Amount (flat ₹ threshold)
  Rule 7: Channel Anomaly (first-time channel usage for customer)
  Rule 9: Duplicate-Like Transactions (same customer+merchant+amount within 24h)
"""

import sqlite3
import pandas as pd
import json
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fraud_monitoring.db")


# ---------------------------------------------------------------------------
# Default rule configurations
# ---------------------------------------------------------------------------
DEFAULT_RULES = {
    "rule_1_high_amount_vs_avg": {
        "name": "High Amount vs Customer Avg",
        "description": "Flag if transaction amount exceeds X times the customer's average monthly spend",
        "enabled": True,
        "icon": "🔴",
        "params": {
            "multiplier": 5.0,
        },
    },
    "rule_2_late_night": {
        "name": "Late Night Transaction",
        "description": "Flag transactions occurring during unusual late-night hours",
        "enabled": True,
        "icon": "🌙",
        "params": {
            "start_hour": 1,
            "end_hour": 4,
        },
    },
    "rule_3_city_mismatch": {
        "name": "City Mismatch",
        "description": "Flag if transaction city differs from the customer's registered city",
        "enabled": True,
        "icon": "🏙️",
        "params": {},
    },
    "rule_4_rapid_fire": {
        "name": "Rapid-Fire Transactions",
        "description": "Flag if the same customer makes N or more transactions within M minutes",
        "enabled": True,
        "icon": "⚡",
        "params": {
            "max_txns": 3,
            "window_minutes": 10,
        },
    },
    "rule_5_absolute_high_amount": {
        "name": "Absolute High Amount",
        "description": "Flag any transaction above a flat rupee threshold",
        "enabled": True,
        "icon": "💰",
        "params": {
            "threshold": 50000.0,
        },
    },
    "rule_7_channel_anomaly": {
        "name": "Channel Anomaly",
        "description": "Flag if the customer is using a payment channel for the first time",
        "enabled": True,
        "icon": "📡",
        "params": {},
    },
    "rule_9_duplicate_like": {
        "name": "Duplicate-Like Transaction",
        "description": "Flag if the same customer transacts at the same merchant with similar amount (±5%) within 24 hours",
        "enabled": True,
        "icon": "🔁",
        "params": {
            "tolerance_pct": 5.0,
            "window_hours": 24,
        },
    },
}


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Rule config persistence (SQLite)
# ---------------------------------------------------------------------------
def _init_rules_table(conn):
    """Create the rule_configs table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule_configs (
            rule_id   TEXT PRIMARY KEY,
            config    TEXT NOT NULL
        )
    """)
    conn.commit()


def _init_flagged_table(conn):
    """Create the flagged_transactions table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flagged_transactions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id        TEXT NOT NULL,
            customer_id   TEXT NOT NULL,
            rule_id       TEXT NOT NULL,
            rule_name     TEXT NOT NULL,
            reason        TEXT NOT NULL,
            flagged_at    TEXT NOT NULL,
            UNIQUE(txn_id, rule_id)
        )
    """)
    conn.commit()


def load_rule_configs() -> dict:
    """
    Load rule configurations from the database.
    Falls back to DEFAULT_RULES for any missing rules.
    """
    conn = get_connection()
    _init_rules_table(conn)

    cursor = conn.execute("SELECT rule_id, config FROM rule_configs")
    saved = {row[0]: json.loads(row[1]) for row in cursor.fetchall()}
    conn.close()

    # Merge: use saved config if available, else default
    merged = {}
    for rule_id, default_cfg in DEFAULT_RULES.items():
        if rule_id in saved:
            merged[rule_id] = saved[rule_id]
        else:
            merged[rule_id] = default_cfg.copy()

    return merged


def save_rule_configs(configs: dict):
    """Persist rule configurations to the database."""
    conn = get_connection()
    _init_rules_table(conn)

    for rule_id, cfg in configs.items():
        conn.execute(
            "INSERT OR REPLACE INTO rule_configs (rule_id, config) VALUES (?, ?)",
            (rule_id, json.dumps(cfg)),
        )
    conn.commit()
    conn.close()


def save_single_rule_config(rule_id: str, config: dict):
    """Persist a single rule configuration."""
    conn = get_connection()
    _init_rules_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO rule_configs (rule_id, config) VALUES (?, ?)",
        (rule_id, json.dumps(config)),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------
def _load_transactions_with_customers() -> pd.DataFrame:
    """Load transactions joined with customer data."""
    conn = get_connection()
    query = """
        SELECT
            t.txn_id, t.customer_id, t.timestamp, t.amount,
            t.merchant, t.merchant_category, t.city AS txn_city, t.channel,
            c.name AS customer_name, c.city AS customer_city,
            c.avg_monthly_spend
        FROM transactions t
        LEFT JOIN customers c ON t.customer_id = c.customer_id
        ORDER BY t.timestamp
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


# ---------------------------------------------------------------------------
# Individual rule implementations
# ---------------------------------------------------------------------------
def _check_rule_1(row, params) -> tuple[bool, str]:
    """Rule 1: High Amount vs Customer Average."""
    multiplier = params.get("multiplier", 5.0)
    avg_spend = row.get("avg_monthly_spend")
    if avg_spend is None or pd.isna(avg_spend) or avg_spend <= 0:
        return False, ""
    threshold = avg_spend * multiplier
    if row["amount"] > threshold:
        return True, (
            f"Amount ₹{row['amount']:,.2f} exceeds {multiplier}× "
            f"customer avg spend (₹{avg_spend:,.2f} × {multiplier} = ₹{threshold:,.2f})"
        )
    return False, ""


def _check_rule_2(row, params) -> tuple[bool, str]:
    """Rule 2: Late Night Transaction."""
    start_hour = params.get("start_hour", 1)
    end_hour = params.get("end_hour", 4)
    txn_hour = row["timestamp"].hour
    if start_hour <= txn_hour < end_hour:
        return True, (
            f"Transaction at {row['timestamp'].strftime('%I:%M %p')} "
            f"falls in late-night window ({start_hour}:00 – {end_hour}:00)"
        )
    return False, ""


def _check_rule_3(row, params) -> tuple[bool, str]:
    """Rule 3: City Mismatch."""
    customer_city = row.get("customer_city")
    txn_city = row.get("txn_city")
    if customer_city and txn_city and customer_city != txn_city:
        return True, (
            f"Transaction in {txn_city} but customer is registered in {customer_city}"
        )
    return False, ""


def _check_rule_5(row, params) -> tuple[bool, str]:
    """Rule 5: Absolute High Amount."""
    threshold = params.get("threshold", 50000.0)
    if row["amount"] > threshold:
        return True, (
            f"Amount ₹{row['amount']:,.2f} exceeds absolute threshold ₹{threshold:,.2f}"
        )
    return False, ""


def _check_rule_4_batch(df: pd.DataFrame, params) -> dict[str, list[str]]:
    """
    Rule 4: Rapid-Fire Transactions (requires batch analysis).
    Returns {txn_id: reason} for flagged transactions.
    """
    max_txns = params.get("max_txns", 3)
    window_minutes = params.get("window_minutes", 10)
    flagged = {}

    for cust_id, group in df.groupby("customer_id"):
        group = group.sort_values("timestamp")
        timestamps = group["timestamp"].tolist()
        txn_ids = group["txn_id"].tolist()

        for i in range(len(timestamps)):
            window_end = timestamps[i] + timedelta(minutes=window_minutes)
            # Count txns in window starting from this one
            count = 0
            for j in range(i, len(timestamps)):
                if timestamps[j] <= window_end:
                    count += 1
                else:
                    break

            if count >= max_txns:
                # Flag all transactions in this rapid window
                for k in range(i, i + count):
                    if k < len(txn_ids) and txn_ids[k] not in flagged:
                        flagged[txn_ids[k]] = (
                            f"{count} transactions by {cust_id} within "
                            f"{window_minutes} min (threshold: {max_txns})"
                        )

    return flagged


def _check_rule_7_batch(df: pd.DataFrame, params) -> dict[str, str]:
    """
    Rule 7: Channel Anomaly (requires batch analysis).
    Flag if the customer is using a channel for the first time.
    Returns {txn_id: reason} for flagged transactions.
    """
    flagged = {}
    df_sorted = df.sort_values("timestamp")

    # Track which channels each customer has used
    customer_channels: dict[str, set] = {}

    for _, row in df_sorted.iterrows():
        cust_id = row["customer_id"]
        channel = row["channel"]
        txn_id = row["txn_id"]

        if cust_id not in customer_channels:
            customer_channels[cust_id] = set()

        if channel not in customer_channels[cust_id]:
            # First time this customer uses this channel
            if len(customer_channels[cust_id]) > 0:
                # Only flag if they have used other channels before
                # (first transaction ever shouldn't be flagged)
                prev_channels = ", ".join(sorted(customer_channels[cust_id]))
                flagged[txn_id] = (
                    f"First-time use of '{channel}' by {cust_id} "
                    f"(previously used: {prev_channels})"
                )
            customer_channels[cust_id].add(channel)

    return flagged


def _check_rule_9_batch(df: pd.DataFrame, params) -> dict[str, str]:
    """
    Rule 9: Duplicate-Like Transactions (requires batch analysis).
    Same customer + same merchant + similar amount (±tolerance%) within window.
    Returns {txn_id: reason} for flagged transactions.
    """
    tolerance_pct = params.get("tolerance_pct", 5.0) / 100.0
    window_hours = params.get("window_hours", 24)
    flagged = {}

    for (cust_id, merchant), group in df.groupby(["customer_id", "merchant"]):
        if len(group) < 2:
            continue
        group = group.sort_values("timestamp")
        rows = group.to_dict("records")

        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                time_diff = (rows[j]["timestamp"] - rows[i]["timestamp"]).total_seconds() / 3600
                if time_diff > window_hours:
                    break

                amount_i = rows[i]["amount"]
                amount_j = rows[j]["amount"]
                # Check if amounts are within tolerance
                if amount_i > 0 and abs(amount_j - amount_i) / amount_i <= tolerance_pct:
                    reason = (
                        f"Duplicate-like: {cust_id} at {merchant}, "
                        f"₹{amount_i:,.2f} & ₹{amount_j:,.2f} "
                        f"within {time_diff:.1f}h (tolerance: ±{tolerance_pct*100:.0f}%)"
                    )
                    flagged[rows[i]["txn_id"]] = reason
                    flagged[rows[j]["txn_id"]] = reason

    return flagged


# ---------------------------------------------------------------------------
# Main engine: run all enabled rules
# ---------------------------------------------------------------------------
def run_rules_engine(configs: dict | None = None) -> pd.DataFrame:
    """
    Run all enabled rules against the full transaction dataset.
    Returns a DataFrame of flagged transactions with columns:
        txn_id, customer_id, rule_id, rule_name, reason
    """
    if configs is None:
        configs = load_rule_configs()

    df = _load_transactions_with_customers()
    if df.empty:
        return pd.DataFrame(columns=["txn_id", "customer_id", "rule_id", "rule_name", "reason"])

    all_flags: list[dict] = []

    # --- Per-row rules (1, 2, 3, 5) ---
    for _, row in df.iterrows():
        # Rule 1
        cfg = configs.get("rule_1_high_amount_vs_avg", {})
        if cfg.get("enabled", False):
            flagged, reason = _check_rule_1(row, cfg.get("params", {}))
            if flagged:
                all_flags.append({
                    "txn_id": row["txn_id"],
                    "customer_id": row["customer_id"],
                    "rule_id": "rule_1_high_amount_vs_avg",
                    "rule_name": "High Amount vs Customer Avg",
                    "reason": reason,
                })

        # Rule 2
        cfg = configs.get("rule_2_late_night", {})
        if cfg.get("enabled", False):
            flagged, reason = _check_rule_2(row, cfg.get("params", {}))
            if flagged:
                all_flags.append({
                    "txn_id": row["txn_id"],
                    "customer_id": row["customer_id"],
                    "rule_id": "rule_2_late_night",
                    "rule_name": "Late Night Transaction",
                    "reason": reason,
                })

        # Rule 3
        cfg = configs.get("rule_3_city_mismatch", {})
        if cfg.get("enabled", False):
            flagged, reason = _check_rule_3(row, cfg.get("params", {}))
            if flagged:
                all_flags.append({
                    "txn_id": row["txn_id"],
                    "customer_id": row["customer_id"],
                    "rule_id": "rule_3_city_mismatch",
                    "rule_name": "City Mismatch",
                    "reason": reason,
                })

        # Rule 5
        cfg = configs.get("rule_5_absolute_high_amount", {})
        if cfg.get("enabled", False):
            flagged, reason = _check_rule_5(row, cfg.get("params", {}))
            if flagged:
                all_flags.append({
                    "txn_id": row["txn_id"],
                    "customer_id": row["customer_id"],
                    "rule_id": "rule_5_absolute_high_amount",
                    "rule_name": "Absolute High Amount",
                    "reason": reason,
                })

    # --- Batch rules (4, 7, 9) ---
    # Rule 4
    cfg = configs.get("rule_4_rapid_fire", {})
    if cfg.get("enabled", False):
        r4_flags = _check_rule_4_batch(df, cfg.get("params", {}))
        for txn_id, reason in r4_flags.items():
            cust_id = df.loc[df["txn_id"] == txn_id, "customer_id"].iloc[0]
            all_flags.append({
                "txn_id": txn_id,
                "customer_id": cust_id,
                "rule_id": "rule_4_rapid_fire",
                "rule_name": "Rapid-Fire Transactions",
                "reason": reason,
            })

    # Rule 7
    cfg = configs.get("rule_7_channel_anomaly", {})
    if cfg.get("enabled", False):
        r7_flags = _check_rule_7_batch(df, cfg.get("params", {}))
        for txn_id, reason in r7_flags.items():
            cust_id = df.loc[df["txn_id"] == txn_id, "customer_id"].iloc[0]
            all_flags.append({
                "txn_id": txn_id,
                "customer_id": cust_id,
                "rule_id": "rule_7_channel_anomaly",
                "rule_name": "Channel Anomaly",
                "reason": reason,
            })

    # Rule 9
    cfg = configs.get("rule_9_duplicate_like", {})
    if cfg.get("enabled", False):
        r9_flags = _check_rule_9_batch(df, cfg.get("params", {}))
        for txn_id, reason in r9_flags.items():
            cust_id = df.loc[df["txn_id"] == txn_id, "customer_id"].iloc[0]
            all_flags.append({
                "txn_id": txn_id,
                "customer_id": cust_id,
                "rule_id": "rule_9_duplicate_like",
                "rule_name": "Duplicate-Like Transaction",
                "reason": reason,
            })

    flags_df = pd.DataFrame(all_flags)
    return flags_df


def save_flags_to_db(flags_df: pd.DataFrame):
    """
    Persist flagged transactions to the database.
    Clears previous flags and replaces with the new set.
    """
    conn = get_connection()
    _init_flagged_table(conn)

    # Clear old flags
    conn.execute("DELETE FROM flagged_transactions")

    if not flags_df.empty:
        now = datetime.now().isoformat()
        flags_df = flags_df.copy()
        flags_df["flagged_at"] = now
        flags_df[["txn_id", "customer_id", "rule_id", "rule_name", "reason", "flagged_at"]].to_sql(
            "flagged_transactions", conn, if_exists="append", index=False
        )

    conn.commit()
    conn.close()


def get_flagged_transactions() -> pd.DataFrame:
    """Load all flagged transactions from the database."""
    conn = get_connection()
    _init_flagged_table(conn)
    try:
        df = pd.read_sql_query("SELECT * FROM flagged_transactions", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


def get_flags_for_transaction(txn_id: str) -> pd.DataFrame:
    """Get all rule violations for a specific transaction."""
    conn = get_connection()
    _init_flagged_table(conn)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM flagged_transactions WHERE txn_id = ?",
            conn,
            params=(txn_id,),
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


def get_flagged_summary() -> dict:
    """Get summary statistics about flagged transactions."""
    conn = get_connection()
    _init_flagged_table(conn)
    summary = {}
    try:
        # Total unique flagged txns
        row = conn.execute(
            "SELECT COUNT(DISTINCT txn_id) FROM flagged_transactions"
        ).fetchone()
        summary["total_flagged_txns"] = row[0] if row else 0

        # Flags per rule
        cursor = conn.execute(
            "SELECT rule_name, COUNT(*) FROM flagged_transactions GROUP BY rule_name ORDER BY COUNT(*) DESC"
        )
        summary["flags_per_rule"] = {r[0]: r[1] for r in cursor.fetchall()}

        # Total flags (a txn can have multiple flags)
        row = conn.execute("SELECT COUNT(*) FROM flagged_transactions").fetchone()
        summary["total_flags"] = row[0] if row else 0

        # Total transactions
        row = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
        summary["total_txns"] = row[0] if row else 0

    except Exception:
        summary["total_flagged_txns"] = 0
        summary["flags_per_rule"] = {}
        summary["total_flags"] = 0
        summary["total_txns"] = 0

    conn.close()
    return summary
