"""
Feature 1 UI — ui/viewer.py
Transaction explorer & CSV upload screen built with Streamlit.
Provides:
  • CSV upload for both customers and transactions datasets
  • Dataset overview / summary statistics
  • Interactive filters (customer, date range, amount, channel, category, city, search)
  • Paginated data table with highlighted high-value transactions
"""

import streamlit as st
import pandas as pd
from core.ingestion import (
    ingest_customers_csv,
    ingest_transactions_csv,
    fetch_transactions,
    fetch_customers,
    get_dataset_stats,
    get_unique_values,
    table_exists,
)


# ---------------------------------------------------------------------------
# Helper: format currency
# ---------------------------------------------------------------------------
def _fmt_currency(value: float) -> str:
    """Format a number as Indian-style currency."""
    return f"₹{value:,.2f}"


# ---------------------------------------------------------------------------
# Main page renderer
# ---------------------------------------------------------------------------
def render():
    """Render the Feature 1 — Transaction Upload & Viewer page."""

    # ---- Page header ----
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #1e3a5f 0%, #0f2027 100%);
            border-radius: 16px;
            padding: 28px 32px;
            margin-bottom: 24px;
        ">
            <h1 style="margin:0; color:#fff; font-size:2rem;">
                📊 Transaction Upload &amp; Viewer
            </h1>
            <p style="margin:4px 0 0 0; color:#94a3b8; font-size:1rem;">
                Upload datasets, explore transactions, and apply smart filters
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- Tabs ----
    tab_upload, tab_explore, tab_customers = st.tabs(
        ["📤 Upload Datasets", "🔍 Transaction Explorer", "👥 Customer Explorer"]
    )

    # =======================================================================
    # TAB 1 — Upload Datasets
    # =======================================================================
    with tab_upload:
        _render_upload_section()

    # =======================================================================
    # TAB 2 — Transaction Explorer
    # =======================================================================
    with tab_explore:
        _render_transaction_explorer()

    # =======================================================================
    # TAB 3 — Customer Explorer
    # =======================================================================
    with tab_customers:
        _render_customer_explorer()


# ---------------------------------------------------------------------------
# Upload section
# ---------------------------------------------------------------------------
def _render_upload_section():
    st.markdown("### 📁 Upload Your Datasets")
    st.markdown(
        "Upload **customers.csv** and **transactions.csv** files. "
        "They will be validated and stored in the database for analysis."
    )

    col1, col2 = st.columns(2)

    # ---- Customers upload ----
    with col1:
        st.markdown(
            """
            <div style="
                background: linear-gradient(135deg, #0d9488 0%, #065f46 100%);
                border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;
            ">
                <h4 style="margin:0; color:#fff;">👥 Customers Dataset</h4>
                <p style="color:#a7f3d0; font-size:0.85rem; margin:4px 0 0 0;">
                    Required columns: customer_id, name, city, avg_monthly_spend
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        cust_file = st.file_uploader(
            "Upload customers.csv",
            type=["csv"],
            key="cust_upload",
            help="CSV with columns: customer_id, name, city, avg_monthly_spend",
        )
        if cust_file is not None:
            with st.spinner("Validating & ingesting customers data…"):
                rows, errors = ingest_customers_csv(cust_file)
            if errors:
                for err in errors:
                    st.error(f"❌ {err}")
            else:
                st.success(f"✅ Successfully loaded **{rows}** customers into the database!")

    # ---- Transactions upload ----
    with col2:
        st.markdown(
            """
            <div style="
                background: linear-gradient(135deg, #6366f1 0%, #312e81 100%);
                border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;
            ">
                <h4 style="margin:0; color:#fff;">💳 Transactions Dataset</h4>
                <p style="color:#c7d2fe; font-size:0.85rem; margin:4px 0 0 0;">
                    Required columns: txn_id, customer_id, timestamp, amount,
                    merchant, merchant_category, city, channel
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        txn_file = st.file_uploader(
            "Upload transactions.csv",
            type=["csv"],
            key="txn_upload",
            help="CSV with columns: txn_id, customer_id, timestamp, amount, merchant, merchant_category, city, channel",
        )
        if txn_file is not None:
            with st.spinner("Validating & ingesting transactions data…"):
                rows, errors = ingest_transactions_csv(txn_file)
            if errors:
                for err in errors:
                    st.error(f"❌ {err}")
            else:
                st.success(f"✅ Successfully loaded **{rows}** transactions into the database!")

    # ---- Dataset summary ----
    st.markdown("---")
    _render_dataset_summary()


# ---------------------------------------------------------------------------
# Dataset summary cards
# ---------------------------------------------------------------------------
def _render_dataset_summary():
    st.markdown("### 📈 Dataset Overview")

    if not table_exists("transactions") and not table_exists("customers"):
        st.info("💡 No data loaded yet. Upload CSV files above or the default seed data will be used.")
        return

    stats = get_dataset_stats()

    # ---- KPI Row 1 — Transactions ----
    if stats.get("txn_count", 0) > 0:
        st.markdown("#### 💳 Transactions Summary")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Transactions", f"{stats['txn_count']:,}")
        c2.metric("Total Volume", _fmt_currency(stats["txn_total_amount"]))
        c3.metric("Avg Txn Amount", _fmt_currency(stats["txn_avg_amount"]))
        c4.metric("Unique Customers", f"{stats['txn_unique_customers']}")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Max Txn Amount", _fmt_currency(stats["txn_max_amount"]))
        c6.metric("Min Txn Amount", _fmt_currency(stats["txn_min_amount"]))
        c7.metric("Unique Merchants", f"{stats['txn_unique_merchants']}")
        c8.metric("Payment Channels", f"{len(stats['txn_channels'])}")

        date_from, date_to = stats.get("txn_date_range", ("N/A", "N/A"))
        st.caption(f"📅 Date range: **{date_from}** → **{date_to}**")

        # Category & channel breakdown
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Merchant Categories:**")
            st.write(", ".join(stats.get("txn_categories", [])))
        with col_b:
            st.markdown("**Payment Channels:**")
            st.write(", ".join(stats.get("txn_channels", [])))

    # ---- KPI Row 2 — Customers ----
    if stats.get("cust_count", 0) > 0:
        st.markdown("#### 👥 Customers Summary")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Customers", f"{stats['cust_count']:,}")
        c2.metric("Avg Monthly Spend (mean)", _fmt_currency(stats["cust_avg_spend_mean"]))
        c3.metric("Max Avg Monthly Spend", _fmt_currency(stats["cust_avg_spend_max"]))
        c4.metric("Min Avg Monthly Spend", _fmt_currency(stats["cust_avg_spend_min"]))

        st.markdown("**Customer Cities:**")
        st.write(", ".join(stats.get("cust_cities", [])))


# ---------------------------------------------------------------------------
# Transaction Explorer with filters
# ---------------------------------------------------------------------------
def _render_transaction_explorer():
    if not table_exists("transactions"):
        st.warning("⚠️ No transactions data loaded. Please upload a transactions CSV first.")
        return

    st.markdown("### 🔎 Filter Transactions")

    # ---- Filter controls ----
    with st.expander("🎛️ Filter Options", expanded=True):
        # Row 1
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            customers_list = ["All"] + get_unique_values("transactions", "customer_id")
            selected_customer = st.selectbox(
                "👤 Customer ID", customers_list, key="txn_filter_cust"
            )
        with fc2:
            channels_list = ["All"] + get_unique_values("transactions", "channel")
            selected_channel = st.selectbox(
                "📡 Channel", channels_list, key="txn_filter_channel"
            )
        with fc3:
            categories_list = ["All"] + get_unique_values("transactions", "merchant_category")
            selected_category = st.selectbox(
                "🏷️ Category", categories_list, key="txn_filter_cat"
            )

        # Row 2
        fc4, fc5 = st.columns(2)
        with fc4:
            cities_list = ["All"] + get_unique_values("transactions", "city")
            selected_city = st.selectbox(
                "🏙️ City", cities_list, key="txn_filter_city"
            )
        with fc5:
            search_text = st.text_input(
                "🔍 Search (txn_id, merchant, customer_id)",
                key="txn_search",
                placeholder="e.g. TXN_00287 or Tanishq",
            )

        # Row 3 — Date range & Amount range
        fc6, fc7 = st.columns(2)
        with fc6:
            date_range = st.date_input(
                "📅 Date Range",
                value=[],
                key="txn_date_range",
                help="Select start and end dates",
            )
        with fc7:
            amount_range = st.slider(
                "💰 Amount Range (₹)",
                min_value=0.0,
                max_value=900000.0,
                value=(0.0, 900000.0),
                step=100.0,
                key="txn_amount_range",
                format="₹%.0f",
            )

    # ---- Build filter params ----
    params: dict = {}
    if selected_customer != "All":
        params["customer_id"] = selected_customer
    if selected_channel != "All":
        params["channel"] = selected_channel
    if selected_category != "All":
        params["merchant_category"] = selected_category
    if selected_city != "All":
        params["city"] = selected_city
    if search_text:
        params["search_query"] = search_text
    if date_range:
        if len(date_range) == 2:
            params["date_from"] = str(date_range[0])
            params["date_to"] = str(date_range[1])
        elif len(date_range) == 1:
            params["date_from"] = str(date_range[0])
    if amount_range != (0.0, 900000.0):
        params["amount_min"] = amount_range[0]
        params["amount_max"] = amount_range[1]

    # ---- Fetch & display ----
    df = fetch_transactions(**params)

    # Results header
    st.markdown(f"**Showing {len(df):,} transaction(s)**")

    if df.empty:
        st.info("No transactions match the selected filters.")
        return

    # ---- Style the dataframe ----
    def _highlight_high_amount(row):
        """Highlight rows with amount > 50,000 in red tint."""
        if row["amount"] > 50000:
            return ["background-color: rgba(239, 68, 68, 0.15)"] * len(row)
        elif row["amount"] > 10000:
            return ["background-color: rgba(251, 191, 36, 0.10)"] * len(row)
        return [""] * len(row)

    styled_df = df.style.apply(_highlight_high_amount, axis=1).format(
        {"amount": "₹{:,.2f}"}
    )

    st.dataframe(
        styled_df,
        use_container_width=True,
        height=500,
    )

    # ---- Download filtered data ----
    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Filtered Data as CSV",
        data=csv_data,
        file_name="filtered_transactions.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Customer Explorer
# ---------------------------------------------------------------------------
def _render_customer_explorer():
    if not table_exists("customers"):
        st.warning("⚠️ No customer data loaded. Please upload a customers CSV first.")
        return

    st.markdown("### 👥 Customer Directory")

    fc1, fc2 = st.columns(2)
    with fc1:
        cities = ["All"] + get_unique_values("customers", "city")
        selected_city = st.selectbox("🏙️ City", cities, key="cust_filter_city")
    with fc2:
        search_text = st.text_input(
            "🔍 Search (customer_id, name)",
            key="cust_search",
            placeholder="e.g. CUST_001 or Jeevika",
        )

    params: dict = {}
    if selected_city != "All":
        params["city"] = selected_city
    if search_text:
        params["search_query"] = search_text

    df = fetch_customers(**params)

    st.markdown(f"**Showing {len(df):,} customer(s)**")

    if df.empty:
        st.info("No customers match the selected filters.")
        return

    styled_df = df.style.format({"avg_monthly_spend": "₹{:,.2f}"})

    st.dataframe(styled_df, use_container_width=True, height=450)

    csv_data = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Customer Data as CSV",
        data=csv_data,
        file_name="filtered_customers.csv",
        mime="text/csv",
    )
