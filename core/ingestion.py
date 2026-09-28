"""
Feature 1 Backend — core/ingestion.py
CSV ingestion, validation, and table-level query helpers for
the Transaction Fraud Monitoring system.
"""

import sqlite3
import pandas as pd
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fraud_monitoring.db")

# ---------------------------------------------------------------------------
# Expected schemas (column name -> python dtype string for validation)
# ---------------------------------------------------------------------------
CUSTOMERS_SCHEMA = {
    "customer_id": "object",
    "name": "object",
    "city": "object",
    "avg_monthly_spend": "float64",
}

TRANSACTIONS_SCHEMA = {
    "txn_id": "object",
    "customer_id": "object",
    "timestamp": "object",
    "amount": "float64",
    "merchant": "object",
    "merchant_category": "object",
    "city": "object",
    "channel": "object",
}


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def validate_csv(df: pd.DataFrame, expected_cols: dict) -> list[str]:
    """
    Validate that *df* contains the expected columns.
    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    missing = set(expected_cols.keys()) - set(df.columns)
    if missing:
        errors.append(f"Missing columns: {', '.join(sorted(missing))}")
    return errors


def validate_customers_df(df: pd.DataFrame) -> list[str]:
    """Validate a customers DataFrame."""
    errors = validate_csv(df, CUSTOMERS_SCHEMA)
    if not errors:
        if df["customer_id"].duplicated().any():
            dup_count = df["customer_id"].duplicated().sum()
            errors.append(f"{dup_count} duplicate customer_id(s) found")
        if df["avg_monthly_spend"].isnull().any():
            errors.append("avg_monthly_spend contains null values")
        if (df["avg_monthly_spend"] < 0).any():
            errors.append("avg_monthly_spend contains negative values")
    return errors


def validate_transactions_df(df: pd.DataFrame) -> list[str]:
    """Validate a transactions DataFrame."""
    errors = validate_csv(df, TRANSACTIONS_SCHEMA)
    if not errors:
        if df["txn_id"].duplicated().any():
            dup_count = df["txn_id"].duplicated().sum()
            errors.append(f"{dup_count} duplicate txn_id(s) found")
        if df["amount"].isnull().any():
            errors.append("amount contains null values")
        if (df["amount"] < 0).any():
            errors.append("amount contains negative values")
        # Validate channel values
        valid_channels = {"UPI", "card", "netbanking"}
        unique_channels = set(df["channel"].dropna().unique())
        invalid = unique_channels - valid_channels
        if invalid:
            errors.append(f"Invalid channel values: {', '.join(sorted(invalid))}")
    return errors


# ---------------------------------------------------------------------------
# Ingestion (upload → SQLite)
# ---------------------------------------------------------------------------
def ingest_customers_csv(uploaded_file) -> tuple[int, list[str]]:
    """
    Read an uploaded customers CSV file, validate, and store in SQLite.
    Returns (rows_inserted, list_of_errors).
    """
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    errors = validate_customers_df(df)
    if errors:
        return 0, errors

    conn = get_connection()
    df.to_sql("customers", conn, if_exists="replace", index=False)
    conn.close()
    return len(df), []


def ingest_transactions_csv(uploaded_file) -> tuple[int, list[str]]:
    """
    Read an uploaded transactions CSV file, validate, and store in SQLite.
    Returns (rows_inserted, list_of_errors).
    """
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as e:
        return 0, [f"Failed to read CSV: {e}"]

    errors = validate_transactions_df(df)
    if errors:
        return 0, errors

    conn = get_connection()
    df.to_sql("transactions", conn, if_exists="replace", index=False)
    conn.close()
    return len(df), []


# ---------------------------------------------------------------------------
# Query / filter helpers
# ---------------------------------------------------------------------------
def fetch_transactions(
    customer_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    channel: str | None = None,
    merchant_category: str | None = None,
    city: str | None = None,
    search_query: str | None = None,
) -> pd.DataFrame:
    """
    Fetch transactions from SQLite with optional filters.
    Returns a pandas DataFrame.
    """
    conn = get_connection()
    query = "SELECT * FROM transactions WHERE 1=1"
    params: list = []

    if customer_id:
        query += " AND customer_id = ?"
        params.append(customer_id)
    if date_from:
        query += " AND timestamp >= ?"
        params.append(date_from)
    if date_to:
        query += " AND timestamp <= ?"
        params.append(date_to + " 23:59:59")
    if amount_min is not None:
        query += " AND amount >= ?"
        params.append(amount_min)
    if amount_max is not None:
        query += " AND amount <= ?"
        params.append(amount_max)
    if channel:
        query += " AND channel = ?"
        params.append(channel)
    if merchant_category:
        query += " AND merchant_category = ?"
        params.append(merchant_category)
    if city:
        query += " AND city = ?"
        params.append(city)
    if search_query:
        query += " AND (txn_id LIKE ? OR merchant LIKE ? OR customer_id LIKE ?)"
        like_pattern = f"%{search_query}%"
        params.extend([like_pattern, like_pattern, like_pattern])

    query += " ORDER BY timestamp DESC"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def fetch_customers(
    customer_id: str | None = None,
    city: str | None = None,
    search_query: str | None = None,
) -> pd.DataFrame:
    """Fetch customers with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM customers WHERE 1=1"
    params: list = []

    if customer_id:
        query += " AND customer_id = ?"
        params.append(customer_id)
    if city:
        query += " AND city = ?"
        params.append(city)
    if search_query:
        query += " AND (customer_id LIKE ? OR name LIKE ?)"
        like_pattern = f"%{search_query}%"
        params.extend([like_pattern, like_pattern])

    query += " ORDER BY customer_id"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_dataset_stats() -> dict:
    """
    Return summary statistics about the currently loaded datasets.
    """
    conn = get_connection()
    stats: dict = {}
    try:
        # Transactions stats
        txn_df = pd.read_sql_query("SELECT * FROM transactions", conn)
        stats["txn_count"] = len(txn_df)
        stats["txn_total_amount"] = txn_df["amount"].sum()
        stats["txn_avg_amount"] = txn_df["amount"].mean()
        stats["txn_max_amount"] = txn_df["amount"].max()
        stats["txn_min_amount"] = txn_df["amount"].min()
        stats["txn_unique_customers"] = txn_df["customer_id"].nunique()
        stats["txn_unique_merchants"] = txn_df["merchant"].nunique()
        stats["txn_channels"] = sorted(txn_df["channel"].unique().tolist())
        stats["txn_categories"] = sorted(txn_df["merchant_category"].unique().tolist())
        stats["txn_cities"] = sorted(txn_df["city"].unique().tolist())
        stats["txn_date_range"] = (txn_df["timestamp"].min(), txn_df["timestamp"].max())
    except Exception:
        stats["txn_count"] = 0

    try:
        # Customers stats
        cust_df = pd.read_sql_query("SELECT * FROM customers", conn)
        stats["cust_count"] = len(cust_df)
        stats["cust_cities"] = sorted(cust_df["city"].unique().tolist())
        stats["cust_avg_spend_mean"] = cust_df["avg_monthly_spend"].mean()
        stats["cust_avg_spend_max"] = cust_df["avg_monthly_spend"].max()
        stats["cust_avg_spend_min"] = cust_df["avg_monthly_spend"].min()
    except Exception:
        stats["cust_count"] = 0

    conn.close()
    return stats


def get_unique_values(table: str, column: str) -> list:
    """Return sorted unique values for a given column in a table."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}", conn
        )
        return df[column].tolist()
    except Exception:
        return []
    finally:
        conn.close()


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists
