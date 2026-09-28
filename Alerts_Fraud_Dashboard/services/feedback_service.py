"""
FEEDBACK LOOP - "Can the system learn from alerts analysts mark as false alarms?"
(Feature 5's learning half; built on the outcomes stored by Feature 4.)

The idea in one paragraph
-------------------------
Every alert closed as 'Confirmed Fraud' or 'False Alarm' is a labelled example.
For each rule we count how often it fired on a REAL fraud (its precision).
Rules that mostly cause false alarms get a suggestion to lower their points.
Before changing anything, simulate_tuning() replays the past labelled alerts with
the new points and shows: how many alerts analysts would have reviewed, how many
false alarms disappear, and whether any real fraud would be missed.
An analyst then decides (human in the loop) and apply_suggestions() saves the new points.

No machine learning: it is transparent counting, so every suggestion can be explained.

Reads : alerts (F4), rule_results (F2), risk_scores (F3), rule_config (F2/F3)
Writes: rule_config.base_points, ONLY inside apply_suggestions() when the analyst clicks Apply
"""
from __future__ import annotations

import sqlite3

import pandas as pd

# --- tunable settings (plain numbers, easy to explain and change) -------------------
MIN_SAMPLES = 3          # need at least this many reviewed alerts before judging a rule
NOISY_BELOW = 50.0       # precision % under this  -> rule is "Noisy"
RELIABLE_FROM = 80.0     # precision % at/above    -> rule is "Reliable"
NOISY_MULTIPLIER = 0.5   # suggestion for noisy rules: halve their points
DEFAULT_REVIEW_THRESHOLD = 50  # alerts scoring >= this are sent to a human analyst

_LABELLED = "a.status = 'Closed' AND a.resolution IN ('Confirmed Fraud', 'False Alarm')"

STATS_COLUMNS = [
    "rule_name", "times_fired", "confirmed_fraud", "false_alarms", "precision_pct",
    "verdict", "suggestion", "suggested_multiplier", "current_points", "suggested_points",
]


class FeedbackError(Exception):
    """Raised when the feedback tables/settings cannot be read or updated."""


# ----------------------------------------------------------------------------
# 1. Rule accuracy from analyst outcomes
# ----------------------------------------------------------------------------
def _classify(fired: int, precision: float) -> tuple[str, str, float]:
    """Turn (times fired, precision %) into (verdict, suggestion text, points multiplier)."""
    if fired < MIN_SAMPLES:
        return ("Not enough data", f"Need at least {MIN_SAMPLES} reviewed alerts (have {fired}).", 1.0)
    if precision < NOISY_BELOW:
        cut = int(round((1 - NOISY_MULTIPLIER) * 100))
        return ("Noisy", f"Reduce points by {cut}%: only {precision:.0f}% of {fired} reviewed alerts were real fraud.",
                NOISY_MULTIPLIER)
    if precision >= RELIABLE_FROM:
        return ("Reliable", f"Keep as is: {precision:.0f}% of {fired} reviewed alerts were real fraud.", 1.0)
    return ("Mixed", f"Keep and monitor: {precision:.0f}% of {fired} reviewed alerts were real fraud.", 1.0)


def _current_points(conn: sqlite3.Connection) -> dict:
    """Current base points per rule from rule_config ({} if that table is unavailable)."""
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT rule_name, base_points FROM rule_config").fetchall()}
    except sqlite3.Error:
        return {}


def rule_feedback_stats(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    One row per rule, using ONLY closed alerts that have an outcome.
    precision_pct = confirmed_fraud / times_fired * 100
    (a rule that fires together with other rules shares the credit - a deliberate simplification).
    """
    df = pd.read_sql_query(
        f"""
        SELECT rr.rule_name,
               COUNT(*) AS times_fired,
               SUM(CASE WHEN a.resolution = 'Confirmed Fraud' THEN 1 ELSE 0 END) AS confirmed_fraud,
               SUM(CASE WHEN a.resolution = 'False Alarm'     THEN 1 ELSE 0 END) AS false_alarms
        FROM rule_results rr
        JOIN alerts a ON a.txn_id = rr.txn_id
        WHERE {_LABELLED}
        GROUP BY rr.rule_name
        ORDER BY rr.rule_name
        """,
        conn,
    )
    if df.empty:
        return pd.DataFrame(columns=STATS_COLUMNS)

    points = _current_points(conn)
    rows = []
    for r in df.itertuples():
        fired, confirmed = int(r.times_fired), int(r.confirmed_fraud)
        precision = round(100 * confirmed / fired, 1)
        verdict, suggestion, mult = _classify(fired, precision)
        current = points.get(r.rule_name)
        suggested = max(1, int(current * mult + 0.5)) if current is not None else None
        rows.append({
            "rule_name": r.rule_name, "times_fired": fired, "confirmed_fraud": confirmed,
            "false_alarms": int(r.false_alarms), "precision_pct": precision,
            "verdict": verdict, "suggestion": suggestion, "suggested_multiplier": mult,
            "current_points": current, "suggested_points": suggested,
        })
    return pd.DataFrame(rows, columns=STATS_COLUMNS)


def suggested_multipliers(stats: pd.DataFrame) -> dict:
    """{rule_name: multiplier} taken from rule_feedback_stats() output."""
    if stats.empty:
        return {}
    return {r.rule_name: float(r.suggested_multiplier) for r in stats.itertuples()}


# ----------------------------------------------------------------------------
# 2. What-if simulation (safe: reads only, changes nothing)
# ----------------------------------------------------------------------------
def simulate_tuning(
    conn: sqlite3.Connection,
    multipliers: dict,
    review_threshold: int = DEFAULT_REVIEW_THRESHOLD,
) -> dict:
    """
    Replay all reviewed alerts as if each rule's points were multiplied by multipliers[rule]
    (missing rule = 1.0 = unchanged). new_score = old_score + sum(points * (multiplier - 1)),
    limited to 0..100. Alerts with score >= review_threshold would go to a human analyst.
    """
    result = {
        "reviewed_alerts": 0,
        "sent_to_analyst_before": 0, "sent_to_analyst_after": 0, "workload_reduction_pct": 0.0,
        "false_alarms_before": 0, "false_alarms_after": 0, "false_alarms_removed": 0,
        "frauds_before": 0, "frauds_after": 0, "frauds_dropped": 0,
    }
    alerts = pd.read_sql_query(
        f"""
        SELECT a.alert_id, a.txn_id, a.resolution, r.score
        FROM alerts a JOIN risk_scores r ON r.txn_id = a.txn_id
        WHERE {_LABELLED}
        """,
        conn,
    )
    if alerts.empty:
        return result

    hits = pd.read_sql_query(
        f"""
        SELECT rr.txn_id, rr.rule_name, rr.points
        FROM rule_results rr JOIN alerts a ON a.txn_id = rr.txn_id
        WHERE {_LABELLED}
        """,
        conn,
    )
    if not hits.empty:
        hits["delta"] = hits["rule_name"].map(lambda n: float(multipliers.get(n, 1.0)) - 1.0) * hits["points"]
        delta = hits.groupby("txn_id")["delta"].sum()
        alerts["new_score"] = (alerts["score"] + alerts["txn_id"].map(delta).fillna(0.0)).clip(0, 100)
    else:
        alerts["new_score"] = alerts["score"].astype(float)

    before = alerts["score"] >= review_threshold
    after = alerts["new_score"] >= review_threshold
    fraud = alerts["resolution"] == "Confirmed Fraud"
    false = ~fraud

    rb, ra = int(before.sum()), int(after.sum())
    result.update({
        "reviewed_alerts": len(alerts),
        "sent_to_analyst_before": rb,
        "sent_to_analyst_after": ra,
        "workload_reduction_pct": round(100 * (rb - ra) / rb, 1) if rb else 0.0,
        "false_alarms_before": int((before & false).sum()),
        "false_alarms_after": int((after & false).sum()),
        "false_alarms_removed": int((before & ~after & false).sum()),
        "frauds_before": int((before & fraud).sum()),
        "frauds_after": int((after & fraud).sum()),
        "frauds_dropped": int((before & ~after & fraud).sum()),
    })
    return result


# ----------------------------------------------------------------------------
# 3. Apply (only when the analyst clicks the button)
# ----------------------------------------------------------------------------
def apply_suggestions(conn: sqlite3.Connection, multipliers: dict) -> list[tuple[str, int, int]]:
    """
    Multiply rule_config.base_points by each multiplier (new points >= 1).
    Rules with multiplier 1.0 are untouched. Returns [(rule_name, old_points, new_points)].
    Only affects transactions scored from now on; old scores are unchanged until detection is re-run.
    """
    changes: list[tuple[str, int, int]] = []
    try:
        for rule, mult in multipliers.items():
            if abs(float(mult) - 1.0) < 1e-9:
                continue
            row = conn.execute("SELECT base_points FROM rule_config WHERE rule_name = ?", (rule,)).fetchone()
            if row is None:
                continue
            old = int(row[0])
            new = max(1, int(old * float(mult) + 0.5))
            if new != old:
                conn.execute("UPDATE rule_config SET base_points = ? WHERE rule_name = ?", (new, rule))
                changes.append((rule, old, new))
        conn.commit()
        return changes
    except sqlite3.Error as exc:
        conn.rollback()
        raise FeedbackError(f"Could not update rule settings: {exc}") from exc
