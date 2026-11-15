import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone

BASE = "https://data-api.binance.vision"
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
         "DOGEUSDT", "ADAUSDT", "TRXUSDT", "LINKUSDT", "AVAXUSDT"]
TIMEFRAMES = ["30m", "1h", "2h", "3h", "4h"]

st.set_page_config(page_title="BLUE WHALE PRO", page_icon="🐋", layout="wide")

# Shared-password gate. Set APP_PASSWORD in Streamlit Cloud > App settings > Secrets.
import hmac

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🐋 BLUE WHALE PRO — Login")
    try:
        expected_password = st.secrets["APP_PASSWORD"]
    except Exception:
        st.error("Login password is not configured. Add APP_PASSWORD in Streamlit app Secrets.")
        st.stop()

    with st.form("login_form"):
        entered_password = st.text_input("Enter password", type="password")
        submitted = st.form_submit_button("Login", type="primary")
    if submitted:
        if hmac.compare_digest(entered_password, str(expected_password)):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password. Please try again.")
    st.stop()

with st.sidebar:
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.rerun()

st.title("🐋 BLUE WHALE PRO")
st.caption("Crypto Spot + USDⓈ-M Perpetual Scanner • Binance public market data • Paper signals only")

st.info("Free public market-data mode. No API key or exchange trading permission is required. "
        "This dashboard only reads market data and does not place orders.")

@st.cache_data(ttl=45)
def get_klines(symbol, interval, market_type, limit=250):
    # Spot and USDⓈ-M perpetual use separate public Binance market-data endpoints.
    # Perpetual symbols are shown with TradingView-style .P in the UI, but
    # Binance API requests use the native symbol (for example BTCUSDT).
    if market_type == "Spot":
        url = f"{BASE}/api/v3/klines"
    else:
        url = "https://fapi.binance.com/fapi/v1/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=15)
    r.raise_for_status()
    rows = r.json()
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df

def poc_series(htf):
    # User's original custom POC formula:
    # (previous HTF high + previous HTF low + previous HTF close + current HTF open) / 4
    x = htf.copy()
    x["poc"] = (x["high"].shift(1) + x["low"].shift(1) +
                x["close"].shift(1) + x["open"]) / 4
    return x[["open_time", "poc"]].dropna()

def attach_poc(base, htf):
    p = poc_series(htf).sort_values("open_time")
    return pd.merge_asof(base.sort_values("open_time"), p,
                         on="open_time", direction="backward")

def scan_symbol(symbol, tf, market_type):
    # Native Binance intervals: 30m, 1h, 2h, 3h, 4h, 1d, 1w.
    # Historical alerts are evaluated on the same closed candles and with the
    # same POC formula / three-candle crossing window as the live scan.
    base = get_klines(symbol, tf, market_type, 250)
    h4 = get_klines(symbol, "4h", market_type, 250)
    day = get_klines(symbol, "1d", market_type, 250)
    week = get_klines(symbol, "1w", market_type, 250)
    for name, higher in [("poc4h", h4), ("pocD", day), ("pocW", week)]:
        p = poc_series(higher).rename(columns={"poc": name}).sort_values("open_time")
        base = pd.merge_asof(base.sort_values("open_time"), p,
                             on="open_time", direction="backward")
    base = base.dropna(subset=["poc4h","pocD","pocW"]).reset_index(drop=True)
    if len(base) < 5:
        return {"latest": None, "history": []}

    closes = base["close"]
    display_symbol = symbol if market_type == "Spot" else f"{symbol}.P"

    def crossed(level_col, direction, i):
        s = base[level_col]
        start = max(1, i-2)
        for j in range(start, i+1):
            if direction == "BUY" and closes.iloc[j-1] <= s.iloc[j-1] and closes.iloc[j] > s.iloc[j]:
                return True
            if direction == "SELL" and closes.iloc[j-1] >= s.iloc[j-1] and closes.iloc[j] < s.iloc[j]:
                return True
        return False

    history = []
    # Exclude the currently forming candle. Evaluate every available closed candle.
    for i in range(3, len(base)-1):
        row = base.iloc[i]
        levels = [row.poc4h, row.pocD, row.pocW]
        buy = all(crossed(c, "BUY", i) for c in ["poc4h","pocD","pocW"]) and all(row.close > x for x in levels)
        sell = all(crossed(c, "SELL", i) for c in ["poc4h","pocD","pocW"]) and all(row.close < x for x in levels)
        if not (buy or sell):
            continue

        signal = "BUY" if buy else "SELL"
        sl_window = base.iloc[max(0, i-2):i+1]
        sl = float(sl_window.low.min()) if signal == "BUY" else float(sl_window.high.max())
        candle_time = row.close_time
        history.append({
            "Market": market_type,
            "Coin": display_symbol,
            "Timeframe": tf.upper(),
            "Signal": signal,
            "Entry": float(row.close),
            "Stop Loss": sl,
            "4H POC": float(row.poc4h),
            "Daily POC": float(row.pocD),
            "Weekly POC": float(row.pocW),
            "Alert Time (UTC)": str(candle_time),
            "Alert Time (IST)": candle_time.tz_convert("Asia/Kolkata").strftime("%d-%b-%Y %I:%M %p")
        })

    latest = history[-1] if history else None
    if latest:
        latest = dict(latest)
        latest["Last Price"] = latest["Entry"]
    return {"latest": latest, "history": history}

with st.sidebar:
    st.header("Scanner settings")
    market_type = st.selectbox("Market", ["Spot", "USDⓈ-M Perpetual"], index=0)
    selected = st.multiselect("Coins", COINS, default=COINS)
    selected_tf = st.multiselect("Timeframes", TIMEFRAMES, default=TIMEFRAMES)
    run = st.button("Run scanner", type="primary")
    st.caption("Data refresh cache: 45 seconds")

if run:
    results = []
    historical_alerts = []
    errors = []
    progress = st.progress(0)
    tasks = [(s,t) for s in selected for t in selected_tf]
    for n, (symbol, tf) in enumerate(tasks, start=1):
        try:
            scanned = scan_symbol(symbol, tf, market_type)
            if scanned.get("latest"):
                results.append(scanned["latest"])
            historical_alerts.extend(scanned.get("history", []))
        except Exception as e:
            errors.append(f"{symbol} {tf}: {e}")
        progress.progress(n / max(1, len(tasks)))
    st.session_state["results"] = results
    st.session_state["historical_alerts"] = historical_alerts
    st.session_state["errors"] = errors
    st.session_state["last_market_type"] = market_type

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state["results"])
    if not df.empty:
        buys = int((df.Signal == "BUY").sum())
        sells = int((df.Signal == "SELL").sum())
        a,b,c = st.columns(3)
        a.metric("Latest BUY signals", buys)
        b.metric("Latest SELL signals", sells)
        c.metric("Pairs with alerts", df.Coin.nunique())
        st.subheader("Latest scanner results")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No current/latest BUY or SELL alert found for the selected closed candles.")

    hist = pd.DataFrame(st.session_state.get("historical_alerts", []))
    st.subheader("🐋 Past Blue Whale Alerts")
    st.caption("Shows every qualifying closed candle found in the downloaded candle history for the selected market, coins and timeframes.")
    if not hist.empty:
        hist = hist.sort_values("Alert Time (UTC)", ascending=False)
        st.metric("Past alerts found", len(hist))
        st.dataframe(hist, use_container_width=True, hide_index=True)
        csv = hist.to_csv(index=False).encode("utf-8")
        st.download_button("Download past alerts CSV", data=csv,
                           file_name="blue_whale_past_alerts.csv", mime="text/csv")
    else:
        st.info("Selected market/coins/timeframes mein downloaded history ke andar koi past alert nahi mila.")

    if st.session_state.get("errors"):
        with st.expander("Data/API errors"):
            for e in st.session_state["errors"]:
                st.write(e)
else:
    st.markdown("### Ready to scan")
    st.write("Choose coins and timeframes from the left, then press **Run scanner**.")
    st.write("Choose Spot or USDⓈ-M Perpetual, then select coins and timeframes.")
    st.write("Default watchlist: BTC, ETH, BNB, XRP, SOL, DOGE, ADA, TRX, LINK and AVAX (USDT pairs).")
    st.caption("Perpetual symbols are displayed with .P (for example BTCUSDT.P); Binance API requests use BTCUSDT.")

st.caption("Past-alert history checks up to 250 fetched candles per selected timeframe (the calendar lookback varies by timeframe). " \
           "Spot uses Binance Spot candles. USDⓈ-M Perpetual uses Binance Futures candles. "
           "The .P suffix is a display label, not part of the Binance API symbol. "
           "Important: This is a technical signal scanner for research/paper testing, not financial advice. "
           "It does not track actual whale wallets or true volume-profile POC. Signals are not guaranteed.")
