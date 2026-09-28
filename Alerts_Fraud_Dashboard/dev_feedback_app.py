"""
Standalone runner for the FEEDBACK LOOP (and Feature 4 screens) with ready-made labelled alerts.

    streamlit run dev_feedback_app.py

It builds data/f4_feedback_dev.db: ~30 alerts that analysts already closed, designed so that
  new_city is noisy (mostly false alarms), high_amount is reliable, odd_hour is mixed.
Delete data/f4_feedback_dev.db to reset. Do NOT submit this file; app.py is the real entry point.
"""
import sqlite3
from pathlib import Path

import streamlit as st

from services import alert_service
from ui import alerts, feedback

DB_PATH = Path(__file__).parent / "data" / "f4_feedback_dev.db"

POINTS = {"high_amount": 35, "odd_hour": 20, "new_city": 25, "rapid_txn": 30}
DESCRIPTIONS = {
    "high_amount": "Amount above 5x the customer's average",
    "odd_hour": "Transaction between 1 AM and 4 AM",
    "new_city": "City never used by this customer before",
    "rapid_txn": "Many transactions within a few minutes",
}

# (rules that fired, analyst outcome or None for still Open, how many alerts, amount)
SCENARIOS = [
    (["new_city"], "False Alarm", 6, 3000),
    (["new_city", "odd_hour"], "False Alarm", 4, 2500),
    (["new_city", "odd_hour"], "Confirmed Fraud", 1, 8000),
    (["high_amount", "new_city", "odd_hour"], "Confirmed Fraud", 5, 55000),
    (["high_amount"], "Confirmed Fraud", 2, 40000),
    (["high_amount"], "False Alarm", 1, 30000),
    (["high_amount", "odd_hour"], "Confirmed Fraud", 3, 45000),
    (["rapid_txn", "high_amount"], "Confirmed Fraud", 2, 20000),
    (["odd_hour"], "False Alarm", 3, 1200),
    (["odd_hour"], "Confirmed Fraud", 1, 15000),
    (["rapid_txn"], "False Alarm", 1, 900),
    (["rapid_txn"], "Confirmed Fraud", 1, 4000),
    (["new_city", "odd_hour"], None, 3, 2800),  # still Open
]

DDL = """
CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT, home_city TEXT,
    avg_monthly_spend REAL, avg_txn_amount REAL, phone_masked TEXT);
CREATE TABLE transactions (txn_id TEXT PRIMARY KEY, customer_id TEXT, timestamp TEXT, amount REAL,
    merchant TEXT, merchant_category TEXT, city TEXT, channel TEXT);
CREATE TABLE rule_results (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT, rule_name TEXT,
    points INTEGER, detail TEXT, UNIQUE(txn_id, rule_name));
CREATE TABLE risk_scores (txn_id TEXT PRIMARY KEY, score INTEGER, level TEXT, reasons TEXT, computed_at TEXT);
CREATE TABLE rule_config (rule_name TEXT PRIMARY KEY, enabled INTEGER, param_json TEXT,
    base_points INTEGER, description TEXT);
"""


def build_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(DDL)
        names = ["Priya Sharma", "Rahul Verma", "Anita Desai", "Vikram Singh", "Sneha Rao"]
        conn.executemany(
            "INSERT INTO customers VALUES (?,?,?,?,?,?)",
            [(f"C{i + 1:03d}", n, "Pune", 30000, 2000, f"XXXXXX{i + 1}{i + 1}{i + 1}{i + 1}")
             for i, n in enumerate(names)],
        )
        conn.executemany(
            "INSERT INTO rule_config VALUES (?, 1, '{}', ?, ?)",
            [(r, p, DESCRIPTIONS[r]) for r, p in POINTS.items()],
        )

        outcomes = {}
        n = 0
        for rules, outcome, count, amount in SCENARIOS:
            for _ in range(count):
                n += 1
                txn_id = f"T{n:03d}"
                score = min(100, sum(POINTS[r] for r in rules) + (10 if len(rules) >= 2 else 0))
                level = "High" if score >= 70 else "Medium" if score >= 40 else "Low"
                hour = 2 if "odd_hour" in rules else 14
                city = "Dubai" if "new_city" in rules else "Pune"
                conn.execute(
                    "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
                    (txn_id, f"C{n % 5 + 1:03d}", f"2026-03-{n % 28 + 1:02d} {hour:02d}:15:00",
                     amount, "Sample Merchant", "Retail", city, "UPI"),
                )
                for r in rules:
                    conn.execute(
                        "INSERT INTO rule_results (txn_id, rule_name, points, detail) VALUES (?,?,?,?)",
                        (txn_id, r, POINTS[r], DESCRIPTIONS[r]),
                    )
                reasons = "; ".join(f"{r} (+{POINTS[r]})" for r in rules)
                conn.execute("INSERT INTO risk_scores VALUES (?,?,?,?, '2026-03-30 09:00:00')",
                             (txn_id, score, level, reasons))
                outcomes[txn_id] = outcome
        conn.commit()

        # Use the REAL Feature 4 code path so alerts and the history log look exactly like live use.
        alert_service.ensure_tables(conn)
        alert_service.create_alerts(conn)
        for txn_id, outcome in outcomes.items():
            if outcome:
                alert_id = conn.execute("SELECT alert_id FROM alerts WHERE txn_id = ?", (txn_id,)).fetchone()[0]
                alert_service.update_status(conn, alert_id, "Closed", resolution=outcome, actor="analyst")
    finally:
        conn.close()


st.set_page_config(page_title="Feedback loop - dev runner", layout="wide")
st.title("🚨 Fraud Monitoring - Feedback loop (dev runner)")

if not DB_PATH.exists():
    build_database()

page = st.sidebar.radio("Page", ["Learning loop", "Dashboard & Alerts"])
connection = sqlite3.connect(DB_PATH)  # fresh connection per Streamlit run
try:
    if page == "Learning loop":
        feedback.render(connection)
    else:
        alerts.render(connection)
finally:
    connection.close()
