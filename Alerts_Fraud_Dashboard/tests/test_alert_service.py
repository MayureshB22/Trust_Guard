"""
Tests for Feature 4 (alert_service).
Run from the project root:   python -m unittest discover -s tests -v
(or `pytest tests` if you have pytest installed)
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import alert_service as svc  # noqa: E402

UPSTREAM_DDL = """
CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT, home_city TEXT,
                        avg_monthly_spend REAL, avg_txn_amount REAL, phone_masked TEXT);
CREATE TABLE transactions (txn_id TEXT PRIMARY KEY, customer_id TEXT, timestamp TEXT, amount REAL,
                           merchant TEXT, merchant_category TEXT, city TEXT, channel TEXT);
CREATE TABLE rule_results (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT, rule_name TEXT,
                           points INTEGER, detail TEXT, UNIQUE(txn_id, rule_name));
CREATE TABLE risk_scores (txn_id TEXT PRIMARY KEY, score INTEGER, level TEXT, reasons TEXT, computed_at TEXT);
"""


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(UPSTREAM_DDL)
    conn.executemany(
        "INSERT INTO customers VALUES (?,?,?,?,?,?)",
        [("C1", "Priya", "Pune", 30000, 2000, "XXXXXX1111"), ("C2", "Rahul", "Pune", 25000, 1500, "XXXXXX2222")],
    )
    conn.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)",
        [
            ("T1", "C1", "2026-03-12 02:14:00", 60000, "Jewel Palace", "Jewellery", "Dubai", "UPI"),
            ("T2", "C2", "2026-03-13 01:30:00", 3000, "Beach Shack", "Food", "Goa", "card"),
            ("T3", "C2", "2026-03-13 12:00:00", 500, "Tea Stall", "Food", "Pune", "UPI"),  # not flagged
        ],
    )
    conn.executemany(
        "INSERT INTO rule_results (txn_id, rule_name, points, detail) VALUES (?,?,?,?)",
        [
            ("T1", "high_amount", 35, "30x average"),
            ("T1", "odd_hour", 20, "02:14"),
            ("T1", "new_city", 25, "Dubai"),
            ("T2", "odd_hour", 20, "01:30"),
            ("T2", "new_city", 25, "Goa"),
        ],
    )
    conn.executemany(
        "INSERT INTO risk_scores VALUES (?,?,?,?,?)",
        [
            ("T1", 90, "High", "30x average (+35); 02:14 (+20); new city Dubai (+25)", "2026-03-12 02:15:00"),
            ("T2", 55, "Medium", "01:30 (+20); new city Goa (+25)", "2026-03-13 01:31:00"),
        ],
    )
    conn.commit()
    svc.ensure_tables(conn)
    return conn


class AlertServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    # ---- alert creation -------------------------------------------------
    def test_create_alerts_one_per_scored_txn(self):
        self.assertEqual(svc.create_alerts(self.conn), 2)
        self.assertEqual(len(svc.list_alerts(self.conn)), 2)

    def test_create_alerts_is_idempotent(self):
        svc.create_alerts(self.conn)
        self.assertEqual(svc.create_alerts(self.conn), 0)  # no duplicates on re-run
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0], 2)

    def test_min_score_threshold(self):
        self.assertEqual(svc.create_alerts(self.conn, min_score=60), 1)  # only T1 (90)

    def test_new_alerts_start_open_and_are_logged(self):
        svc.create_alerts(self.conn)
        df = svc.list_alerts(self.conn)
        self.assertTrue((df["status"] == "Open").all())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0], 2)

    # ---- status workflow ------------------------------------------------
    def test_full_workflow_and_audit_trail(self):
        svc.create_alerts(self.conn)
        aid = svc.list_alerts(self.conn, customer="Priya").iloc[0]["alert_id"]
        svc.update_status(self.conn, aid, "Investigating", actor="analyst1", expected_status="Open")
        svc.update_status(self.conn, aid, "Closed", resolution="Confirmed Fraud", note="Customer said NO")
        alert = svc.get_alert(self.conn, aid)
        self.assertEqual(alert["status"], "Closed")
        self.assertEqual(alert["resolution"], "Confirmed Fraud")
        self.assertEqual(alert["analyst_note"], "Customer said NO")
        events = svc.get_alert_events(self.conn, aid)
        self.assertEqual(list(events["new_status"]), ["Open", "Investigating", "Closed"])

    def test_close_requires_resolution(self):
        svc.create_alerts(self.conn)
        with self.assertRaises(svc.AlertError):
            svc.update_status(self.conn, 1, "Closed")
        with self.assertRaises(svc.AlertError):
            svc.update_status(self.conn, 1, "Closed", resolution="Maybe")

    def test_invalid_status_and_unknown_alert(self):
        svc.create_alerts(self.conn)
        with self.assertRaises(svc.AlertError):
            svc.update_status(self.conn, 1, "Deleted")
        with self.assertRaises(svc.AlertError):
            svc.update_status(self.conn, 999, "Investigating")

    def test_stale_update_is_rejected(self):
        """Two analysts: the second one must not silently overwrite the first."""
        svc.create_alerts(self.conn)
        svc.update_status(self.conn, 1, "Investigating")
        with self.assertRaises(svc.AlertError):
            svc.update_status(self.conn, 1, "Closed", resolution="False Alarm", expected_status="Open")
        self.assertEqual(svc.get_alert(self.conn, 1)["status"], "Investigating")  # unchanged

    def test_reopen_clears_resolution(self):
        svc.create_alerts(self.conn)
        svc.update_status(self.conn, 1, "Closed", resolution="False Alarm")
        svc.update_status(self.conn, 1, "Open")
        alert = svc.get_alert(self.conn, 1)
        self.assertEqual(alert["status"], "Open")
        self.assertIsNone(alert["resolution"])

    def test_note_kept_when_not_supplied(self):
        svc.create_alerts(self.conn)
        svc.update_status(self.conn, 1, "Investigating", note="called customer")
        svc.update_status(self.conn, 1, "Closed", resolution="False Alarm")  # note=None
        self.assertEqual(svc.get_alert(self.conn, 1)["analyst_note"], "called customer")

    # ---- listing & filters ---------------------------------------------
    def test_queue_order_and_filters(self):
        svc.create_alerts(self.conn)
        df = svc.list_alerts(self.conn)
        self.assertEqual(list(df["txn_id"]), ["T1", "T2"])  # highest score first
        self.assertEqual(len(svc.list_alerts(self.conn, level="high")), 1)  # case-insensitive
        self.assertEqual(len(svc.list_alerts(self.conn, customer="rahul")), 1)
        self.assertEqual(len(svc.list_alerts(self.conn, min_score=60)), 1)
        svc.update_status(self.conn, 1, "Investigating")
        self.assertEqual(len(svc.list_alerts(self.conn, status=["Open"])), 1)

    def test_closed_alerts_sink_to_bottom(self):
        svc.create_alerts(self.conn)
        svc.update_status(self.conn, 1, "Closed", resolution="False Alarm")  # alert 1 = T1 (highest)
        self.assertEqual(list(svc.list_alerts(self.conn)["txn_id"]), ["T2", "T1"])

    def test_rule_hits(self):
        hits = svc.get_rule_hits(self.conn, "T1")
        self.assertEqual(set(hits["rule_name"]), {"high_amount", "odd_hour", "new_city"})

    # ---- dashboard ------------------------------------------------------
    def test_dashboard_metrics(self):
        svc.create_alerts(self.conn)
        m = svc.dashboard_metrics(self.conn)
        self.assertEqual(m["total_transactions"], 3)
        self.assertEqual(m["flagged_count"], 2)
        self.assertAlmostEqual(m["flag_rate_pct"], 66.7)
        self.assertEqual(m["open_count"], 2)
        self.assertEqual(m["amount_at_risk"], 63000)
        self.assertEqual(m["high_risk_count"], 1)
        self.assertIsNone(m["false_alarm_rate_pct"])

    def test_metrics_update_after_closing(self):
        svc.create_alerts(self.conn)
        svc.update_status(self.conn, 1, "Closed", resolution="Confirmed Fraud")  # T1, 60000
        svc.update_status(self.conn, 2, "Closed", resolution="False Alarm")  # T2, 3000
        m = svc.dashboard_metrics(self.conn)
        self.assertEqual(m["amount_at_risk"], 0)
        self.assertEqual(m["closed_count"], 2)
        self.assertEqual(m["confirmed_fraud_amount"], 60000)
        self.assertEqual(m["false_alarm_rate_pct"], 50.0)

    def test_top_rules(self):
        svc.create_alerts(self.conn)
        tr = svc.top_rules(self.conn)
        counts = dict(zip(tr["rule_name"], tr["times_triggered"]))
        self.assertEqual(counts["odd_hour"], 2)
        self.assertEqual(counts["new_city"], 2)
        self.assertEqual(counts["high_amount"], 1)

    def test_empty_database_gives_zeros_not_errors(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(UPSTREAM_DDL)
        svc.ensure_tables(conn)
        m = svc.dashboard_metrics(conn)
        self.assertEqual(m["flagged_count"], 0)
        self.assertEqual(m["amount_at_risk"], 0)
        self.assertTrue(svc.list_alerts(conn).empty)
        self.assertTrue(svc.top_rules(conn).empty)
        self.assertEqual(svc.create_alerts(conn), 0)


if __name__ == "__main__":
    unittest.main()
