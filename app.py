import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from google.cloud import firestore
from google.oauth2 import service_account
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
# MANUAL + AUTO REFRESH
# -------------------------
# Manual button
st.markdown("### 🔄 Refresh Data")
if st.button("Refresh Now"):
    st.experimental_rerun()

# Auto-refresh every 5 seconds
st_autorefresh(interval=5000, key="auto_refresh_cfp")


# -------------------------
# HELPERS
# -------------------------
def team_from_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    return ticker.split("-")[-1]


def prob_text(prob: float | None) -> str:
    if prob is None:
        return "--"
    return f"{prob * 100:.1f}%"


def delta_text(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta * 100:.1f}%"  # delta is still 0–1 scale


def prob_pct(prob: float | None) -> float | None:
    if prob is None:
        return None
    return round(prob * 100.0, 1)


def pretty_time(ts: str) -> str:
    """Convert ISO timestamp to a human-readable string."""
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", ""))
    except Exception:
        return ts  # fallback

    now = datetime.datetime.utcnow()
    diff = now - dt

    # Today -> show only time
    if diff.days == 0:
        return dt.strftime("%-I:%M %p")  # e.g. 7:32 PM

    # Within the last week -> day + time
    if diff.days < 7:
        return dt.strftime("%a %-I:%M %p")  # e.g. Sat 7:32 PM

    # Older -> date + time
    return dt.strftime("%b %-d, %-I:%M %p")  # e.g. Nov 15, 7:32 PM


# -------------------------
# LOAD CURRENT MARKETS
# -------------------------
doc = db.collection("cfp_markets").document("current").get()
if not doc.exists:
    st.error("No data found in Firestore at cfp_markets/current.")
    st.stop()

payload = doc.to_dict() or {}
markets = payload.get("markets", [])
df = pd.DataFrame(markets)

if df.empty:
    st.error("Markets array is empty in Firestore.")
    st.stop()

# Sort by probability descending
df = df.sort_values("probability", ascending=False, na_position="last")

# Save previous probabilities in session state for deltas
if "prev_probs" not in st.session_state:
    st.session_state.prev_probs = {}


# -------------------------
# HEADER
# -------------------------
st.title("🏈 CFP Playoff Odds — Live Ticker")
st.caption("Live probabilities from Kalshi, synced via Firestore.")


# -------------------------
# BUILD TICKER DATA
# -------------------------
ticker_rows: list[dict] = []

for _, r in df.iterrows():
    ticker = r.get("ticker")
    team = team_from_ticker(ticker)
    prob = r.get("probability")

    prev = st.session_state.prev_probs.get(ticker)
    delta = None
    direction = "flat"

    if prev is not None and prob is not None:
        delta = prob - prev
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"

    ticker_rows.append(
        {
            "team": team,
            "prob": prob,
            "prob_text": prob_text(prob),
            "delta": delta,
            "delta_text": delta_text(delta),
            "direction": direction,
        }
    )

# Update state for next refresh
st.session_state.prev_probs = {
    r["ticker"]: r["probability"] for _, r in df.iterrows()
}


# -------------------------
# BUILD TICKER HTML
# -------------------------
ticker_items_html = ""
for item in ticker_rows:
    direction = item["direction"]
    arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "")
    cls = {"up": "delta-up", "down": "delta-down", "flat": "delta-flat"}[direction]

    ticker_items_html += f"""
      <div class="ticker-item">
        <span class="ticker-team">{item['team']}</span>
        <span class="ticker-prob">{item['prob_text']}</span>
        <span class="ticker-delta {cls}">{arrow} {item['delta_text']}</span>
      </div>
    """

# Duplicate list for seamless scrolling
track_html = ticker_items_html + ticker_items_html

ticker_html = f"""
<html>
<head>
<meta charset="utf-8" />
<style>
body {{
  margin: 0;
  padding: 0;
}}
.ticker-wrapper {{
  width: 100%;
  overflow: hidden;
  background: #020617;
  border-radius: 16px;
  padding: 8px 0;
  border: 1px solid rgba(148, 163, 184, 0.5);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.9);
}}
.ticker-track {{
  display: inline-flex;
  white-space: nowrap;
  animation: scroll 25s linear infinite;
}}
@keyframes scroll {{
  from {{ transform: translateX(0%); }}
  to   {{ transform: translateX(-50%); }}
}}
.ticker-item {{
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 4px 14px;
  margin-right: 18px;
  border-radius: 999px;
  background: rgba(255,255,255,0.06);
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 0.9rem;
}}
.ticker-team {{
  text-transform: uppercase;
  letter-spacing: 0.07em;
  font-weight: 600;
  color: #f9fafb;
}}
.ticker-prob {{
  font-variant-numeric: tabular-nums;
  color: #a5b4fc;
}}
.ticker-delta {{
  font-variant-numeric: tabular-nums;
  font-size: 0.8rem;
}}
.delta-up   {{ color: #22c55e; }}
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

st.markdown("### 📈 Live Ticker")
components.html(ticker_html, height=70, scrolling=False)


# ============================================================
# 🔥 RECENT MOVERS (NEAR TOP, CLEAN, COLOR CODED)
# ============================================================
st.markdown("### 🔥 Recent Movers")


def load_recent_movers(limit_docs: int = 10) -> pd.DataFrame:
    docs = (
        db.collection("movers")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit_docs)
        .stream()
    )

    rows: list[dict] = []
    for d in docs:
        blob = d.to_dict() or {}
        ts = blob.get("timestamp")
        if not ts:
            continue

        for item in blob.get("items", []):
            rows.append(
                {
                    "time": pretty_time(ts),
                    "team": team_from_ticker(item.get("ticker")),
                    "old": item.get("old"),
                    "new": item.get("new"),
                    "change": item.get("change"),
                }
            )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


movers_df = load_recent_movers()

if movers_df.empty:
    st.info("No movers yet.")
else:
    movers_df["direction"] = movers_df["change"].apply(
        lambda x: "🟢 UP" if x > 0 else ("🔴 DOWN" if x < 0 else "⚪ FLAT")
    )

    display_movers = movers_df[["time", "team", "old", "new", "change", "direction"]]

    st.dataframe(
        display_movers,
        hide_index=True,
        use_container_width=True,
    )


# ============================================================
# 📊 CURRENT PRICES (CLEAN TABLE)
# ============================================================
st.markdown("### 📊 Current Prices")


def pick_price(row: pd.Series):
    if pd.notna(row.get("yes_price")):
        return row.get("yes_price")
    return row.get("last_price")


df_prices = df.copy()
df_prices["team"] = df_prices["ticker"].apply(team_from_ticker)
df_prices["probability (%)"] = df_prices["probability"].apply(prob_pct)
df_prices["price"] = df_prices.apply(pick_price, axis=1)

clean_prices = df_prices[["team", "probability (%)", "price"]]

st.dataframe(
    clean_prices.sort_values("probability (%)", ascending=False),
    hide_index=True,
    use_container_width=True,
)
