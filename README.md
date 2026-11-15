# BLUE WHALE PRO – Crypto Spot + Perpetual + Past Alerts

Replace `app.py` in the existing Crypto GitHub repository with the included `app.py`.
Keep the existing `requirements.txt` (streamlit, pandas, requests) and keep `APP_PASSWORD` in Streamlit Secrets.
Do not put the password in GitHub.

Features:
- Spot and Binance USDⓈ-M Perpetual market selector
- 10 USDT pairs, 30m / 1h / 2h / 3h / 4h
- Latest scanner results and a separate Past Blue Whale Alerts table
- Past alerts list every qualifying closed candle in the fetched history, with UTC and IST timestamps
- CSV download for past alerts

History limitation:
- The app fetches up to 250 base candles per selected timeframe. Calendar coverage therefore differs by timeframe.
- Historical alerts are recalculated from exchange candles when a scan runs; they are not a permanent database of alerts observed live.
- Existing custom POC formula and signal conditions are retained. This is a research/paper scanner, not order execution.
