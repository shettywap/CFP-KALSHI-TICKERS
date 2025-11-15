import os
import time
import json
import base64
from urllib.parse import urlparse, urljoin
from datetime import datetime

import requests
import streamlit as st
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


# ===========================
#  CONFIG / ENV VARS
# ===========================

# Set these in Streamlit Cloud "Secrets":
#
#   KALSHI_API_KEY_ID       = your Kalshi API key ID
#   KALSHI_PRIVATE_KEY_PEM  = your *multi-line* RSA private key in PEM format
#

API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY_PEM")

# *** CORRECT TRADING API BASE URL ***
BASE_URL = os.getenv(
    "KALSHI_BASE_URL",
    "https://api.kalshi.com/trade-api/v2",   # <-- fixed here
)

# College Football Playoff Qualifiers event ticker
CFP_EVENT_TICKER = "KXNCAAFPLAYOFF-25"


# ===========================
#  SIGNING HELPERS
# ===========================

@st.cache_resource
def _load_private_key():
    if not PRIVATE_KEY_PEM:
        raise RuntimeError("KALSHI_PRIVATE_KEY_PEM is not set.")
    return serialization.load_pem_private_key(
        PRIVATE_KEY_PEM.encode("utf-8"),
        password=None,
    )


def _sign_message(private_key, message: str) -> str:
    """Sign message using RSA-PSS SHA256, return base64 string."""
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def kalshi_signed_get(path: str):
    """
    Send a signed GET request to Kalshi.
    `path` must start with '/'.
    """
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not set.")

    private_key = _load_private_key()

    # Full URL
    url = urljoin(BASE_URL, path.lstrip("/"))

    # Sign: timestamp + METHOD + PATH (INCLUDE leading slash)
    parsed = urlparse(url)
    method = "GET"
    timestamp = str(int(time.time() * 1000))
    message = timestamp + method + parsed.path

    signature = _sign_message(private_key, message)

    headers = {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "Content-Type": "application/json",
    }

    resp = requests.get(url, headers=headers, timeout=10)
    return resp


def kalshi_get_json(path: str):
    """GET JSON or raise descriptive error."""
    resp = kalshi_signed_get(path)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Non-JSON response ({resp.status_code}): {resp.text[:200]}"
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Kalshi returned {resp.status_code} for {path}:\n"
            + json.dumps(data, indent=2)
        )

    return data


# ===========================
#  CFP MARKET HELPERS
# ===========================

def fetch_cfp_markets():
    """Fetch all CFP qualifier markets."""
    data = kalshi_get_json(
        f"/markets?event_ticker={CFP_EVENT_TICKER}&limit=1000"
    )
    markets = data.get("markets", [])

    # Sort by YES price desc
    def sort_key(m):
        yp = m.get("yes_price")
        return -(yp if yp is not None else -1)

    return sorted(markets, key=sort_key)


def summarize_cfp_markets(markets):
    rows = []
    for m in markets:
        rows.append(
            {
                "Team": m.get("title", m.get("ticker")),
                "Ticker": m.get("ticker"),
                "YES": m.get("yes_price"),
                "NO": m.get("no_price"),
                "Volume": m.get("volume"),
            }
        )
    return rows


def compute_movers(rows, prev_prices, threshold):
    movers = []
    new_prices = {}

    for row in rows:
        ticker = row["Ticker"]
        price = row["YES"]
        if price is None:
            continue

        new_prices[ticker] = price

        prev = prev_prices.get(ticker)
        if prev is None:
            continue

        diff = price - prev
        if abs(diff) >= threshold:
            movers.append(
                {
                    "Team": row["Team"],
                    "Ticker": ticker,
                    "Prev": prev,
                    "Now": price,
                    "Diff": diff,
                }
            )

    return movers, new_prices


# ===========================
#  STREAMLIT UI
# ===========================

st.set_page_config(
    page_title="CFP Playoff Odds Tracker",
    page_icon="🏈",
    layout="wide",
)

st.title("🏈 CFP Playoff Odds Tracker (Kalshi API)")
st.caption(
    "Live CFP qualifier odds from Kalshi. Read-only — no orders, no money."
)

# Sidebar
st.sidebar.header("Settings")
min_move = st.sidebar.slider(
    "Minimum movement to log (points)", 1, 20, 2
)

# Session state
if "prev_prices" not in st.session_state:
    st.session_state.prev_prices = {}
if "tape" not in st.session_state:
    st.session_state.tape = []
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = None


# ===========================
#  REFRESH LOGIC
# ===========================

def refresh_data():
    """Always return (rows, movers) even on failure."""
    try:
        markets = fetch_cfp_markets()
    except Exception as e:
        st.error(f"Error fetching markets: {e}")
        return [], []

    rows = summarize_cfp_markets(markets)
    movers, new_prices = compute_movers(rows, st.session_state.prev_prices, min_move)

    # Save new prices
    st.session_state.prev_prices = new_prices
    st.session_state.last_refresh = datetime.utcnow().strftime("%H:%M:%S UTC")

    # Append movers to tape
    now_str = datetime.utcnow().strftime("%H:%M:%S")
    for m in movers:
        sign = "+" if m["Diff"] > 0 else ""
        line = (
            f"[{now_str}] {m['Ticker']} {m['Team']} "
            f"{m['Prev']} → {m['Now']} ({sign}{m['Diff']})"
        )
        st.session_state.tape.append(line)

    return rows, movers


# ===========================
#  REFRESH BUTTON
# ===========================

col1, col2 = st.columns([1, 4])
with col1:
    if st.button("🔄 Refresh Now"):
        rows, movers = refresh_data()
    else:
        if st.session_state.last_refresh is None:
            rows, movers = refresh_data()
        else:
            rows, movers = None, None

with col2:
    if st.session_state.last_refresh:
        st.write(f"Last Refresh: **{st.session_state.last_refresh}**")
    else:
        st.write("Last Refresh: pending")


# If no new refresh, load rows for table
if rows is None:
    try:
        markets = fetch_cfp_markets()
        rows = summarize_cfp_markets(markets)
    except:
        rows = []


# ===========================
#  TABS
# ===========================

tab1, tab2, tab3 = st.tabs([
    "📋 Full Market Table",
    "📈 Movers",
    "📜 Ticker Tape",
])

# ---- Tab 1 ----
with tab1:
    st.subheader("All CFP Qualifier Markets")
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.write("No markets returned.")

# ---- Tab 2 ----
with tab2:
    st.subheader(f"Movers ≥ {min_move} points")
    if movers:
        import pandas as pd
        df = pd.DataFrame(movers)
        st.dataframe(df, use_container_width=True)
    else:
        st.write("No movers since last refresh.")

# ---- Tab 3 ----
with tab3:
    st.subheader("Ticker Tape Log")
    if st.session_state.tape:
        for line in reversed(st.session_state.tape[-200:]):
            color = "limegreen" if "+" in line else "#ff4b4b"
            st.markdown(f"<span style='color:{color}'>{line}</span>", unsafe_allow_html=True)
    else:
        st.write("No movements logged yet.")
