"""
Feature 2 UI — ui/rules_config.py
Sliders, toggles & controls to configure fraud detection rules in real-time.

Provides:
  • Toggle ON/OFF for each rule
  • Sliders & inputs to adjust thresholds
  • Live preview showing how many transactions get flagged
  • "Apply Rules" button to run the engine and persist flags
  • Summary of flagged results
"""

import streamlit as st
import pandas as pd
from core.rules_engine import (
    load_rule_configs,
    save_rule_configs,
    run_rules_engine,
    save_flags_to_db,
    get_flagged_summary,
    get_flagged_transactions,
    DEFAULT_RULES,
)


def render():
    """Render the Feature 2 — Rules Engine Configuration page."""

    # ---- Page header ----
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #7c3aed 0%, #312e81 100%);
            border-radius: 16px;
            padding: 28px 32px;
            margin-bottom: 24px;
        ">
            <h1 style="margin:0; color:#fff; font-size:2rem;">
                ⚙️ Fraud Rules Engine
            </h1>
            <p style="margin:4px 0 0 0; color:#c4b5fd; font-size:1rem;">
                Configure detection rules, adjust thresholds, and flag suspicious transactions in real-time
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Load current configs ----
    configs = load_rule_configs()

    # ---- Tabs ----
    tab_config, tab_results = st.tabs(["🎛️ Configure Rules", "📊 Flagged Results"])

    # =======================================================================
    # TAB 1 — Configure Rules
    # =======================================================================
    with tab_config:
        _render_rule_configurator(configs)

    # =======================================================================
    # TAB 2 — Flagged Results
    # =======================================================================
    with tab_results:
        _render_flagged_results()


# ---------------------------------------------------------------------------
# Rule configurator
# ---------------------------------------------------------------------------
def _render_rule_configurator(configs: dict):

    st.markdown("### 🎯 Rule Configuration Panel")
    st.markdown(
        "Toggle rules ON/OFF and adjust thresholds. "
        "Click **Apply Rules** at the bottom to run the engine."
    )

    # Keep updated configs in a dict
    updated_configs = {}

    # ---- Rule 1: High Amount vs Customer Avg ----
    _render_rule_divider()
    cfg = configs.get("rule_1_high_amount_vs_avg", DEFAULT_RULES["rule_1_high_amount_vs_avg"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r1_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        multiplier = st.slider(
            "Multiplier (× customer avg monthly spend)",
            min_value=1.0,
            max_value=20.0,
            value=float(cfg["params"].get("multiplier", 5.0)),
            step=0.5,
            key="r1_multiplier",
            disabled=not enabled,
        )
        if enabled:
            st.caption(
                f"📌 A customer with ₹50,000 avg spend will be flagged if txn > ₹{50000 * multiplier:,.0f}"
            )

    updated_configs["rule_1_high_amount_vs_avg"] = {
        **cfg,
        "enabled": enabled,
        "params": {"multiplier": multiplier},
    }

    # ---- Rule 2: Late Night Transaction ----
    _render_rule_divider()
    cfg = configs.get("rule_2_late_night", DEFAULT_RULES["rule_2_late_night"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r2_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        c1, c2 = st.columns(2)
        with c1:
            start_hour = st.number_input(
                "Start Hour (24h format)",
                min_value=0,
                max_value=23,
                value=int(cfg["params"].get("start_hour", 1)),
                key="r2_start",
                disabled=not enabled,
            )
        with c2:
            end_hour = st.number_input(
                "End Hour (24h format)",
                min_value=0,
                max_value=23,
                value=int(cfg["params"].get("end_hour", 4)),
                key="r2_end",
                disabled=not enabled,
            )
        if enabled:
            st.caption(f"📌 Transactions between {start_hour}:00 and {end_hour}:00 will be flagged")

    updated_configs["rule_2_late_night"] = {
        **cfg,
        "enabled": enabled,
        "params": {"start_hour": start_hour, "end_hour": end_hour},
    }

    # ---- Rule 3: City Mismatch ----
    _render_rule_divider()
    cfg = configs.get("rule_3_city_mismatch", DEFAULT_RULES["rule_3_city_mismatch"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r3_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        if enabled:
            st.caption("📌 Compares transaction city with the customer's registered city from the customers dataset")

    updated_configs["rule_3_city_mismatch"] = {**cfg, "enabled": enabled}

    # ---- Rule 4: Rapid-Fire Transactions ----
    _render_rule_divider()
    cfg = configs.get("rule_4_rapid_fire", DEFAULT_RULES["rule_4_rapid_fire"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r4_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        c1, c2 = st.columns(2)
        with c1:
            max_txns = st.number_input(
                "Min transactions to flag",
                min_value=2,
                max_value=20,
                value=int(cfg["params"].get("max_txns", 3)),
                key="r4_max_txns",
                disabled=not enabled,
            )
        with c2:
            window_min = st.number_input(
                "Time window (minutes)",
                min_value=1,
                max_value=120,
                value=int(cfg["params"].get("window_minutes", 10)),
                key="r4_window",
                disabled=not enabled,
            )
        if enabled:
            st.caption(f"📌 Flag if ≥ {max_txns} transactions from same customer within {window_min} minutes")

    updated_configs["rule_4_rapid_fire"] = {
        **cfg,
        "enabled": enabled,
        "params": {"max_txns": max_txns, "window_minutes": window_min},
    }

    # ---- Rule 5: Absolute High Amount ----
    _render_rule_divider()
    cfg = configs.get("rule_5_absolute_high_amount", DEFAULT_RULES["rule_5_absolute_high_amount"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r5_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        threshold = st.number_input(
            "Amount threshold (₹)",
            min_value=1000.0,
            max_value=10000000.0,
            value=float(cfg["params"].get("threshold", 50000.0)),
            step=5000.0,
            key="r5_threshold",
            format="%.0f",
            disabled=not enabled,
        )
        if enabled:
            st.caption(f"📌 Any transaction above ₹{threshold:,.0f} will be flagged")

    updated_configs["rule_5_absolute_high_amount"] = {
        **cfg,
        "enabled": enabled,
        "params": {"threshold": threshold},
    }

    # ---- Rule 7: Channel Anomaly ----
    _render_rule_divider()
    cfg = configs.get("rule_7_channel_anomaly", DEFAULT_RULES["rule_7_channel_anomaly"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r7_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        if enabled:
            st.caption("📌 If a customer has only used 'card' before and now uses 'UPI', the UPI transaction is flagged")

    updated_configs["rule_7_channel_anomaly"] = {**cfg, "enabled": enabled}

    # ---- Rule 9: Duplicate-Like Transaction ----
    _render_rule_divider()
    cfg = configs.get("rule_9_duplicate_like", DEFAULT_RULES["rule_9_duplicate_like"])
    col_toggle, col_config = st.columns([1, 3])

    with col_toggle:
        enabled = st.toggle(
            "Enable", value=cfg.get("enabled", True), key="r9_toggle"
        )
        st.markdown(
            f"""
            <div style="text-align:center; padding:8px;">
                <span style="font-size:2rem;">{cfg['icon']}</span>
                <p style="font-weight:600; margin:4px 0 0 0;">{cfg['name']}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_config:
        st.markdown(f"**{cfg['description']}**")
        c1, c2 = st.columns(2)
        with c1:
            tolerance = st.slider(
                "Amount tolerance (%)",
                min_value=1.0,
                max_value=20.0,
                value=float(cfg["params"].get("tolerance_pct", 5.0)),
                step=1.0,
                key="r9_tol",
                disabled=not enabled,
            )
        with c2:
            window_hrs = st.number_input(
                "Time window (hours)",
                min_value=1,
                max_value=72,
                value=int(cfg["params"].get("window_hours", 24)),
                key="r9_window",
                disabled=not enabled,
            )
        if enabled:
            st.caption(
                f"📌 Flag if same customer + merchant + amount ±{tolerance:.0f}% within {window_hrs}h"
            )

    updated_configs["rule_9_duplicate_like"] = {
        **cfg,
        "enabled": enabled,
        "params": {"tolerance_pct": tolerance, "window_hours": window_hrs},
    }

    # ---- Action buttons ----
    _render_rule_divider()
    st.markdown("### 🚀 Apply Rules")

    enabled_count = sum(1 for c in updated_configs.values() if c.get("enabled", False))
    st.info(f"**{enabled_count}** out of **{len(updated_configs)}** rules are enabled")

    col_save, col_run, col_reset = st.columns(3)

    with col_save:
        if st.button("💾 Save Configuration", use_container_width=True, type="secondary"):
            save_rule_configs(updated_configs)
            st.success("✅ Rule configurations saved!")

    with col_run:
        if st.button("🚀 Apply Rules & Flag Transactions", use_container_width=True, type="primary"):
            save_rule_configs(updated_configs)
            with st.spinner("Running rules engine on all transactions…"):
                flags_df = run_rules_engine(updated_configs)
                save_flags_to_db(flags_df)

            if flags_df.empty:
                st.success("✅ No transactions flagged with current rules.")
            else:
                unique_txns = flags_df["txn_id"].nunique()
                total_flags = len(flags_df)
                st.success(
                    f"✅ Flagged **{unique_txns}** unique transactions "
                    f"with **{total_flags}** total rule violations!"
                )
                # Show quick summary
                st.markdown("**Flags per rule:**")
                rule_counts = flags_df.groupby("rule_name").size().reset_index(name="count")
                rule_counts = rule_counts.sort_values("count", ascending=False)
                st.dataframe(rule_counts, use_container_width=True, hide_index=True)

    with col_reset:
        if st.button("🔄 Reset to Defaults", use_container_width=True, type="secondary"):
            save_rule_configs(DEFAULT_RULES)
            st.success("✅ Rules reset to defaults! Please refresh the page.")
            st.rerun()


# ---------------------------------------------------------------------------
# Flagged results viewer
# ---------------------------------------------------------------------------
def _render_flagged_results():
    st.markdown("### 📊 Flagged Transactions")

    summary = get_flagged_summary()

    if summary.get("total_flagged_txns", 0) == 0:
        st.info(
            "💡 No flagged transactions yet. Go to **Configure Rules** tab and click "
            "**Apply Rules & Flag Transactions** to run the engine."
        )
        return

    # ---- Summary KPIs ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Transactions", f"{summary['total_txns']:,}")
    c2.metric("Flagged Transactions", f"{summary['total_flagged_txns']:,}")
    c3.metric("Total Rule Violations", f"{summary['total_flags']:,}")
    flag_rate = (
        (summary["total_flagged_txns"] / summary["total_txns"] * 100)
        if summary["total_txns"] > 0
        else 0
    )
    c4.metric("Flag Rate", f"{flag_rate:.1f}%")

    # ---- Flags per rule breakdown ----
    st.markdown("#### 📋 Rule-wise Breakdown")
    if summary.get("flags_per_rule"):
        rule_data = pd.DataFrame(
            [
                {"Rule": name, "Flags": count}
                for name, count in summary["flags_per_rule"].items()
            ]
        )
        st.bar_chart(rule_data.set_index("Rule"))

    # ---- Detailed table ----
    st.markdown("#### 🔍 Detailed Flagged Transactions")
    flags_df = get_flagged_transactions()

    if not flags_df.empty:
        # Filter by rule
        rules_list = ["All Rules"] + sorted(flags_df["rule_name"].unique().tolist())
        selected_rule = st.selectbox("Filter by Rule", rules_list, key="flag_filter_rule")

        if selected_rule != "All Rules":
            flags_df = flags_df[flags_df["rule_name"] == selected_rule]

        st.markdown(f"**Showing {len(flags_df):,} flag(s)**")

        display_df = flags_df[["txn_id", "customer_id", "rule_name", "reason", "flagged_at"]].copy()
        display_df.columns = ["Txn ID", "Customer", "Rule", "Reason", "Flagged At"]

        st.dataframe(display_df, use_container_width=True, height=500, hide_index=True)

        # Download
        csv_data = display_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download Flagged Transactions CSV",
            data=csv_data,
            file_name="flagged_transactions.csv",
            mime="text/csv",
        )


# ---------------------------------------------------------------------------
# Helper: visual divider
# ---------------------------------------------------------------------------
def _render_rule_divider():
    st.markdown(
        '<hr style="border: none; border-top: 1px solid #334155; margin: 24px 0;">',
        unsafe_allow_html=True,
    )
