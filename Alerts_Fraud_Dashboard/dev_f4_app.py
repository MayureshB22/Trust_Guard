"""
Standalone runner for Feature 4 - lets you work on the dashboard/alerts BEFORE
Features 1-3 are finished. Uses its own throw-away database (data/f4_dev.db)
filled with hand-made transactions, rule hits and risk scores.

    streamlit run dev_f4_app.py

To reset the sample data, delete data/f4_dev.db and refresh the page.
Do NOT submit this file as part of the real app; app.py (Member 1) is the real entry point.
"""
import sqlite3
from pathlib import Path

import streamlit as st

from services import alert_service
from ui import alerts

DB_PATH = Path(__file__).parent / "data" / "f4_dev.db"

DDL = """
CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, name TEXT, home_city TEXT,
    avg_monthly_spend REAL, avg_txn_amount REAL, phone_masked TEXT);
CREATE TABLE IF NOT EXISTS transactions (txn_id TEXT PRIMARY KEY, customer_id TEXT, timestamp TEXT,
    amount REAL, merchant TEXT, merchant_category TEXT, city TEXT, channel TEXT);
CREATE TABLE IF NOT EXISTS rule_results (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT,
    rule_name TEXT, points INTEGER, detail TEXT, UNIQUE(txn_id, rule_name));
CREATE TABLE IF NOT EXISTS risk_scores (txn_id TEXT PRIMARY KEY, score INTEGER, level TEXT,
    reasons TEXT, computed_at TEXT);
"""

CUSTOMERS = [
    ("C001", "Priya Sharma", "Pune", 30000, 2000, "XXXXXX1111"),
    ("C002", "Rahul Verma", "Pune", 25000, 1500, "XXXXXX2222"),
    ("C003", "Anita Desai", "Pune", 40000, 3000, "XXXXXX3333"),
    ("C004", "Vikram Singh", "Mumbai", 20000, 1500, "XXXXXX4444"),
    ("C005", "Sneha Rao", "Bengaluru", 35000, 2500, "XXXXXX5555"),
]

# txn_id, customer, timestamp, amount, merchant, category, city, channel
TRANSACTIONS = [
    ("T001", "C001", "2026-03-12 02:14:00", 60000, "Jewel Palace", "Jewellery", "Dubai", "UPI"),
    ("T002", "C002", "2026-03-13 01:30:00", 3000, "Beach Shack", "Food", "Goa", "card"),
    ("T003", "C003", "2026-03-14 11:00:00", 48000, "Croma", "Electronics", "Pune", "card"),
    ("T004", "C004", "2026-03-15 14:01:00", 1500, "QuickMart", "Grocery", "Mumbai", "UPI"),
    ("T005", "C004", "2026-03-15 14:03:00", 1800, "QuickMart", "Grocery", "Mumbai", "UPI"),
    ("T006", "C004", "2026-03-15 14:06:00", 9000, "GadgetHub", "Electronics", "Mumbai", "UPI"),
    ("T007", "C005", "2026-03-16 03:45:00", 25000, "NetTransfer", "Transfer", "Delhi", "netbanking"),
    ("T008", "C001", "2026-03-10 10:00:00", 1850, "Big Bazaar", "Grocery", "Pune", "UPI"),   # normal
    ("T009", "C002", "2026-03-11 19:05:00", 1200, "Zomato", "Food", "Pune", "card"),         # normal
    ("T010", "C003", "2026-03-12 09:30:00", 2500, "Metro Cafe", "Food", "Pune", "UPI"),      # normal
]

# txn_id, rule_name, points, detail
RULE_RESULTS = [
    ("T001", "high_amount", 35, "Amount 60,000 is 30.0x the customer's average of 2,000"),
    ("T001", "odd_hour", 20, "Transaction at 02:14 (window 01:00-04:00)"),
    ("T001", "new_city", 25, "Dubai is not the home city (Pune) and not seen before"),
    ("T002", "odd_hour", 20, "Transaction at 01:30 (window 01:00-04:00)"),
    ("T002", "new_city", 25, "Goa is not the home city (Pune) and not seen before"),
    ("T003", "high_amount", 35, "Amount 48,000 is 16.0x the customer's average of 3,000"),
    ("T006", "rapid_txn", 30, "3 transactions within 5 minutes"),
    ("T006", "high_amount", 35, "Amount 9,000 is 6.0x the customer's average of 1,500"),
    ("T007", "high_amount", 35, "Amount 25,000 is 10.0x the customer's average of 2,500"),
    ("T007", "odd_hour", 20, "Transaction at 03:45 (window 01:00-04:00)"),
    ("T007", "new_city", 25, "Delhi is not the home city (Bengaluru) and not seen before"),
]

# txn_id, score, level, reasons
RISK_SCORES = [
    ("T001", 90, "High", "30.0x average amount (+35); odd hour 02:14 (+20); new city Dubai (+25); combo (+10)"),
    ("T002", 55, "Medium", "odd hour 01:30 (+20); new city Goa (+25); combo (+10)"),
    ("T003", 35, "Low", "16.0x average amount (+35)"),
    ("T006", 75, "High", "rapid transactions (+30); 6.0x average amount (+35); combo (+10)"),
    ("T007", 90, "High", "10.0x average amount (+35); odd hour 03:45 (+20); new city Delhi (+25); combo (+10)"),
]


def seed_if_needed() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(DDL)
        if conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0:
            conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", CUSTOMERS)
            conn.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", TRANSACTIONS)
            conn.executemany(
                "INSERT INTO rule_results (txn_id, rule_name, points, detail) VALUES (?,?,?,?)", RULE_RESULTS
            )
            conn.executemany(
                "INSERT INTO risk_scores VALUES (?,?,?,?, '2026-03-16 09:00:00')", RISK_SCORES
            )
            conn.commit()
        alert_service.ensure_tables(conn)
        alert_service.create_alerts(conn)
    finally:
        conn.close()


st.set_page_config(page_title="Feature 4 - dev runner", layout="wide")
st.title("🚨 Fraud Monitoring - Feature 4 (dev runner)")

seed_if_needed()
connection = sqlite3.connect(DB_PATH)  # fresh connection per Streamlit run
try:
    alerts.render(connection)
finally:
    connection.close()
