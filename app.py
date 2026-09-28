"""
Trust Guard — Transaction Fraud Monitoring System
Main Streamlit orchestrator connecting all modules via sidebar navigation.

Currently implements:
  - Feature 1: Transaction Upload & Viewer
  - Feature 2: Fraud Rules Engine
Other feature tabs are placeholders for future development.
"""

import streamlit as st
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from init_db import seed_from_data_dir
from ui.viewer import render as render_viewer
from ui.rules_config import render as render_rules


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Trust Guard — Fraud Monitoring",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS for premium look
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Global font */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    }
    [data-testid="stSidebar"] .stMarkdown h1,
    [data-testid="stSidebar"] .stMarkdown h2,
    [data-testid="stSidebar"] .stMarkdown h3,
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown li {
        color: #e2e8f0 !important;
    }

    /* Metric cards */
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 16px 20px;
    }
    [data-testid="stMetricLabel"] {
        color: #94a3b8 !important;
    }
    [data-testid="stMetricValue"] {
        color: #f1f5f9 !important;
    }

    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 20px;
    }

    /* Dataframe */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
    }

    /* Expander */
    .streamlit-expanderHeader {
        font-weight: 600;
        font-size: 1rem;
    }

    /* Download button */
    .stDownloadButton > button {
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 8px 24px;
        font-weight: 500;
    }
    .stDownloadButton > button:hover {
        background: linear-gradient(135deg, #818cf8 0%, #6366f1 100%);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Seed database on first run
# ---------------------------------------------------------------------------
@st.cache_resource
def _init_database():
    """Seed the SQLite database with default data from CSV files (runs once)."""
    return seed_from_data_dir()


rows_c, rows_t = _init_database()


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        """
        <div style="text-align:center; padding: 16px 0 8px 0;">
            <span style="font-size:2.5rem;">🛡️</span>
            <h2 style="margin:4px 0 0 0; color:#f1f5f9;">Trust Guard</h2>
            <p style="color:#64748b; font-size:0.85rem; margin:0;">
                Fraud Monitoring System
            </p>
        </div>
        <hr style="border-color:#334155; margin:12px 0;">
        """,
        unsafe_allow_html=True,
    )

    nav = st.radio(
        "Navigation",
        [
            "📊 Transaction Viewer",
            "⚙️ Rules Engine",
            "🎯 Risk Scoring",
            "📋 Alerts Dashboard",
            "✅ Verification",
        ],
        index=0,
        label_visibility="collapsed",
    )

    st.markdown(
        """
        <hr style="border-color:#334155; margin:20px 0 12px 0;">
        <div style="text-align:center;">
            <p style="color:#475569; font-size:0.75rem;">
                💾 DB seeded: {c} customers · {t} transactions
            </p>
        </div>
        """.format(c=rows_c, t=rows_t),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page routing
# ---------------------------------------------------------------------------
if nav == "📊 Transaction Viewer":
    render_viewer()
elif nav == "⚙️ Rules Engine":
    render_rules()
elif nav == "🎯 Risk Scoring":
    st.title("🎯 Risk Scoring")
    st.info("🚧 Feature 3 — Coming soon. View transaction risk scores here.")
elif nav == "📋 Alerts Dashboard":
    st.title("📋 Alerts Dashboard")
    st.info("🚧 Feature 4 — Coming soon. Manage fraud alerts here.")
elif nav == "✅ Verification":
    st.title("✅ Verification")
    st.info("🚧 Feature 5 — Coming soon. Customer verification workflow here.")
