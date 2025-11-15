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
# FIRESTORE CLIENT (uses st.secrets, no local secrets file needed)
# -------------------------
@st.cache_resource
def get_db():
    creds_dict = st.secrets["firebase"]
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
    """
    "KXNCAAFPLAYOFF-25-PITT" -> "PITT"
    """
    if not ticker:
        return ""
    return ticker.split("-")[-1]


def format_prob(prob):
    if prob is None:
        return "--"
    return f"{prob * 100:.1f}%"


def format_delta(delta):
    if delta is None:
        return ""
    if delta == 0:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta * 100:.1f}%"


# -------------------------
# FETCH CURRENT MARKETS
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

# sort by probability
df = df.sort_values(by="probability", ascending=False, na_position="last")

# previous probabilities for flash + delta
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
    padding: 10px 0;
    border: 1px solid rgba(148, 163, 184, 0.5);
    box-shadow: 0 18px 35px rgba(15, 23, 42, 0.85);
}

/* One long line, no wrapping */
.ticker-track {
    display: inline-block;
    white-space: nowrap;
    animation: ticker-scroll 35s linear infinite;
}

@keyframes ticker-scroll {
    from { transform: translateX(0%); }
    to { transform: translateX(-100%); }
}

.ticker-item {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    margin: 0 22px;
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

.ticker-delta {
    font-variant-numeric: tabular-nums;
    font-size: 0.8rem;
    opacity: 0.85;
}

.delta-up {
    color: #22c55e;
}

.delta-down {
    color: #ef4444;
}

.delta-flat {
    color: #9ca3af;
}

.flash {
    text-shadow: 0 0 10px rgba(34, 197, 94, 0.8);
}

/* Cards grid for teams */
.card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.75rem;
}

.card {
    background: linear-gradient(145deg, #020617, #030712);
    border-radius: 16px;
    padding: 10px 12px;
    border: 1px solid rgba(55, 65, 81, 0.8);
    box-shadow: 0 10px 20px rgba(15, 23, 42, 0.7);
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 4px;
}

.card-team {
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #e5e7eb;
}

.card-ticker {
    font-size: 0.7rem;
    color: #6b7280;
}

.card-row {
    display: flex;
    justify-content: space-between;
    font-size: 0.82rem;
}

.card-row span:first-child {
    color: #9ca3af;
}

.card-row strong {
    font-variant-numeric: tabular-nums;
}
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------
# BUILD ONE-LINE TICKER HTML (with delta + color)
# -------------------------
ticker_html = '<div class="ticker-container"><div class="ticker-track">'

for _, row in df.iterrows():
    ticker = row.get("ticker")
    team = format_ticker(ticker)
    prob = row.get("probability")

    prev_prob = st.session_state.prev_probs.get(ticker)
    delta = None
    delta_class = "delta-flat"
    flash_class = ""

    if prev_prob is not None and prob is not None:
        delta = prob - prev_prob
        if delta > 0:
            delta_class = "delta-up"
            flash_class = "flash"
        elif delta < 0:
            delta_class = "delta-down"
            flash_class = ""
        else:
            delta_class = "delta-flat"

    prob_text = format_prob(prob)
    delta_text = format_delta(delta)

    ticker_html += (
        f'<span class="ticker-item {flash_class}">'
        f'<span class="ticker-team">{team}</span>'
        f'<span class="ticker-prob">{prob_text}</span>'
        f'<span class="ticker-delta {delta_class}">{delta_text}</span>'
        f"</span>"
    )

ticker_html += "</div></div>"

# update prev probabilities for next refresh
st.session_state.prev_probs = {
    r["ticker"]: r["probability"] for _, r in df.iterrows()
}

# -------------------------
# PAGE LAYOUT
# -------------------------
st.title("🏈 CFP Playoff Odds — Live Ticker")
st.caption("Live probabilities from Kalshi, synced via Firestore.")

st.markdown("### Live Ticker")
st.markdown(ticker_html, unsafe_allow_html=True)

# -------------------------
# All teams table / cards
# -------------------------
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

# -------------------------
# Recent Movers (from `movers` collection)
# -------------------------
st.markdown("### Recent Movers")

def load_recent_movers(limit_docs=8, max_rows=30):
    try:
        # Get most recent mover docs by timestamp (string ISO works lexicographically)
        movers_ref = (
            db.collection("movers")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit_docs)
        )
        docs = list(movers_ref.stream())

        rows = []
        for d in docs:
            payload = d.to_dict() or {}
            ts = payload.get("timestamp")
            for item in payload.get("items", []):
                rows.append(
                    {
                        "timestamp": ts,
                        "ticker": item.get("ticker"),
                        "old": item.get("old"),
                        "new": item.get("new"),
                        "change": item.get("change"),
                    }
                )

        if not rows:
            return pd.DataFrame()

        # Sort combined movers by timestamp desc and limit rows
        rows_sorted = sorted(rows, key=lambda r: r["timestamp"], reverse=True)[:max_rows]
        return pd.DataFrame(rows_sorted)

    except Exception as e:
        st.warning(f"Could not load movers: {e}")
        return pd.DataFrame()

movers_df = load_recent_movers()

if movers_df.empty:
    st.info("No movers recorded yet.")
else:
    # Add sign and percent-style change if your prices are 0–100
    movers_df["change_points"] = movers_df["change"]
    movers_df["direction"] = movers_df["change"].apply(
        lambda x: "up" if x is not None and x > 0 else ("down" if x is not None and x < 0 else "flat")
    )

    st.dataframe(
        movers_df[["timestamp", "ticker", "old", "new", "change_points", "direction"]],
        hide_index=True,
    )
