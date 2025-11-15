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

# Kalshi elections API base
BASE_URL = os.getenv(
    "KALSHI_BASE_URL",
    "https://api.elections.kalshi.com/trade-api/v2",
)

# College Football Playoff Qualifiers event/series
CFP_EVENT_TICKER = "KXNCAAFPLAYOFF-25"  # 2025–26 CFP Qualifiers


# ===========================
#  LOW-LEVEL: SIGNING
# ===========================

@st.cache_resource
def _load_private_key():
    if not PRIVATE_KEY_PEM:
        raise RuntimeError("KALSHI_PRIVATE_KEY_PEM is not set in the environment.")
    return serialization.load_pem_private_key(
        PRIVATE_KEY_PEM.encode("utf-8"),
        password=None,
    )


def _sign_message(private_key, message: str) -> str:
    """
    Sign `message` using RSA-PSS SHA256 and return base64 signature.
    """
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

    `path` should start with '/' and may include query params, e.g.:
      "/markets?event_ticker=KXNCAAFPLAYOFF-25&limit=1000"
    """
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not set in the environment.")

    private_key = _load_private_key()

    # Build full URL
    url = urljoin(BASE_URL, path.lstrip("/"))

    # Per Kalshi docs, sign: timestamp + METHOD + PATH (no query)
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
    """
    GET + JSON helper with basic error handling.
    Returns dict or raises an Exception for non-200.
    """
    resp = kalshi_signed_get(path)
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Non-JSON response ({resp.status_code}): {resp.text[:300]}")

    if resp.status_code != 200:
        raise RuntimeError(
            f"Kalshi error {resp.status_code} for {path}:\n"
            + json.dumps(data, indent=2)
        )

    return data


# ===========================
#  MARKET HELPERS
# ===========================

def fetch_cfp_markets():
    """
    Fetch all markets (teams) in the CFP playoff qualifiers event.
    Returns a list of market dicts, sorted by YES price desc.
    """
    path = f"/markets?event_ticker={CFP_EVENT_TICKER}&limit=1000"
    data = kalshi_get_json(path)
    markets = data.get("markets", [])

    def sort_key(m):
        yp = m.get("yes_price")
        if yp is None:
            yp = -1
        return (-yp, m.get("title", m.get("ticker", "")))

    markets.sort(key=sort_key)
    return markets


def summarize_cfp_markets(markets):
    """
    Convert raw market list into a simple list of dicts with
    team, ticker, yes, no, volume.
    """
    rows = []
    for m in markets:
        rows.append(
            {
                "Team": m.get("title", m.get("ticker", "")),
                "Ticker": m.get("ticker", ""),
                "YES": m.get("yes_price"),
                "NO": m.get("no_price"),
                "Volume": m.get("volume"),
            }
        )
    return rows


def compute_movers(current_rows, prev_prices, min_move_points):
    """
    Given current rows and previous price dict, compute movers:
      [{"Team", "Ticker", "Prev", "Now", "Diff"}, ...]
    """
    movers = []
    new_prices = {}

    for row in current_rows:
        ticker = row["Ticker"]
        price = row["YES"]
        if price is None:
            continue
        new_prices[ticker] = price
        prev = prev_prices.get(ticker)
        if prev is None:
            continue
        diff = price - prev
        if abs(diff) >= min_move_points:
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
#  STREAMLIT APP
# ===========================

st.set_page_config(
    page_title="CFP Playoff Odds Tracker (Kalshi)",
    page_icon="🏈",
    layout="wide",
)

st.title("🏈 CFP Playoff Odds Tracker (Kalshi API)")
st.caption(
    "Read-only Streamlit app tracking the **College Football Playoff Qualifiers** market "
    f"(`{CFP_EVENT_TICKER}`) on Kalshi."
)

# Sidebar controls
st.sidebar.header("Settings")
poll_interval = st.sidebar.slider(
    "Refresh interval (seconds)",
    min_value=5,
    max_value=60,
    value=15,
    step=5,
    help="How often you want to manually refresh the data.",
)
min_move = st.sidebar.slider(
    "Minimum move to count as 'mover' (points)",
    min_value=1,
    max_value=10,
    value=2,
    step=1,
)

st.sidebar.info(
    "To update prices, click **Refresh now**. "
    "You can also enable auto-refresh on your hosting platform if desired."
)

# Session state for prev prices & ticker tape log
if "cfp_prev_prices" not in st.session_state:
    st.session_state.cfp_prev_prices = {}
if "cfp_tape" not in st.session_state:
    st.session_state.cfp_tape = []  # list of strings (ticker tape lines)
if "last_refresh_ts" not in st.session_state:
    st.session_state.last_refresh_ts = None


# ========== DATA FETCH ==========

def refresh_data():
    """
    Fetch markets, build rows + movers.
    Always returns (rows, movers), even on error.
    """
    try:
        markets = fetch_cfp_markets()
    except Exception as e:
        st.error(f"Error fetching CFP markets: {e}")
        # On error, return safe empty values so unpacking works
        return [], []

    rows = summarize_cfp_markets(markets)

    movers, new_prices = compute_movers(
        rows,
        st.session_state.cfp_prev_prices,
        min_move_points=min_move,
    )

    # Update session state
    st.session_state.cfp_prev_prices = new_prices
    st.session_state.last_refresh_ts = time.time()

    # Update ticker tape log
    now_str = datetime.utcnow().strftime("%H:%M:%S")
    for m in movers:
        direction = "UP" if m["Diff"] > 0 else "DOWN"
        sign = "+" if m["Diff"] > 0 else ""
        line = (
            f"[{now_str}] {m['Ticker']}  {m['Team']}  "
            f"{m['Prev']} → {m['Now']}  ({sign}{m['Diff']}) {direction}"
        )
        st.session_state.cfp_tape.append(line)

    return rows, movers


# Trigger refresh if user hits button
col_refresh, col_ts = st.columns([1, 3])
with col_refresh:
    if st.button("🔄 Refresh now"):
        rows, movers = refresh_data()
    else:
        # On first load, fetch once
        if st.session_state.last_refresh_ts is None:
            rows, movers = refresh_data()
        else:
            # Use whatever we had on last refresh
            rows = None
            movers = None
with col_ts:
    if st.session_state.last_refresh_ts is not None:
        dt = datetime.utcfromtimestamp(st.session_state.last_refresh_ts)
        st.write(f"Last refresh: **{dt.strftime('%Y-%m-%d %H:%M:%S UTC')}**")
    else:
        st.write("Last refresh: _pending_")

# If we just refreshed, we got rows/movers.
# If not, recompute rows from current markets for display only.
if rows is None:
    try:
        markets = fetch_cfp_markets()
        rows = summarize_cfp_markets(markets)
        # No movers recomputed here; ticker tape only updates on explicit refresh
    except Exception as e:
        st.error(f"Error fetching CFP markets: {e}")
        rows = []


# ========== MAIN LAYOUT ==========

tab_table, tab_movers, tab_tape = st.tabs(
    ["📋 Full Market Table", "📈 Movers (since last refresh)", "📜 Ticker Tape Log"]
)

# ---- Tab 1: full table ----
with tab_table:
    st.subheader("All CFP Playoff Qualifier Markets")
    st.caption(
        f"Event: `{CFP_EVENT_TICKER}` • "
        f"Source: Kalshi `/markets?event_ticker={CFP_EVENT_TICKER}`"
    )
    if rows:
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.write("No markets found.")

# ---- Tab 2: movers ----
with tab_movers:
    st.subheader(f"Movers ≥ {min_move} pts since last refresh")
    if movers is None:
        st.info("Hit **Refresh now** to compute movers.")
    else:
        if movers:
            import pandas as pd

            df = pd.DataFrame(movers)

            def color_diff(val):
                if val > 0:
                    return "color: green;"
                if val < 0:
                    return "color: red;"
                return ""

            st.dataframe(
                df.style.applymap(color_diff, subset=["Diff"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.write("No movers above the threshold on the last refresh.")

# ---- Tab 3: ticker tape log ----
with tab_tape:
    st.subheader("Ticker Tape (movement log)")
    st.caption(
        "Each line is a price move of at least the selected threshold between refreshes."
    )
    if st.session_state.cfp_tape:
        # Show most recent first
        for line in reversed(st.session_state.cfp_tape[-200:]):
            if "UP" in line:
                st.markdown(
                    f"<span style='color:limegreen;'>{line}</span>",
                    unsafe_allow_html=True,
                )
            elif "DOWN" in line:
                st.markdown(
                    f"<span style='color:#ff4b4b;'>{line}</span>",
                    unsafe_allow_html=True,
                )
            else:
                st.write(line)
    else:
        st.write("No moves logged yet. Hit **Refresh now** to start building the tape.")

st.markdown(
    "---\n"
    "_This app is read-only and only uses Kalshi GET endpoints. "
    "It does not place, cancel, or modify any orders._"
)
