import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timezone

BASE = "https://data-api.binance.vision"
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
         "DOGEUSDT", "ADAUSDT", "TRXUSDT", "LINKUSDT", "AVAXUSDT"]
TIMEFRAMES = ["30m", "1h", "2h", "3h", "4h"]

st.set_page_config(page_title="BLUE WHALE PRO", page_icon="🐋", layout="wide")
st.title("🐋 BLUE WHALE PRO")
st.caption("Crypto Spot Scanner • Binance public market data • Paper signals only")

st.info("Free public data mode. No API key, Dhan login, or exchange trading permission is required. "
        "This dashboard only reads market data and does not place orders.")

@st.cache_data(ttl=45)
def get_klines(symbol, interval, limit=250):
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

def scan_symbol(symbol, tf):
    # Native Binance intervals: 30m, 1h, 2h, 3h, 4h, 1d, 1w.
    base = get_klines(symbol, tf, 250)
    h4 = get_klines(symbol, "4h", 250)
    day = get_klines(symbol, "1d", 250)
    week = get_klines(symbol, "1w", 250)
    for name, higher in [("poc4h", h4), ("pocD", day), ("pocW", week)]:
        p = poc_series(higher).rename(columns={"poc": name}).sort_values("open_time")
        base = pd.merge_asof(base.sort_values("open_time"), p,
                             on="open_time", direction="backward")
    base = base.dropna(subset=["poc4h","pocD","pocW"]).reset_index(drop=True)
    if len(base) < 5:
        return None
    # Exclude currently forming candle; evaluate only closed candles.
    i = len(base) - 2
    row = base.iloc[i]
    prev = base.iloc[i-1]
    levels = [row.poc4h, row.pocD, row.pocW]
    prev_levels = [prev.poc4h, prev.pocD, prev.pocW]
    closes = base["close"]
    # A cross above/below each POC must have occurred within last 3 closed candles.
    def crossed(level_col, direction):
        s = base[level_col]
        start = max(1, i-2)
        for j in range(start, i+1):
            if direction == "BUY" and closes.iloc[j-1] <= s.iloc[j-1] and closes.iloc[j] > s.iloc[j]:
                return True
            if direction == "SELL" and closes.iloc[j-1] >= s.iloc[j-1] and closes.iloc[j] < s.iloc[j]:
                return True
        return False
    buy = all(crossed(c, "BUY") for c in ["poc4h","pocD","pocW"]) and all(row.close > x for x in levels)
    sell = all(crossed(c, "SELL") for c in ["poc4h","pocD","pocW"]) and all(row.close < x for x in levels)
    signal = "BUY" if buy else ("SELL" if sell else "—")
    sl_window = base.iloc[max(0, i-2):i+1]
    sl = float(sl_window.low.min()) if signal == "BUY" else (
        float(sl_window.high.max()) if signal == "SELL" else None)
    return {
        "Coin": symbol, "Timeframe": tf.upper(), "Signal": signal,
        "Last Price": float(row.close), "Entry": float(row.close) if signal != "—" else None,
        "Stop Loss": sl, "4H POC": float(row.poc4h),
        "Daily POC": float(row.pocD), "Weekly POC": float(row.pocW),
        "Candle Close (UTC)": str(row.close_time)
    }

with st.sidebar:
    st.header("Scanner settings")
    selected = st.multiselect("Coins", COINS, default=COINS)
    selected_tf = st.multiselect("Timeframes", TIMEFRAMES, default=TIMEFRAMES)
    run = st.button("Run scanner", type="primary")
    st.caption("Data refresh cache: 45 seconds")

if run:
    results = []
    errors = []
    progress = st.progress(0)
    tasks = [(s,t) for s in selected for t in selected_tf]
    for n, (symbol, tf) in enumerate(tasks, start=1):
        try:
            item = scan_symbol(symbol, tf)
            if item:
                results.append(item)
        except Exception as e:
            errors.append(f"{symbol} {tf}: {e}")
        progress.progress(n / max(1, len(tasks)))
    st.session_state["results"] = results
    st.session_state["errors"] = errors

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state["results"])
    if not df.empty:
        buys = int((df.Signal == "BUY").sum())
        sells = int((df.Signal == "SELL").sum())
        a,b,c = st.columns(3)
        a.metric("BUY signals", buys)
        b.metric("SELL signals", sells)
        c.metric("Pairs scanned", df.Coin.nunique())
        st.subheader("Scanner results")
        st.dataframe(df, use_container_width=True, hide_index=True)
        signals = df[df.Signal != "—"]
        st.subheader("Active signals")
        if signals.empty:
            st.write("No fresh BUY/SELL signal on the selected closed candles.")
        else:
            st.dataframe(signals, use_container_width=True, hide_index=True)
    else:
        st.warning("No results returned. Check connection or try again.")
    if st.session_state.get("errors"):
        with st.expander("Data/API errors"):
            for e in st.session_state["errors"]:
                st.write(e)
else:
    st.markdown("### Ready to scan")
    st.write("Choose coins and timeframes from the left, then press **Run scanner**.")
    st.write("Default watchlist: BTC, ETH, BNB, XRP, SOL, DOGE, ADA, TRX, LINK and AVAX (USDT spot pairs).")

st.caption("Important: This is a technical signal scanner for research/paper testing, not financial advice. "
           "It does not track actual whale wallets or true volume-profile POC. Signals are not guaranteed.")
