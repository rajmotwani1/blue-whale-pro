import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone

BASE = "https://data-api.binance.vision"
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
         "DOGEUSDT", "ADAUSDT", "TRXUSDT", "LINKUSDT", "AVAXUSDT"]
TIMEFRAMES = ["30m", "1h", "2h", "3h", "4h", "1d"]

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
st.caption("Crypto Scanner • Binance Spot + Bybit Spot + Bybit Perpetual • Paper signals only")

st.info("Free public market-data mode. No API key or exchange trading permission is required. "
        "This dashboard only reads market data and does not place orders.")

@st.cache_data(ttl=45)
def get_klines(symbol, interval, market_type, limit=250):
    """Return candles in the common OHLCV schema used by the unchanged signal logic."""
    if market_type == "Binance Spot":
        # Existing Binance Spot source is preserved.
        url = f"{BASE}/api/v3/klines"
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
        return df[["open_time","open","high","low","close","volume","close_time"]]

    # Bybit V5 public candles: category=spot for Bybit Spot, linear for Perpetual.
    # Bybit has no native 3-hour candle, so 3h is aggregated from 1h candles.
    category = "spot" if market_type == "Bybit Spot" else "linear"
    bybit_intervals = {"30m": "30", "1h": "60", "2h": "120", "3h": "60",
                       "4h": "240", "1d": "D", "1w": "W"}
    if interval not in bybit_intervals:
        raise ValueError(f"Unsupported Bybit interval: {interval}")
    api_interval = bybit_intervals[interval]
    fetch_limit = min(1000, limit * 3 if interval == "3h" else limit)
    # Try Bybit's primary public endpoint, then its official alternate endpoint.
    # This helps when one hostname is blocked or unavailable from the hosting server.
    params = {"category": category, "symbol": symbol,
              "interval": api_interval, "limit": fetch_limit}
    payload = None
    request_errors = []
    for host in ("https://api.bybit.com", "https://api.bytick.com"):
        try:
            response = requests.get(
                f"{host}/v5/market/kline",
                params=params,
                headers={"User-Agent": "Mozilla/5.0 BLUE-WHALE-PRO"},
                timeout=20
            )
            if response.status_code == 403:
                request_errors.append(f"{host}: HTTP 403 Forbidden")
                continue
            response.raise_for_status()
            payload = response.json()
            break
        except requests.RequestException as exc:
            request_errors.append(f"{host}: {exc}")

    if payload is None:
        raise RuntimeError(
            "Bybit public API is refusing requests from this app server. "
            "Both official Bybit endpoints failed. " + " | ".join(request_errors)
        )
    if payload.get("retCode") != 0:
        raise RuntimeError(f"Bybit API: {payload.get('retMsg', 'Unknown API error')}")
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        raise RuntimeError("Bybit returned no candle data for this symbol/interval.")
    df = pd.DataFrame(rows, columns=["open_time_ms","open","high","low","close","volume","turnover"])
    df["open_time"] = pd.to_datetime(pd.to_numeric(df["open_time_ms"]), unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("open_time").reset_index(drop=True)  # Bybit returns newest first.

    if interval == "3h":
        df = df.set_index("open_time")
        df = df.resample("3h", origin="start_day", label="left", closed="left").agg(
            {"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum"}
        ).dropna(subset=["open","high","low","close"]).reset_index()
        df = df.tail(limit).reset_index(drop=True)
        candle_delta = pd.Timedelta(hours=3)
    elif interval.endswith("m"):
        candle_delta = pd.Timedelta(minutes=int(interval[:-1]))
    elif interval.endswith("h"):
        candle_delta = pd.Timedelta(hours=int(interval[:-1]))
    elif interval == "1d":
        candle_delta = pd.Timedelta(days=1)
    elif interval == "1w":
        candle_delta = pd.Timedelta(weeks=1)
    else:
        raise ValueError(f"Unsupported interval: {interval}")

    df["close_time"] = df["open_time"] + candle_delta - pd.Timedelta(milliseconds=1)
    return df[["open_time","open","high","low","close","volume","close_time"]]

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
    display_symbol = f"{symbol}.P" if market_type == "Bybit Perpetual" else symbol

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
        candle_time = row.close_time
        history.append({
            "Market": market_type,
            "Coin": display_symbol,
            "Timeframe": tf.upper(),
            "Alert": signal,
            "Entry Price": float(row.close),
            "4H POC": float(row.poc4h),
            "Daily POC": float(row.pocD),
            "Weekly POC": float(row.pocW),
            "Alert Time (UTC)": str(candle_time),
            "Alert Time (IST)": candle_time.tz_convert("Asia/Kolkata").strftime("%d-%b-%Y %I:%M:%S %p")
        })

    latest = history[-1] if history else None
    return {"latest": latest, "history": history}

with st.sidebar:
    st.header("Scanner settings")
    market_type = st.selectbox("Market", ["Binance Spot", "Bybit Spot", "Bybit Perpetual"], index=0)
    selected = st.multiselect("Coins", COINS, default=COINS)
    selected_tf = st.multiselect("Timeframes", TIMEFRAMES, default=TIMEFRAMES)
    run = st.button("Run scanner", type="primary")
    st.caption("Data refresh cache: 45 seconds • Binance Spot | Bybit Spot | Bybit Perpetual")

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
        buys = int((df.Alert == "BUY").sum())
        sells = int((df.Alert == "SELL").sum())
        a,b,c = st.columns(3)
        a.metric("Latest BUY signals", buys)
        b.metric("Latest SELL signals", sells)
        c.metric("Pairs with alerts", df.Coin.nunique())
        st.subheader("Latest BUY / SELL Alerts")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No current/latest BUY or SELL alert found for the selected closed candles.")

    hist = pd.DataFrame(st.session_state.get("historical_alerts", []))
    st.subheader("🐋 Blue Whale BUY / SELL Alerts")
    st.caption("Entry Price aur 4H, Daily, Weekly POC ke saath alert date/time. Stop Loss aur targets nahi dikhaye jaate.")
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
    st.write("Choose Binance Spot, Bybit Spot or Bybit Perpetual, then select coins and timeframes.")
    st.write("Default watchlist: BTC, ETH, BNB, XRP, SOL, DOGE, ADA, TRX, LINK and AVAX (USDT pairs).")
    st.caption("Alert details: Entry Price, 4H POC, Daily POC, Weekly POC aur exact alert date/time. Stop Loss aur targets nahi hain. Bybit Perpetual symbols .P ke saath dikhte hain; Daily (1D) bhi available hai.")

st.caption("Past-alert history checks up to 250 fetched candles per selected timeframe (the calendar lookback varies by timeframe). " \
           "Binance Spot uses Binance Spot candles; Bybit Spot uses Bybit spot candles; Bybit Perpetual uses Bybit USDT linear perpetual candles. "
           "3h Bybit candles are aggregated from Bybit 1h candles; the signal formula and conditions are unchanged. "
           "The .P suffix is a display label, not part of the exchange API symbol. "
           "Important: This is a technical signal scanner for research/paper testing, not financial advice. "
           "It does not track actual whale wallets or true volume-profile POC. Signals are not guaranteed.")
