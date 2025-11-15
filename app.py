import streamlit as st
import pandas as pd
from google.oauth2 import service_account
from google.cloud import firestore
from streamlit_autorefresh import st_autorefresh

# -------------------------
# PAGE CONFIG
# -------------------------
st.set_page_config(
    page_title="CFP Playoff Odds — Live Ticker",
    layout="wide",
)

# -------------------------
# FIRESTORE CLIENT
# -------------------------
@st.cache_resource
def get_db():
    creds_dict = st.secrets["firebase"]
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    return firestore.Client(credentials=creds, project=creds_dict["project_id"])

db = get_db()

# auto-refresh every 5 seconds
st_autorefresh(interval=5000, key="cfp_ticker_refresh")

# -------------------------
# HELPERS
# -------------------------
def team_from_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    return ticker.split("-")[-1]

def prob_pct(prob):
    if prob is None:
        return None
    return round(prob * 100, 1)

def prob_text(prob):
    if prob is None:
        return "--"
    return f"{prob * 100:.1f}%"

def delta_text(delta):
    if delta is None or delta == 0:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta * 100:.1f}%"  # delta is still 0–1 scale

# -------------------------
# FETCH CURRENT MARKETS
# -------------------------
doc_ref = db.collection("cfp_markets").document("current")
doc = doc_ref.get()

if not doc.exists:
    st.error("No Firestore document at cfp_markets/current yet.")
    st.stop()

payload = doc.to_dict() or {}
markets = payload.get("markets", [])
df = pd.DataFrame(markets)

if df.empty:
    st.warning("No markets in cfp_markets/current.")
    st.stop()

# sort by probability
df = df.sort_values(by="probability", ascending=False, na_position="last")

# previous probabilities for deltas
if "prev_probs" not in st.session_state:
    st.session_state.prev_probs = {}

# -------------------------
# BASIC STYLING
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

/* Ticker */
.ticker-wrapper {
    width: 100%;
    overflow: hidden;
    background: #020617;
    border-radius: 16px;
    padding: 8px 0;
    border: 1px solid rgba(148, 163, 184, 0.4);
    box-shadow: 0 10px 25px rgba(15, 23, 42, 0.85);
}
.ticker {
    display: inline-block;
    white-space: nowrap !important;
    animation: ticker-scroll 35s linear infinite;
}
@keyframes ticker-scroll {
    from { transform: translateX(0%); }
    to { transform: translateX(-50%); }
}
.ticker-item {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    padding: 4px 10px;
    margin-right: 20px;
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.9);
    border: 1px solid rgba(148, 163, 184, 0.4);
    font-size: 0.9rem;
}
.ticker-team {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    color: #f9fafb;
}
.ticker-prob {
    font-variant-numeric: tabular-nums;
    color: #a5b4fc;
}
.ticker-delta {
    font-variant-numeric: tabular-nums;
    font-size: 0.8rem;
}
.delta-up { color: #22c55e; }
.delta-down { color: #ef4444; }
.delta-flat { color: #9ca3af; }

/* tables */
.block-container {
    padding-top: 1.5rem;
}
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------
# BUILD TICKER HTML
# -------------------------
ticker_html = '<div class="ticker-wrapper"><div class="ticker">'

for _, row in df.iterrows():
    ticker = row.get("ticker")
    team = team_from_ticker(ticker)
    prob = row.get("probability")

    prev_prob = st.session_state.prev_probs.get(ticker)
    delta = None
    delta_class = "delta-flat"

    if prev_prob is not None and prob is not None:
        delta = prob - prev_prob
        if delta > 0:
            delta_class = "delta-up"
        elif delta < 0:
            delta_class = "delta-down"

    p_text = prob_text(prob)
    d_text = delta_text(delta) if delta is not None else ""

    # add up/down arrow
    if delta is not None and delta != 0:
        if delta > 0:
            d_text = "▲ " + d_text
        else:
            d_text = "▼ " + d_text

    ticker_html += f"""
        <span class="ticker-item">
            <span class="ticker-team">{team}</span>
            <span class="ticker-prob">{p_text}</span>
            <span class="ticker-delta {delta_class}">{d_text}</span>
        </span>
    """

# duplicate for continuous scroll
ticker_html += ticker_html
ticker_html += "</div></div>"

# store current probs for next refresh
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

# -------------------------
# CLEAN PRICES TABLE
# -------------------------
st.markdown("### Current Prices")

prices_df = df.copy()
prices_df["team"] = prices_df["ticker"].apply(team_from_ticker)
prices_df["probability (%)"] = prices_df["probability"].apply(prob_pct)

# choose a single price column (yes_price preferred, else last_price)
def pick_price(row):
    if pd.notna(row.get("yes_price")):
        return row.get("yes_price")
    return row.get("last_price")

prices_df["price"] = prices_df.apply(pick_price, axis=1)

display_prices = prices_df[["team", "ticker", "probability (%)", "price"]]

st.dataframe(
    display_prices.sort_values("probability (%)", ascending=False),
    hide_index=True,
    use_container_width=True,
)

# -------------------------
# RECENT MOVERS TABLE
# -------------------------
st.markdown("### Recent Movers")

def load_recent_movers(limit_docs=6, max_rows=25):
    try:
        movers_q = (
            db.collection("movers")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit_docs)
        )
        docs = list(movers_q.stream())
    except Exception as e:
        st.info(f"No movers yet or cannot load movers: {e}")
        return pd.DataFrame()

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

    rows = sorted(rows, key=lambda r: r["timestamp"], reverse=True)[:max_rows]
    return pd.DataFrame(rows)

movers_df = load_recent_movers()

if movers_df.empty:
    st.info("No movers recorded yet.")
else:
    movers_df["team"] = movers_df["ticker"].apply(team_from_ticker)
    movers_df["direction"] = movers_df["change"].apply(
        lambda x: "up" if x is not None and x > 0 else ("down" if x is not None and x < 0 else "flat")
    )
    movers_df["change (pts)"] = movers_df["change"]

    display_movers = movers_df[
        ["timestamp", "team", "ticker", "old", "new", "change (pts)", "direction"]
    ]

    st.dataframe(
        display_movers,
        hide_index=True,
        use_container_width=True,
    )
