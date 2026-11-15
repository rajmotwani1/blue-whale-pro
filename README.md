# BLUE WHALE PRO

See **START_HERE.txt** for the full Windows setup and Streamlit Cloud deployment steps.

Install dependencies with `pip install -r requirements.txt` and run with `streamlit run app.py`.

Configure `APP_PASSWORD` in Streamlit Secrets (or locally in `.streamlit/secrets.toml`). Never commit secrets to GitHub.


Bybit API configuration: the app calls only https://api.bybit.com/v5/market/kline. It does not automatically switch to api.bytick.com.
