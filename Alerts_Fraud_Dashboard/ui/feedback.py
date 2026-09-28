"""
FEEDBACK LOOP screen - "Learning from analyst feedback"

app.py only needs:
    from ui import feedback
    feedback.render(conn)

No SQL and no logic here: everything comes from services/feedback_service.py.
"""
import streamlit as st

from services import alert_service
from services import feedback_service as fb

VERDICT_BADGE = {
    "Noisy": "🔴 Noisy",
    "Mixed": "🟡 Mixed",
    "Reliable": "🟢 Reliable",
    "Not enough data": "⚪ Not enough data",
}
RESET_FLAG = "fb_reset_sliders"
FLASH = "fb_flash"


def render(conn) -> None:
    st.subheader("🧠 Learning from analyst feedback")
    st.caption(
        "Every alert closed as Confirmed Fraud or False Alarm teaches the system how trustworthy each rule is. "
        "Illustrative results from our synthetic dataset."
    )

    item = st.session_state.pop(FLASH, None)
    if item:
        getattr(st, item[0])(item[1])

    try:
        stats = fb.rule_feedback_stats(conn)
        metrics = alert_service.dashboard_metrics(conn)
    except Exception as exc:
        st.error(f"Could not load feedback data: {exc}")
        return

    if stats.empty:
        st.info(
            "No reviewed alerts yet. Close some alerts with an outcome "
            "(Confirmed Fraud or False Alarm) and the system will start learning."
        )
        return

    # ---- 1. what analysts told us ------------------------------------------
    reviewed = metrics["confirmed_fraud_count"] + metrics["false_alarm_count"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Reviewed alerts", reviewed)
    c2.metric("Confirmed fraud", metrics["confirmed_fraud_count"])
    fa = metrics["false_alarm_rate_pct"]
    c3.metric("False-alarm rate", "n/a" if fa is None else f"{fa}%")

    st.markdown("### 1. How accurate is each rule?")
    st.caption("Precision = of the alerts where this rule fired, how many were real fraud?")
    table = stats.copy()
    table["verdict"] = table["verdict"].map(lambda v: VERDICT_BADGE.get(v, v))
    st.dataframe(
        table[["rule_name", "times_fired", "confirmed_fraud", "false_alarms", "precision_pct",
               "verdict", "suggestion"]],
        hide_index=True,
    )
    st.bar_chart(stats.set_index("rule_name")["precision_pct"])

    # ---- 2. what-if ---------------------------------------------------------
    st.markdown("### 2. What if we changed the rule points?")
    st.caption(
        "Slide a rule's points down (or up) and see what would have happened to the alerts already reviewed. "
        "Nothing is changed until you press Apply."
    )

    if st.session_state.pop(RESET_FLAG, False):  # must run BEFORE the sliders are created
        for rule in stats["rule_name"]:
            st.session_state[f"fb_mult_{rule}"] = 1.0

    threshold = st.slider(
        "Alerts scoring at or above this go to a human analyst", 0, 100, fb.DEFAULT_REVIEW_THRESHOLD, key="fb_threshold"
    )
    suggested = fb.suggested_multipliers(stats)
    multipliers = {}
    cols = st.columns(len(stats))
    for col, rule in zip(cols, stats["rule_name"]):
        multipliers[rule] = col.slider(
            f"{rule} points ×", 0.0, 1.5, float(suggested.get(rule, 1.0)), 0.05, key=f"fb_mult_{rule}"
        )

    try:
        sim = fb.simulate_tuning(conn, multipliers, threshold)
    except Exception as exc:
        st.error(f"Could not run the simulation: {exc}")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Alerts sent to analysts",
        sim["sent_to_analyst_after"],
        delta=sim["sent_to_analyst_after"] - sim["sent_to_analyst_before"],
        delta_color="inverse",
        help="Reviewed alerts that would still score above the line, versus today",
    )
    m2.metric(
        "False alarms reaching analysts",
        sim["false_alarms_after"],
        delta=sim["false_alarms_after"] - sim["false_alarms_before"],
        delta_color="inverse",
    )
    m3.metric(
        "Real frauds still caught",
        sim["frauds_after"],
        delta=sim["frauds_after"] - sim["frauds_before"],
    )
    st.write(
        f"Analyst workload change: **{-sim['workload_reduction_pct']:.1f}%** "
        f"({sim['sent_to_analyst_before']} → {sim['sent_to_analyst_after']} alerts)."
    )
    if sim["frauds_dropped"] > 0:
        st.warning(
            f"{sim['frauds_dropped']} confirmed fraud alert(s) would fall below the review line. "
            "Check this trade-off before applying."
        )
    elif sim["sent_to_analyst_before"] > 0:
        st.success("No confirmed fraud would be missed with these settings.")

    # ---- 3. apply -----------------------------------------------------------
    st.markdown("### 3. Apply the change (human decides)")
    changed = any(abs(v - 1.0) > 1e-9 for v in multipliers.values())
    with st.expander("Apply these point changes to the live rules"):
        st.caption(
            "Saves the new points into the rule settings (rule_config). They apply to transactions "
            "processed from now on; run detection again to re-score existing ones."
        )
        sure = st.checkbox("I understand this changes the live rule settings", key="fb_confirm")
        if st.button("Apply changes", disabled=not (sure and changed), key="fb_apply"):
            try:
                changes = fb.apply_suggestions(conn, multipliers)
            except fb.FeedbackError as exc:
                st.error(str(exc))
            else:
                if changes:
                    text = ", ".join(f"{r}: {o} → {n} points" for r, o, n in changes)
                    st.session_state[FLASH] = ("success", f"Rules updated. {text}")
                else:
                    st.session_state[FLASH] = ("info", "Nothing to change.")
                st.session_state[RESET_FLAG] = True  # sliders go back to ×1.0 so changes never compound
                st.rerun()
