"""
FEATURE 4 - Alerts & Fraud Dashboard (Streamlit screen)
Owner: Member 4

app.py (Member 1) only needs:
    from ui import alerts
    alerts.render(conn)              # both tabs together
or, if the sidebar has separate pages:
    alerts.render_dashboard(conn)    # KPIs + charts
    alerts.render_alerts(conn)       # alert queue + status workflow

This file contains NO fraud logic and NO SQL - it only calls services/alert_service.py.
"""
import streamlit as st

from services import alert_service as svc

STATUS_BADGE = {"Open": "🔴 Open", "Investigating": "🟡 Investigating", "Closed": "🟢 Closed"}
LEVEL_BADGE = {"High": "🔴 High", "Medium": "🟠 Medium", "Low": "🟡 Low"}
ANALYST = "analyst"  # who is recorded in the audit trail
PLACEHOLDER = "-- select outcome --"

DASH_FLASH = "f4_flash_dashboard"
ALERT_FLASH = "f4_flash_alerts"


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------
def _flash(key: str, kind: str, message: str) -> None:
    """Remember a message across st.rerun() so the user sees the result of an action."""
    st.session_state[key] = (kind, message)


def _show_flash(key: str) -> None:
    item = st.session_state.pop(key, None)
    if item:
        kind, message = item
        getattr(st, kind)(message)  # st.success / st.error / st.info


def _money(value: float) -> str:
    return f"₹{value:,.0f}"


def _level_badge(level) -> str:
    return LEVEL_BADGE.get(str(level).title(), str(level))


# ----------------------------------------------------------------------------
# Dashboard tab
# ----------------------------------------------------------------------------
def render_dashboard(conn) -> None:
    st.subheader("Fraud dashboard")
    st.caption("Illustrative results from our synthetic dataset.")
    _show_flash(DASH_FLASH)

    if st.button("Create alerts for newly scored transactions", key="f4_create_alerts"):
        try:
            created = svc.create_alerts(conn)
            _flash(DASH_FLASH, "success", f"{created} new alert(s) created.")
        except svc.AlertError as exc:
            _flash(DASH_FLASH, "error", str(exc))
        st.rerun()

    try:
        m = svc.dashboard_metrics(conn)
        rules_df = svc.top_rules(conn)
        by_day = svc.alerts_by_day(conn)
    except Exception as exc:  # e.g. tables missing because nothing was loaded yet
        st.error(f"Could not load dashboard data: {exc}")
        return

    if m["flagged_count"] == 0:
        st.info("No alerts yet. Upload transactions, run fraud detection, then create alerts.")

    row1 = st.columns(5)
    row1[0].metric("Flagged alerts", m["flagged_count"])
    row1[1].metric("Amount at risk", _money(m["amount_at_risk"]), help="Sum of Open + Investigating alerts")
    row1[2].metric("Open", m["open_count"])
    row1[3].metric("Investigating", m["investigating_count"])
    row1[4].metric("Closed", m["closed_count"])

    row2 = st.columns(4)
    row2[0].metric("High-risk alerts", m["high_risk_count"])
    row2[1].metric("Flag rate", f"{m['flag_rate_pct']}%", help="Flagged ÷ all transactions")
    row2[2].metric("Confirmed fraud", _money(m["confirmed_fraud_amount"]))
    fa = m["false_alarm_rate_pct"]
    row2[3].metric(
        "False-alarm rate",
        "n/a" if fa is None else f"{fa}%",
        help="False alarms ÷ (confirmed fraud + false alarms), among closed alerts",
    )

    st.divider()
    left, right = st.columns(2)
    with left:
        st.markdown("**Most-triggered rules**")
        if rules_df.empty:
            st.caption("No rule hits yet.")
        else:
            st.bar_chart(rules_df.set_index("rule_name")["times_triggered"])
    with right:
        st.markdown("**Alerts by status**")
        status_counts = {
            "Open": m["open_count"],
            "Investigating": m["investigating_count"],
            "Closed": m["closed_count"],
        }
        st.bar_chart(status_counts)

    if not by_day.empty:
        st.markdown("**Alerts by transaction date**")
        st.bar_chart(by_day.set_index("day")["alerts"])


# ----------------------------------------------------------------------------
# Alerts tab
# ----------------------------------------------------------------------------
def render_alerts(conn) -> None:
    st.subheader("Alert queue")
    _show_flash(ALERT_FLASH)

    f1, f2, f3 = st.columns(3)
    status_sel = f1.multiselect("Status", svc.STATUSES, default=["Open", "Investigating"], key="f4_f_status")
    level_sel = f2.multiselect("Risk level", svc.LEVELS, default=[], key="f4_f_level")
    search = f3.text_input("Customer (name or ID)", key="f4_f_customer")

    try:
        df = svc.list_alerts(
            conn,
            status=status_sel or None,
            level=level_sel or None,
            customer=search or None,
        )
    except Exception as exc:
        st.error(f"Could not load alerts: {exc}")
        return

    if df.empty:
        st.info("No alerts match these filters (or no alerts have been created yet).")
        return

    view = df.copy()
    view["status"] = view["status"].map(lambda s: STATUS_BADGE.get(s, s))
    view["level"] = view["level"].map(_level_badge)
    view["customer"] = view["customer_name"].fillna(view["customer_id"])
    st.dataframe(
        view[["alert_id", "status", "resolution", "score", "level", "customer", "timestamp",
              "amount", "city", "channel", "reasons"]],
        hide_index=True,
    )
    st.download_button(
        "Export shown alerts (CSV)",
        df.to_csv(index=False).encode("utf-8"),
        file_name="alerts_export.csv",
        mime="text/csv",
        key="f4_export",
    )

    st.divider()
    _render_alert_detail(conn, df)


def _render_alert_detail(conn, df) -> None:
    st.markdown("### Investigate an alert")
    labels = {
        int(r.alert_id): f"#{int(r.alert_id)} · {r.customer_name or r.customer_id} · "
                         f"{_money(r.amount)} · score {int(r.score)} · {r.status}"
        for r in df.itertuples()
    }
    alert_id = st.selectbox(
        "Select an alert", list(labels.keys()), format_func=lambda i: labels[i], key="f4_select_alert"
    )

    alert = svc.get_alert(conn, alert_id)
    if alert is None:
        st.warning("This alert no longer exists. Refresh the page.")
        return

    # --- what happened + why it was flagged --------------------------------
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Transaction**")
        st.write(
            f"**{alert['customer_name'] or alert['customer_id']}** paid **{_money(alert['amount'])}** "
            f"to *{alert['merchant']}* ({alert['merchant_category']})"
        )
        st.write(f"🕒 {alert['timestamp']} · 📍 {alert['city']} · 💳 {alert['channel']}")
        st.write(f"Txn ID: `{alert['txn_id']}`")
    with c2:
        st.markdown("**Risk score**")
        score = int(alert["score"])
        st.progress(min(max(score, 0), 100) / 100)
        st.write(f"**{score}/100** · {_level_badge(alert['level'])}  _(risk index, not a probability)_")

    st.markdown("**Why it was flagged**")
    hits = svc.get_rule_hits(conn, alert["txn_id"])
    if hits.empty:
        st.caption(alert["reasons"] or "No rule details available.")
    else:
        st.dataframe(hits.rename(columns={"rule_name": "rule", "points": "points", "detail": "detail"}),
                     hide_index=True)

    # --- analyst action ---------------------------------------------------
    st.markdown("**Analyst action**")
    current = alert["status"]
    key_suffix = f"{alert_id}_{current}"  # widgets reset after a successful update

    a1, a2 = st.columns(2)
    new_status = a1.selectbox(
        "Set status", svc.STATUSES, index=svc.STATUSES.index(current), key=f"f4_status_{key_suffix}"
    )
    resolution = None
    if new_status == "Closed":
        options = [PLACEHOLDER] + svc.RESOLUTIONS
        default = options.index(alert["resolution"]) if alert["resolution"] in options else 0
        choice = a2.selectbox("Outcome (required to close)", options, index=default,
                              key=f"f4_res_{key_suffix}")
        resolution = None if choice == PLACEHOLDER else choice

    note = st.text_area("Analyst note", value=alert["analyst_note"] or "", key=f"f4_note_{key_suffix}")

    if st.button("Save update", type="primary", key=f"f4_save_{key_suffix}"):
        try:
            svc.update_status(
                conn,
                alert_id,
                new_status,
                resolution=resolution,
                actor=ANALYST,
                note=note,
                expected_status=current,  # protects against two analysts editing at once
            )
            _flash(ALERT_FLASH, "success", f"Alert #{alert_id} updated to {new_status}.")
            st.rerun()
        except svc.AlertError as exc:
            st.error(str(exc))

    st.markdown("**History**")
    st.dataframe(svc.get_alert_events(conn, alert_id), hide_index=True)


# ----------------------------------------------------------------------------
# Both tabs together (simplest integration for app.py)
# ----------------------------------------------------------------------------
def render(conn) -> None:
    tab_dash, tab_alerts = st.tabs(["📊 Dashboard", "🚨 Alerts"])
    with tab_dash:
        render_dashboard(conn)
    with tab_alerts:
        render_alerts(conn)
