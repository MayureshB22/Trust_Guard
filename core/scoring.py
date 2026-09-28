import sqlite3
import pandas as pd
import os
from typing import Dict, List, Tuple
from core.rules_engine import get_connection, run_rules_engine, save_flags_to_db

# Rule weights mapping Member 2's rule IDs to scoring points
RULE_WEIGHTS = {
    "rule_1_high_amount_vs_avg": 35,
    "rule_4_rapid_fire": 25,
    "rule_2_late_night": 20,
    "rule_3_city_mismatch": 15,
    "rule_5_absolute_high_amount": 15,
    "rule_9_duplicate_like": 15,
    "rule_7_channel_anomaly": 10,
}

def calculate_risk_score_from_flags(flags: List[Dict[str, str]]) -> Tuple[int, List[str], str]:
    """
    Computes a risk score from 0 to 100 and aggregates reasons 
    based on the list of rule violations found by Feature 2.
    """
    if not flags:
        return 0, ["Normal transaction - no rules triggered"], "Low"

    score = 0
    reasons = []

    for flag in flags:
        rule_id = flag.get("rule_id", "")
        pts = RULE_WEIGHTS.get(rule_id, 10)  # Default fallback 10 pts
        score += pts
        reasons.append(f"{flag['rule_name']} (+{pts} pts): {flag['reason']}")

    # Compounding penalty if 3 or more rules trigger
    if len(flags) >= 3:
        score += 10
        reasons.append("Compound Threat (+10 pts): 3 or more risk rules triggered simultaneously.")

    # Bound score between 0 and 100
    final_score = min(100, max(0, score))

    # Severity classification
    if final_score >= 75:
        severity = "High"
    elif final_score >= 40:
        severity = "Medium"
    else:
        severity = "Low"

    return final_score, reasons, severity


def enrich_and_store_risk_scores() -> pd.DataFrame:
    """
    Pulls violations from Member 2's flagged_transactions table, 
    calculates risk scores (0-100), and populates the alerts table.
    """
    conn = get_connection()
    
    # 1. Fetch flagged transactions from Feature 2
    query_flags = "SELECT txn_id, customer_id, rule_id, rule_name, reason FROM flagged_transactions"
    df_flags = pd.read_sql_query(query_flags, conn)

    if df_flags.empty:
        conn.close()
        return pd.DataFrame()

    # 2. Fetch transaction metadata
    query_txns = "SELECT txn_id, amount, timestamp, merchant, city, channel FROM transactions"
    df_txns = pd.read_sql_query(query_txns, conn)

    # 3. Group flags by txn_id and compute risk scores
    alerts_data = []
    for txn_id, group in df_flags.groupby("txn_id"):
        flags_list = group.to_dict("records")
        score, reasons, severity = calculate_risk_score_from_flags(flags_list)
        
        # Get transaction info
        txn_row = df_txns[df_txns["txn_id"] == txn_id]
        amount = float(txn_row["amount"].iloc[0]) if not txn_row.empty else 0.0
        cust_id = group["customer_id"].iloc[0]

        alerts_data.append({
            "txn_id": txn_id,
            "customer_id": cust_id,
            "amount": amount,
            "risk_score": score,
            "risk_severity": severity,
            "risk_reasons": " | ".join(reasons),
            "status": "Open",  # Required by Feature 4: Open, Investigating, Closed
            "triggered_rules_count": len(flags_list)
        })

    df_alerts = pd.DataFrame(alerts_data)
    df_alerts = df_alerts.sort_values(by="risk_score", ascending=False)

    # 4. Save to alerts table
    df_alerts.to_sql("alerts", conn, if_exists="replace", index=False)
    conn.close()

    return df_alerts