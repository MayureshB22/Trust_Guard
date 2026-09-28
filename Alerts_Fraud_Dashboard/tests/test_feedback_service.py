"""
Tests for the feedback loop (feedback_service).
Run from the project root:   python -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import alert_service as alerts_svc  # noqa: E402
from services import feedback_service as fb  # noqa: E402

DDL = """
CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE transactions (txn_id TEXT PRIMARY KEY, customer_id TEXT, timestamp TEXT, amount REAL,
                           merchant TEXT, merchant_category TEXT, city TEXT, channel TEXT);
CREATE TABLE rule_results (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT, rule_name TEXT,
                           points INTEGER, detail TEXT, UNIQUE(txn_id, rule_name));
CREATE TABLE risk_scores (txn_id TEXT PRIMARY KEY, score INTEGER, level TEXT, reasons TEXT, computed_at TEXT);
CREATE TABLE rule_config (rule_name TEXT PRIMARY KEY, enabled INTEGER, param_json TEXT,
                          base_points INTEGER, description TEXT);
"""
POINTS = {"high_amount": 35, "odd_hour": 20, "new_city": 25, "rapid_txn": 30}


def add_alert(conn, txn_id, rules, outcome):
    """Insert a transaction + its rule hits + score + alert; close it if outcome is given."""
    score = min(100, sum(POINTS[r] for r in rules) + (10 if len(rules) >= 2 else 0))
    conn.execute("INSERT INTO transactions VALUES (?, 'C1', '2026-03-10 10:00:00', 1000, 'M', 'Food', 'Pune', 'UPI')",
                 (txn_id,))
    for r in rules:
        conn.execute("INSERT INTO rule_results (txn_id, rule_name, points, detail) VALUES (?,?,?,'')",
                     (txn_id, r, POINTS[r]))
    conn.execute("INSERT INTO risk_scores VALUES (?,?,?,?, 'now')", (txn_id, score, "x", ""))
    conn.commit()
    alerts_svc.create_alerts(conn)
    if outcome:
        aid = conn.execute("SELECT alert_id FROM alerts WHERE txn_id = ?", (txn_id,)).fetchone()[0]
        alerts_svc.update_status(conn, aid, "Closed", resolution=outcome)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    conn.execute("INSERT INTO customers VALUES ('C1', 'Test')")
    conn.executemany("INSERT INTO rule_config VALUES (?, 1, '{}', ?, '')", list(POINTS.items()))
    alerts_svc.ensure_tables(conn)
    n = 0

    def nxt():
        nonlocal n
        n += 1
        return f"T{n:03d}"

    for _ in range(3):
        add_alert(conn, nxt(), ["new_city"], "False Alarm")                      # score 25
    for _ in range(2):
        add_alert(conn, nxt(), ["new_city", "odd_hour"], "False Alarm")          # score 55
    add_alert(conn, nxt(), ["new_city", "high_amount"], "Confirmed Fraud")       # score 70
    for _ in range(2):
        add_alert(conn, nxt(), ["high_amount"], "Confirmed Fraud")               # score 35
    add_alert(conn, nxt(), ["rapid_txn"], "Confirmed Fraud")                     # score 30
    add_alert(conn, nxt(), ["rapid_txn"], "False Alarm")                         # score 30
    add_alert(conn, nxt(), ["new_city", "odd_hour"], None)                       # still Open -> ignored
    return conn


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def tearDown(self):
        self.conn.close()

    def stats(self):
        return fb.rule_feedback_stats(self.conn).set_index("rule_name")

    def test_precision_counts(self):
        s = self.stats()
        # new_city fired on 3 + 2 + 1 = 6 reviewed alerts, only 1 was fraud
        self.assertEqual(s.loc["new_city", "times_fired"], 6)
        self.assertEqual(s.loc["new_city", "confirmed_fraud"], 1)
        self.assertEqual(s.loc["new_city", "false_alarms"], 5)
        self.assertAlmostEqual(s.loc["new_city", "precision_pct"], 16.7)
        self.assertEqual(s.loc["high_amount", "precision_pct"], 100.0)

    def test_open_alerts_are_ignored(self):
        s = self.stats()
        self.assertEqual(s.loc["odd_hour", "times_fired"], 2)  # the Open alert is not counted

    def test_verdicts(self):
        s = self.stats()
        self.assertEqual(s.loc["new_city", "verdict"], "Noisy")
        self.assertEqual(s.loc["high_amount", "verdict"], "Reliable")
        self.assertEqual(s.loc["rapid_txn", "verdict"], "Not enough data")  # only 2 samples
        self.assertEqual(s.loc["odd_hour", "verdict"], "Not enough data")

    def test_suggested_multipliers_only_change_noisy_rules(self):
        m = fb.suggested_multipliers(fb.rule_feedback_stats(self.conn))
        self.assertEqual(m["new_city"], 0.5)
        self.assertEqual(m["high_amount"], 1.0)
        self.assertEqual(m["rapid_txn"], 1.0)

    def test_suggested_points_use_rule_config(self):
        s = self.stats()
        self.assertEqual(s.loc["new_city", "current_points"], 25)
        self.assertEqual(s.loc["new_city", "suggested_points"], 13)  # 12.5 rounds up to 13

    def test_simulation_no_change_when_multipliers_are_one(self):
        r = fb.simulate_tuning(self.conn, {}, review_threshold=50)
        self.assertEqual(r["sent_to_analyst_before"], r["sent_to_analyst_after"])
        self.assertEqual(r["false_alarms_removed"], 0)
        self.assertEqual(r["frauds_dropped"], 0)

    def test_simulation_removes_false_alarms(self):
        # threshold 50: before -> 2 false alarms (score 55) + 1 fraud (score 70) reach an analyst
        r = fb.simulate_tuning(self.conn, {"new_city": 0.5}, review_threshold=50)
        self.assertEqual(r["reviewed_alerts"], 10)
        self.assertEqual(r["sent_to_analyst_before"], 3)
        self.assertEqual(r["sent_to_analyst_after"], 1)      # 55 -> 42.5 (dropped), 70 -> 57.5 (kept)
        self.assertEqual(r["false_alarms_removed"], 2)
        self.assertEqual(r["frauds_dropped"], 0)
        self.assertAlmostEqual(r["workload_reduction_pct"], 66.7)

    def test_simulation_reports_missed_fraud(self):
        # cutting new_city to zero drops the 70-point fraud to 45: this must be reported, not hidden
        r = fb.simulate_tuning(self.conn, {"new_city": 0.0}, review_threshold=50)
        self.assertEqual(r["frauds_dropped"], 1)

    def test_simulation_does_not_change_data(self):
        before = self.conn.execute("SELECT SUM(score) FROM risk_scores").fetchone()[0]
        fb.simulate_tuning(self.conn, {"new_city": 0.1})
        self.assertEqual(before, self.conn.execute("SELECT SUM(score) FROM risk_scores").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT base_points FROM rule_config WHERE rule_name='new_city'")
                         .fetchone()[0], 25)

    def test_apply_updates_rule_config(self):
        changes = fb.apply_suggestions(self.conn, {"new_city": 0.5, "high_amount": 1.0})
        self.assertEqual(changes, [("new_city", 25, 13)])
        get = lambda r: self.conn.execute("SELECT base_points FROM rule_config WHERE rule_name=?", (r,)).fetchone()[0]
        self.assertEqual(get("new_city"), 13)
        self.assertEqual(get("high_amount"), 35)  # untouched

    def test_apply_never_goes_below_one_point(self):
        fb.apply_suggestions(self.conn, {"odd_hour": 0.0})
        self.assertEqual(self.conn.execute("SELECT base_points FROM rule_config WHERE rule_name='odd_hour'")
                         .fetchone()[0], 1)

    def test_apply_without_rule_config_table_raises_friendly_error(self):
        self.conn.execute("DROP TABLE rule_config")
        with self.assertRaises(fb.FeedbackError):
            fb.apply_suggestions(self.conn, {"new_city": 0.5})

    def test_no_reviewed_alerts_gives_empty_results_not_errors(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(DDL)
        alerts_svc.ensure_tables(conn)
        self.assertTrue(fb.rule_feedback_stats(conn).empty)
        self.assertEqual(fb.suggested_multipliers(fb.rule_feedback_stats(conn)), {})
        self.assertEqual(fb.simulate_tuning(conn, {"new_city": 0.5})["reviewed_alerts"], 0)


if __name__ == "__main__":
    unittest.main()
