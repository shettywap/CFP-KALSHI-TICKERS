import streamlit as st
import streamlit.components.v1 as components
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

# -------------------------
# OPTIONAL MANUAL REFRESH BUTTON
# -------------------------
st.markdown("### 🔄 Manual Refresh")
if st.button("Refresh Now"):
    st.experimental_rerun()

# auto-refresh every 5 seconds
st_autorefresh(interval=5000, key="auto_refresh_cfp")

# -------------------------
# HELPERS
# -------------------------
def team_from_ticker(ticker):
    return ticker.split("-")[-1] if ticker else ""

def prob_text(prob):
    return "--" if prob is None else f"{prob*100:.1f}%"

def delta_text(delta):
    if delta is None:
        return ""
    if delta == 0:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta*100:.1f}%"


# -------------------------
# LOAD CURRENT MARKETS
# -------------------------
doc = db.collection("cfp_markets").document("current").get()

if not doc.exists:
    st.error("No data found in Firestore yet.")
    st.stop()

raw = doc.to_dict()
df = pd.DataFrame(raw["markets"])
df = df.sort_values("probability", ascending=False)

# save prev for deltas
if "prev_probs" not in st.session_state:
    st.session_state.prev_probs = {}

# -------------------------
# COMPUTE TICKER ITEMS
# -------------------------
ticker_rows = []
for _, r in df.iterrows():
    ticker = r["ticker"]
    team = team_from_ticker(ticker)
    prob = r["probability"]

    prev = st.session_state.prev_probs.get(ticker)
    delta = None
    direction = "flat"

    if prev is not None:
        delta = prob - prev
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"

    ticker_rows.append({
        "team": team,
        "prob_text": prob_text(prob),
        "delta": delta,
        "delta_text": delta_text(delta),
        "dir": direction,
    })

# update state for next refresh
st.session_state.prev_probs = {r["ticker"]: r["probability"] for _, r in df.iterrows()}

# -------------------------
# BUILD TICKER HTML
# -------------------------
ticker_items_html = ""
for item in ticker_rows:
    arrow = "▲" if item["dir"] == "up" else ("▼" if item["dir"] == "down" else "")
    cls = {
        "up": "delta-up",
        "down": "delta-down",
        "flat": "delta-flat"
    }[item["dir"]]

    ticker_items_html += f"""
    <div class="ticker-item">
        <span class="ticker-team">{item['team']}</span>
        <span class="ticker-prob">{item['prob_text']}</span>
        <span class="ticker-delta {cls}">{arrow} {item['delta_text']}</span>
    </div>
    """

# duplicate for scrolling
track_html = ticker_items_html + ticker_items_html

# -------------------------
# TICKER COMPONENT
# -------------------------
ticker_html = f"""
<html>
<head>
<style>
.ticker-wrapper {{
  width: 100%;
  overflow: hidden;
  background: #021024;
  border-radius: 16px;
  padding: 8px 0;
  border: 1px solid rgba(255,255,255,0.15);
}}
.ticker-track {{
  display: inline-flex;
  white-space: nowrap;
  animation: scroll 25s linear infinite;
}}
@keyframes scroll {{
  from {{ transform: translateX(0%); }}
  to {{ transform: translateX(-50%); }}
}}
.ticker-item {{
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 14px;
  margin-right: 18px;
  border-radius: 999px;
  background: rgba(255,255,255,0.05);
  font-family: sans-serif;
  font-size: 0.9rem;
}}
.ticker-team {{ color: #fff; }}
.ticker-prob {{ color: #a5b4fc; font-variant-numeric: tabular-nums; }}
.delta-up {{ color: #22c55e; }}
.delta-down {{ color: #ef4444; }}
.delta-flat {{ color: #94a3b8; }}
</style>
</head>
<body>
<div class="ticker-wrapper">
    <div class="ticker-track">
        {track_html}
    </div>
</div>
</body>
</html>
"""

# -------------------------
# DISPLAY TICKER
# -------------------------
st.markdown("### 📈 Live Ticker")
components.html(ticker_html, height=70, scrolling=False)

# ============================================================
# 🔥 RECENT MOVERS (now near top + color-coded)
# ============================================================
st.markdown("### 🔥 Recent Movers (Last 20 Events)")

def load_movers(limit=10):
    docs = (
        db.collection("movers")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    rows = []
    for doc in docs:
        d = doc.to_dict()
        ts = d["timestamp"]
        for m in d["items"]:
            m["timestamp"] = ts
            m["team"] = team_from_ticker(m["ticker"])
            rows.append(m)
    return pd.DataFrame(rows)

movers_df = load_movers()

if movers_df.empty:
    st.info("No movers yet.")
else:
    movers_df["color"] = movers_df["change"].apply(
        lambda x: "🟢 UP" if x > 0 else ("🔴 DOWN" if x < 0 else "⚪ FLAT")
    )
    display_df = movers_df[["timestamp", "team", "ticker", "old", "new", "change", "color"]]
    st.dataframe(display_df, hide_index=True, use_container_width=True)


# ============================================================
# CLEAN CURRENT PRICES TABLE (moved below movers)
# ============================================================
st.markdown("### 📊 Current Prices")

def pick_price(r):
    return r["yes_price"] if pd.notna(r.get("yes_price")) else r.get("last_price")

df_prices = df.copy()
df_prices["team"] = df_prices["ticker"].apply(team_from_ticker)
df_prices["probability (%)"] = df_prices["probability"].apply(lambda x: round(x*100,1))
df_prices["price"] = df_prices.apply(pick_price, axis=1)

clean_prices = df_prices[["team", "ticker", "probability (%)", "price"]]

st.dataframe(clean_prices, hide_index=True, use_container_width=True)
