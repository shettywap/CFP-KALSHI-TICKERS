import datetime
from zoneinfo import ZoneInfo

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

# AUTO REFRESH (5 seconds)
st_autorefresh(interval=5000, key="auto_refresh_cfp")

# -------------------------
# HELPERS
# -------------------------
def team_from_ticker(ticker: str) -> str:
    return ticker.split("-")[-1] if ticker else ""

def prob_text(prob: float | None) -> str:
    return "--" if prob is None else f"{prob*100:.1f}%"

def prob_pct(prob: float | None) -> float | None:
    return None if prob is None else round(prob * 100, 1)

def delta_text(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta * 100:.1f}%"


def pretty_time(ts: str) -> str:
    """Convert Firestore UTC timestamp → local EST readable format."""
    try:
        # Ensure timezone exists
        if ts.endswith("Z"):
            dt_utc = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt_utc = datetime.datetime.fromisoformat(ts)
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=datetime.timezone.utc)

        eastern = ZoneInfo("America/New_York")
        dt_local = dt_utc.astimezone(eastern)
        now_local = datetime.datetime.now(eastern)
        diff = now_local - dt_local
    except Exception:
        return ts

    if diff.days == 0:
        return dt_local.strftime("%-I:%M %p")
    if diff.days < 7:
        return dt_local.strftime("%a %-I:%M %p")
    return dt_local.strftime("%b %-d, %-I:%M %p")


# -------------------------
# LOAD CURRENT MARKETS
# -------------------------
doc = db.collection("cfp_markets").document("current").get()
if not doc.exists:
    st.error("No data found in Firestore.")
    st.stop()

payload = doc.to_dict()
markets = payload.get("markets", [])
df = pd.DataFrame(markets).sort_values("probability", ascending=False)

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
ticker_rows = []
for _, r in df.iterrows():
    ticker = r["ticker"]
    team = team_from_ticker(ticker)
    prob = r["probability"]

    prev = st.session_state.prev_probs.get(ticker)
    delta = prob - prev if prev is not None else None

    if delta is None:
        direction = "flat"
    elif delta > 0:
        direction = "up"
    elif delta < 0:
        direction = "down"
    else:
        direction = "flat"

    ticker_rows.append({
        "team": team,
        "prob_text": prob_text(prob),
        "delta": delta,
        "delta_text": delta_text(delta),
        "direction": direction,
    })

st.session_state.prev_probs = {
    r["ticker"]: r["probability"] for _, r in df.iterrows()
}

# -------------------------
# TICKER HTML
# -------------------------
ticker_items_html = ""
for item in ticker_rows:
    arrow = "▲" if item["direction"] == "up" else "▼" if item["direction"] == "down" else ""
    cls = {"up": "delta-up", "down": "delta-down", "flat": "delta-flat"}[item["direction"]]

    ticker_items_html += f"""
      <div class="ticker-item">
        <span class="ticker-team">{item['team']}</span>
        <span class="ticker-prob">{item['prob_text']}</span>
        <span class="ticker-delta {cls}">{arrow} {item['delta_text']}</span>
      </div>
    """

track_html = ticker_items_html + ticker_items_html

ticker_html = f"""
<html>
<head>
<style>
body {{ margin:0; padding:0; }}
.ticker-wrapper {{
  width: 100%; background:#020617; overflow:hidden;
  border-radius:16px; padding:8px 0;
  border:1px solid rgba(148,163,184,0.5);
}}
.ticker-track {{
  display:inline-flex; white-space:nowrap;
  animation:scroll 25s linear infinite;
}}
@keyframes scroll {{
  from {{ transform:translateX(0%); }}
  to   {{ transform:translateX(-50%); }}
}}
.ticker-item {{
  display:inline-flex; align-items:baseline; gap:6px;
  padding:4px 14px; margin-right:18px;
  border-radius:999px;
  background:rgba(255,255,255,0.06);
  font-family:sans-serif; font-size:0.9rem;
}}
.ticker-team {{ color:#fff; font-weight:600; text-transform:uppercase; }}
.ticker-prob {{ color:#a5b4fc; font-variant-numeric:tabular-nums; }}
.ticker-delta {{ font-size:0.8rem; font-variant-numeric:tabular-nums; }}
.delta-up {{ color:#22c55e; }}
.delta-down {{ color:#ef4444; }}
.delta-flat {{ color:#94a3b8; }}
</style>
</head>
<body>
<div class="ticker-wrapper">
  <div class="ticker-track">{track_html}</div>
</div>
</body>
</html>
"""

st.markdown("### 📈 Live Ticker")
components.html(ticker_html, height=70, scrolling=False)

# ============================================================
# 🔥 RECENT MOVERS
# ============================================================
st.markdown("### 🔥 Recent Movers")

def load_recent_movers(limit_docs=10):
    docs = (
        db.collection("movers")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit_docs)
        .stream()
    )

    rows = []
    for d in docs:
        blob = d.to_dict() or {}
        ts = blob.get("timestamp")

        for m in blob.get("items", []):
            rows.append({
                "time": pretty_time(ts),
                "team": team_from_ticker(m["ticker"]),
                "old": m["old"],
                "new": m["new"],
                "change": m["change"],
            })
    return pd.DataFrame(rows)

movers_df = load_recent_movers()

if movers_df.empty:
    st.info("No movers yet.")
else:
    movers_df["direction"] = movers_df["change"].apply(
        lambda x: "🟢 UP" if x > 0 else ("🔴 DOWN" if x < 0 else "⚪ FLAT")
    )
    st.dataframe(
        movers_df[["time", "team", "old", "new", "change", "direction"]],
        hide_index=True,
        use_container_width=True,
    )

# ============================================================
# 📊 CURRENT PRICES
# ============================================================
st.markdown("### 📊 Current Prices")

def pick_price(r):
    return r["yes_price"] if pd.notna(r.get("yes_price")) else r["last_price"]

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
