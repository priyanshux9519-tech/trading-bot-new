import os
import requests
import yfinance as yf
import pandas as pd
from flask import Flask, jsonify, request
import upstox_client
from upstox_client.rest import ApiException

app = Flask(__name__)

# Environment Variables
UPSTOX_API_KEY = os.environ.get("UPSTOX_API_KEY")
UPSTOX_API_SECRET = os.environ.get("UPSTOX_API_SECRET")
UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN")  # Generated after daily auth
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TRADE_MODE = os.environ.get("TRADE_MODE", "PAPER").upper()

# Strategy Parameters
CAPITAL = 500.0
MAX_RISK = 60.0    # Stop Loss Limit (₹)
TARGET_PROFIT = 150.0 # Target Profit (₹)
STOCK_SYMBOL = "RELIANCE.NS"
UPSTOX_INSTRUMENT_KEY = "NSE_EQ|INE002A01018" # Reliance EQ Instrument Key

def send_telegram(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Telegram Error: {e}")

def place_upstox_order(transaction_type, quantity, price):
    if TRADE_MODE != "REAL":
        print("Paper Trading Mode: Order Execution Skipped")
        return {"status": "PAPER_SUCCESS"}
    
    if not UPSTOX_ACCESS_TOKEN:
        print("Upstox Access Token missing!")
        send_telegram("⚠️ *Upstox Alert*: Access Token is missing for real trading.")
        return {"status": "TOKEN_MISSING"}

    # Configure Upstox API
    configuration = upstox_client.Configuration()
    configuration.access_token = UPSTOX_ACCESS_TOKEN
    api_instance = upstox_client.OrderApi(upstox_client.ApiClient(configuration))

    order_body = upstox_client.PlaceOrderRequest(
        quantity=int(quantity),
        product="I",              # Intraday
        validity="DAY",
        price=0.0,                # Market Order
        tag="nexus_bot",
        instrument_token=UPSTOX_INSTRUMENT_KEY,
        order_type="MARKET",
        transaction_type=transaction_type.upper(), # BUY / SELL
        disclosed_quantity=0,
        trigger_price=0.0,
        is_amo=False
    )

    try:
        api_response = api_instance.place_order(order_body, api_version='2.0')
        print(f"Upstox Order Executed: {api_response}")
        return {"status": "SUCCESS", "response": api_response}
    except ApiException as e:
        print(f"Upstox Order Exception: {e}")
        send_telegram(f"❌ *Upstox Order Failed*: {e.reason}")
        return {"status": "FAILED", "error": str(e)}

def analyze_and_trade():
    print("Fetching live market data for Reliance...")
    data = yf.download(tickers=STOCK_SYMBOL, period="1d", interval="5m")
    
    if data.empty:
        print("No market data returned.")
        return "No Data"

    close_price = float(data['Close'].iloc[-1])
    
    # Risk Management Calculation
    # Max Loss ₹60, Max Profit ₹150 for ₹500 allocation
    sl_price = round(close_price - (MAX_RISK / 2), 2)  # 2 shares approx
    target_price = round(close_price + (TARGET_PROFIT / 2), 2)
    qty = max(1, int(CAPITAL // close_price)) if CAPITAL >= close_price else 1

    msg = (
        f"📊 *Reliance Intraday Signal*\n"
        f"• *Current Price*: ₹{close_price}\n"
        f"• *Quantity*: {qty}\n"
        f"• *StopLoss*: ₹{sl_price} (-₹{MAX_RISK})\n"
        f"• *Target*: ₹{target_price} (+₹{TARGET_PROFIT})\n"
        f"• *Mode*: `{TRADE_MODE}`"
    )
    print(msg)
    send_telegram(msg)

    # Trigger Order Execution
    res = place_upstox_order("BUY", qty, close_price)
    return res

@app.route('/', methods=['GET', 'HEAD'])
def home():
    res = analyze_and_trade()
    return f"Nexus Trading Bot Live! Mode: {TRADE_MODE} | Upstox Active | Status: {res}"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)