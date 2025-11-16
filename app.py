import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from google.cloud import firestore
from google.oauth2 import service_account
from streamlit_autorefresh import st_autorefresh

# -------------------------
# CONFIG CONSTANTS
# -------------------------
REFRESH_INTERVAL_MS = 15000      # Streamlit auto-refresh interval
WINDOW_HOURS = 6                 # How far back to compute net change for ticker
MOVER_DOC_LIMIT = 50             # How many mover docs to pull for that window

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
# AUTO REFRESH
# -------------------------
st_autorefresh(interval=REFRESH_INTERVAL_MS, key="auto_refresh_cfp")

# -------------------------
# HELPERS
# -------------------------
def team_from_ticker(ticker: str) -> str:
    return ticker.split("-")[-1] if ticker else ""


def prob_text(prob: Optional[float]) -> str:
    return "--" if prob is None else f"{prob * 100:.1f}%"


def prob_pct(prob: Optional[float]) -> Optional[float]:
    return None if prob is None else round(prob * 100, 1)


def delta_points_text(delta: Optional[float]) -> str:
    """Delta in price points (Kalshi ticks), not probability."""
    if delta is None:
        return ""
    if delta == 0:
        return "0"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.0f}"


def pretty_time(ts: str) -> str:
    """Convert Firestore UTC timestamp string -> America/New_York readable."""
    try:
        # Handle both "...Z" and plain ISO
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


def parse_ts_utc(ts: str) -> Optional[datetime.datetime]:
    try:
        if ts.endswith("Z"):
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        dt = datetime.datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


# -------------------------
# CACHED FIRESTORE READS
# -------------------------
@st.cache_data(ttl=5)
def load_current_markets() -> Optional[Dict]:
    doc = db.collection("cfp_markets").document("current").get()
    if not doc.exists:
        return None
    return doc.to_dict() or {}


@st.cache_data(ttl=5)
def load_recent_movers_raw(limit_docs: int = MOVER_DOC_LIMIT) -> List[Dict]:
    """
    Read the last `limit_docs` mover snapshots, return raw rows
    with both raw timestamp and ticker so we can aggregate per team.
    """
    docs = (
        db.collection("movers")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit_docs)
        .stream()
    )

    rows: List[Dict] = []
    for d in docs:
        blob = d.to_dict() or {}
        ts = blob.get("timestamp")
        if not ts:
            continue

        for item in blob.get("items", []):
            rows.append(
                {
                    "timestamp_raw": ts,
                    "ticker": item.get("ticker"),
                    "team": team_from_ticker(item.get("ticker")),
                    "old": item.get("old"),
                    "new": item.get("new"),
                    "change": item.get("change"),
                }
            )
    return rows


def compute_net_changes_by_ticker(movers_df: pd.DataFrame, hours: int) -> Dict[str, float]:
    """
    From the movers dataframe, compute net change in price for each ticker
    over the last `hours` hours.
    """
    if movers_df.empty:
        return {}

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now_utc - datetime.timedelta(hours=hours)

    movers_df = movers_df.copy()
    movers_df["ts_dt"] = movers_df["timestamp_raw"].apply(parse_ts_utc)
    recent = movers_df[movers_df["ts_dt"].notna()]
    recent = recent[recent["ts_dt"] >= cutoff]

    if recent.empty:
        return {}

    net = recent.groupby("ticker")["change"].sum().to_dict()
    return net


# -------------------------
# LOAD CURRENT MARKETS
# -------------------------
payload = load_current_markets()
if not payload:
    st.error("No data found in Firestore at cfp_markets/current.")
    st.stop()

markets = payload.get("markets", [])
df = pd.DataFrame(markets)

if df.empty:
    st.error("Markets array is empty in Firestore.")
    st.stop()

df = df.sort_values("probability", ascending=False, na_position="last")

# -------------------------
# LOAD MOVERS + NET CHANGES
# -------------------------
mover_rows = load_recent_movers_raw()
movers_df = pd.DataFrame(mover_rows)
net_changes = compute_net_changes_by_ticker(movers_df, WINDOW_HOURS)

# -------------------------
# HEADER
# -------------------------
st.title("🏈 CFP Playoff Odds — Live Ticker")
st.caption(f"Live probabilities from Kalshi (net change over last {WINDOW_HOURS}h).")

# -------------------------
# BUILD TICKER DATA (USING NET CHANGES)
# -------------------------
ticker_rows: List[Dict] = []

for _, r in df.iterrows():
    ticker = r.get("ticker")
    team = team_from_ticker(ticker)
    prob = r.get("probability")

    delta_pts = net_changes.get(ticker, 0.0)

    if delta_pts > 0:
        direction = "up"
    elif delta_pts < 0:
        direction = "down"
    else:
        direction = "flat"

    ticker_rows.append(
        {
            "team": team,
            "prob_text": prob_text(prob),
            "delta_pts": delta_pts,
            "delta_text": delta_points_text(delta_pts),
            "direction": direction,
        }
    )

# -------------------------
# TICKER HTML
# -------------------------
ticker_items_html = ""
for item in ticker_rows:
    arrow = "▲" if item["direction"] == "up" else "▼" if item["direction"] == "down" else ""
    cls = {"up": "delta-up", "down": "delta-down", "flat": "delta-flat"}[item["direction"]]

    # Example: "TEX 45.0%  ▲ +8"
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
  box-shadow:0 10px 24px rgba(15,23,42,0.9);
}}
.ticker-track {{
  display:inline-flex; white-space:nowrap;
  animation:scroll 50s linear infinite;
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
.ticker-team {{
  color:#fff; font-weight:600; text-transform:uppercase;
  letter-spacing:0.07em;
}}
.ticker-prob {{
  color:#a5b4fc; font-variant-numeric:tabular-nums;
}}
.ticker-delta {{
  font-size:0.8rem; font-variant-numeric:tabular-nums;
}}
.delta-up   {{ color:#22c55e; }}
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
# 🔥 RECENT MOVERS (TABLE)
# ============================================================
st.markdown("### 🔥 Recent Movers")

if movers_df.empty:
    st.info("No movers recorded yet.")
else:
    movers_df = movers_df.copy()
    movers_df["time"] = movers_df["timestamp_raw"].apply(pretty_time)
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
