"""
Database initializer: Ingests data/customers.csv and data/transactions.csv
into a local SQLite database (fraud_monitoring.db).
"""

import sqlite3
import pandas as pd
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "fraud_monitoring.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def get_connection():
    """Return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_customers_table(conn):
    """Create the customers table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id   TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            city          TEXT NOT NULL,
            avg_monthly_spend REAL NOT NULL
        )
    """)
    conn.commit()


def init_transactions_table(conn):
    """Create the transactions table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id            TEXT PRIMARY KEY,
            customer_id       TEXT NOT NULL,
            timestamp         TEXT NOT NULL,
            amount            REAL NOT NULL,
            merchant          TEXT NOT NULL,
            merchant_category TEXT NOT NULL,
            city              TEXT NOT NULL,
            channel           TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
    """)
    conn.commit()


def load_csv_to_table(conn, csv_path: str, table_name: str):
    """
    Read a CSV file and insert its rows into the given table.
    Existing rows with the same primary key are replaced (upsert).
    Returns the number of rows loaded.
    """
    df = pd.read_csv(csv_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    return len(df)


def seed_from_data_dir():
    """
    Seed the database from the CSV files in the data/ directory.
    Called once at startup to ensure default data is available.
    """
    conn = get_connection()
    init_customers_table(conn)
    init_transactions_table(conn)

    customers_csv = os.path.join(DATA_DIR, "customers.csv")
    transactions_csv = os.path.join(DATA_DIR, "transactions.csv")

    rows_c, rows_t = 0, 0
    if os.path.exists(customers_csv):
        rows_c = load_csv_to_table(conn, customers_csv, "customers")
    if os.path.exists(transactions_csv):
        rows_t = load_csv_to_table(conn, transactions_csv, "transactions")

    conn.close()
    return rows_c, rows_t


if __name__ == "__main__":
    rc, rt = seed_from_data_dir()
    print(f"✅ Database initialized at {DB_PATH}")
    print(f"   Customers loaded : {rc}")
    print(f"   Transactions loaded: {rt}")
