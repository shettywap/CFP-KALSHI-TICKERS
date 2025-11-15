import streamlit as st
import pandas as pd
from google.oauth2 import service_account
from google.cloud import firestore
from streamlit_autorefresh import st_autorefresh

# -------------------------
# PAGE CONFIG
# -------------------------
st.set_page_config(
    page_title="CFP Playoff Odds Ticker",
    layout="wide",
)

# -------------------------
# FIRESTORE CLIENT (uses st.secrets, NO local file required)
# -------------------------
@st.cache_resource
def get_db():
    creds_dict = st.secrets["firebase"]  # loaded from Streamlit Cloud secrets
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    return firestore.Client(credentials=creds, project=creds_dict["project_id"])

db = get_db()

# -------------------------
# AUTO REFRESH EVERY 5s
# -------------------------
st_autorefresh(interval=5000, key="cfp_ticker_refresh")

# -------------------------
# HELPERS
# -------------------------
def format_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    return ticker.split("-")[-1]

def format_prob(prob):
    if prob is None:
        return "--"
    return f"{prob * 100:.1f}%"


# -------------------------
# FETCH DATA FROM FIRESTORE
# -------------------------
doc_ref = db.collection("cfp_markets").document("current")
doc = doc_ref.get()

if not doc.exists:
    st.error("No Firestore document at cfp_markets/current yet.")
    st.stop()

data = doc.to_dict()
markets = data.get("markets", [])

df = pd.DataFrame(markets)

if df.empty:
    st.warning("Markets array is empty in cfp_markets/current.")
    st.stop()

df = df.sort_values(by="probability", ascending=False, na_position="last")


# cache previous probabilities to flash on change
if "prev_probs" not in st.session_state:
    st.session_state.prev_probs = {}

# -------------------------
# CSS
# -------------------------
st.markdown(
    """
<style>
body {
    background: radial-gradient(circle at top, #0f172a, #020617 60%);
}

.main {
    padding-top: 1rem;
}

.ticker-container {
    width: 100%;
    overflow: hidden;
    white-space: nowrap;
    background: #020617;
    border-radius: 18px;
    padding: 12px 0;
    border: 1px solid rgba(148, 163, 184, 0.5);
    box-shadow: 0 18px 35px rgba(15, 23, 42, 0.85);
}

.ticker-track {
    display: inline-block;
    animation: ticker-scroll 35s linear infinite;
}

@keyframes ticker-scroll {
    from { transform: translateX(0%); }
    to { transform: translateX(-50%); }
}

.ticker-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin: 0 24px;
    padding: 4px 10px;
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.95);
    box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.45);
    font-size: 0.95rem;
    color: #e5e7eb;
}

.ticker-team {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
}

.ticker-prob {
    font-variant-numeric: tabular-nums;
    color: #a5b4fc;
}

.flash {
    color: #22c55e !important;
    text-shadow: 0 0 10px rgba(34, 197, 94, 0.8);
}
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------
# BUILD TICKER HTML
# -------------------------
ticker_html = '<div class="ticker-container"><div class="ticker-track">'

for _, row in df.iterrows():
    team = format_ticker(row.get("ticker"))
    prob = row.get("probability")
    prev = st.session_state.prev_probs.get(row["ticker"])

    flash_class = ""
    if prev is not None and prev != prob:
        flash_class = "flash"

    prob_text = format_prob(prob)
    ticker_html += (
        f'<span class="ticker-item {flash_class}">'
        f'<span class="ticker-team">{team}</span>'
        f'<span class="ticker-prob">{prob_text}</span>'
        f"</span>"
    )

# duplicate list for endless scroll
ticker_html += ticker_html
ticker_html += "</div></div>"

# update prev probabilities for next refresh
st.session_state.prev_probs = {
    r["ticker"]: r["probability"] for _, r in df.iterrows()
}

# -------------------------
# LAYOUT
# -------------------------
st.title("🏈 CFP Playoff Odds — Live Ticker")
st.caption("Live probabilities from Kalshi, synced via Firestore.")

st.markdown("### Live Ticker")
st.markdown(ticker_html, unsafe_allow_html=True)

st.markdown("### All Teams")
display_df = df.copy()
display_df["team"] = display_df["ticker"].apply(format_ticker)
display_df["probability (%)"] = display_df["probability"].apply(
    lambda x: None if x is None else round(x * 100, 1)
)

st.dataframe(
    display_df[["team", "ticker", "probability (%)", "last_price", "best_bid", "best_ask"]],
    hide_index=True,
)
