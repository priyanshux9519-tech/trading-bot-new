import os
import json
import logging
import time
from datetime import datetime, time as dtime
import requests
from flask import Flask, request
import pandas as pd
import numpy as np
import threading
import pyotp
from SmartApi import SmartConnect

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Secrets & Configs
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOG_FILE = "trade_history.json"
TRADE_MODE = os.getenv("TRADE_MODE", "PAPER")  
SERVER_URL = os.getenv("SERVER_URL", "https://trading-bot-new-oxf5.onrender.com")

# Angel One Credentials
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY")
ANGEL_CLIENT_ID = os.getenv("ANGEL_CLIENT_ID")
ANGEL_PIN = os.getenv("ANGEL_PIN")
ANGEL_TOTP_KEY = os.getenv("ANGEL_TOTP_KEY")

# ₹500 Capital Risk & Money Management Rules
CAPITAL = 500.0
MAX_TRADES_PER_DAY = 2
RISK_PER_TRADE = 60.0    # Max ₹60 loss per trade
REWARD_PER_TRADE = 150.0  # Max ₹150 profit per trade

smartApi = None

def init_angel_one():
    global smartApi
    if ANGEL_API_KEY and ANGEL_CLIENT_ID and ANGEL_PIN and ANGEL_TOTP_KEY:
        try:
            smartApi = SmartConnect(api_key=ANGEL_API_KEY)
            totp = pyotp.TOTP(ANGEL_TOTP_KEY).now()
            data = smartApi.generateSession(ANGEL_CLIENT_ID, ANGEL_PIN, totp)
            if data['status']:
                logging.info("Angel One SmartAPI Login Successful!")
            else:
                logging.error(f"Angel One Login Failed: {data['message']}")
        except Exception as e:
            logging.error(f"Angel One Connection Error: {e}")

# 🔄 24/7 Internal Self-Ping
def keep_alive():
    time.sleep(15)
    while True:
        try:
            res = requests.get(SERVER_URL, timeout=10)
            logging.info(f"Self-Ping Status: {res.status_code}")
        except Exception as e:
            logging.error(f"Self-Ping Error: {e}")
        time.sleep(120)

threading.Thread(target=keep_alive, daemon=True).start()

def send_telegram(message):
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": CHAT_ID, "text": message}, timeout=5)
        except Exception as e:
            logging.error(f"Telegram Error: {e}")

def load_trade_history():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        try:
            return json.load(f).get("trades", [])
        except:
            return []

def save_trade_history(trades):
    with open(LOG_FILE, "w") as f:
        json.dump({"trades": trades}, f, indent=2)

# Dynamic Position Sizing for ₹500 Capital (MIS 5x Margin)
def calculate_quantity(current_price):
    max_buying_power = CAPITAL * 5.0 # ₹2,500 max limit
    qty_by_margin = int(max_buying_power // current_price)
    return max(1, min(qty_by_margin, 2)) # Capped at 1-2 shares to maintain ₹60 max risk

# Execute Angel One Real Order
def execute_angel_order(symbol, signal_type, quantity):
    if TRADE_MODE != "REAL":
        return "PAPER_SUCCESS"
    
    init_angel_one() # Ensure session active
    if not smartApi:
        send_telegram("⚠️ Angel One Connection Failed! Check Credentials.")
        return None

    try:
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": "RELIANCE-EQ",
            "symboltoken": "2885", # Reliance NSE Token
            "transactiontype": "BUY" if signal_type == "BUY" else "SELL",
            "exchange": "NSE",
            "ordertype": "MARKET",
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": "0",
            "quantity": str(quantity)
        }
        order_id = smartApi.placeOrder(orderparams)
        logging.info(f"Angel One Order Executed ID: {order_id}")
        return order_id
    except Exception as e:
        logging.error(f"Angel One Execution Error: {e}")
        send_telegram(f"⚠️ Angel One Order Error: {e}")
        return None

def fetch_market_data(symbol="RELIANCE.NS"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2d&interval=5m"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        df = pd.DataFrame({
            'Open': quote['open'], 'High': quote['high'],
            'Low': quote['low'], 'Close': quote['close'],
            'Volume': quote['volume']
        }, index=pd.to_datetime(np.array(timestamps)*1000000000))
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logging.error(f"Market Data Error: {e}")
        return None

def calculate_indicators(df):
    df = df.copy()
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / np.where(loss == 0, 1, loss)
    df['RSI'] = 100 - (100 / (1 + rs))
    tr = pd.concat([df['High']-df['Low'], np.abs(df['High']-df['Close'].shift()), np.abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    return df

def analyze_and_trade(symbol="RELIANCE.NS"):
    trades = load_trade_history()
    now = datetime.now()
    
    # 🛑 9:30 AM Noise Filter
    if not (dtime(9, 30) <= now.time() <= dtime(15, 15)):
        return

    today_date = now.strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t.get("date") == today_date]

    if len(today_trades) >= MAX_TRADES_PER_DAY:
        return

    df = fetch_market_data(symbol)
    if df is None or len(df) < 35:
        return

    try:
        df = calculate_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(latest['Close'])
        vwap = float(latest['VWAP'])
        macd = float(latest['MACD'])
        macd_signal = float(latest['Signal_Line'])
        rsi = float(latest['RSI']) if not np.isnan(latest['RSI']) else 50.0
        atr = float(latest['ATR']) if not np.isnan(latest['ATR']) else 2.0
        ema20 = float(latest['EMA20'])

        signal = None
        if (close > vwap) and (close > ema20) and (macd > macd_signal) and (rsi > 52):
            signal = "BUY"
        elif (close < vwap) and (close < ema20) and (macd < macd_signal) and (rsi < 48):
            signal = "SELL"

        if signal:
            # 30-Minute Cool-down Gap
            if len(today_trades) > 0:
                last_trade_time = datetime.strptime(today_trades[-1]['time'], "%H:%M:%S").time()
                if (datetime.combine(datetime.today(), now.time()) - datetime.combine(datetime.today(), last_trade_time)).seconds < 1800:
                    return

            qty = calculate_quantity(close)
            stop_loss = close - (1.0 * atr) if signal == "BUY" else close + (1.0 * atr)
            target = close + (2.5 * atr) if signal == "BUY" else close - (2.5 * atr)

            order_status = execute_angel_order(symbol, signal, qty)

            if order_status:
                msg = (f"🚀 [ANGEL ONE {TRADE_MODE} EXECUTION]\n"
                       f"Stock: RELIANCE (NSE)\n"
                       f"Signal: {signal} 🔥 | Qty: {qty}\n"
                       f"Price: ₹{close:.2f}\n"
                       f"SL (Risk ~₹60): ₹{stop_loss:.2f}\n"
                       f"Target (Profit ~₹150): ₹{target:.2f}")
                send_telegram(msg)

                win_loss = 1 if (signal == "BUY" and close > prev['Close']) or (signal == "SELL" and close < prev['Close']) else 0
                pnl_val = REWARD_PER_TRADE if win_loss == 1 else -RISK_PER_TRADE

                trades.append({
                    "date": today_date,
                    "time": time.strftime("%H:%M:%S"),
                    "rsi": round(rsi, 1),
                    "atr": round(atr, 2),
                    "qty": qty,
                    "mode": TRADE_MODE,
                    "win_loss": win_loss,
                    "pnl": round(pnl_val, 2),
                    "price": round(close, 2),
                    "signal": signal
                })
                save_trade_history(trades)

    except Exception as e:
        logging.error(f"Trading Logic Error: {e}")

def market_scheduler():
    time.sleep(10)
    while True:
        try:
            now = datetime.now()
            if dtime(9, 30) <= now.time() <= dtime(15, 15):
                analyze_and_trade()
        except Exception as e:
            logging.error(f"Scheduler Error: {e}")
        time.sleep(300)

threading.Thread(target=market_scheduler, daemon=True).start()

@app.route('/', methods=['GET', 'HEAD'])
def home():
    analyze_and_trade()
    if request.method == 'HEAD':
        return '', 200

    trades = load_trade_history()
    today_date = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for t in trades if t.get('date') == today_date)
    total_trades = len(trades)
    wins = sum(1 for t in trades if t.get('win_loss') == 1)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    total_net_pnl = sum(t.get('pnl', 0) for t in trades)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>NEXUS HIGH-FI TERMINAL</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body {{ background: #080b11; color: #00f2fe; font-family: monospace; padding: 20px; }}
            .card {{ background: #111622; border: 1px solid #1f293d; color: #fff; margin-bottom: 15px; }}
            .badge-real {{ background: #00e676; color: #000; font-weight: bold; }}
            .badge-paper {{ background: #ff9100; color: #000; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h2>⚡ NEXUS TERMINAL (ANGEL ONE SMARTAPI)</h2>
                <span class="badge { 'badge-real' if TRADE_MODE == 'REAL' else 'badge-paper' } p-2">MODE: {TRADE_MODE}</span>
            </div>
            <div class="row">
                <div class="col-md-3"><div class="card p-3"><h5>CAPITAL</h5><h4>₹{CAPITAL}</h4></div></div>
                <div class="col-md-3"><div class="card p-3"><h5>NET PNL</h5><h4 class="{ 'text-success' if total_net_pnl >=0 else 'text-danger'}">₹{total_net_pnl:.2f}</h4></div></div>
                <div class="col-md-3"><div class="card p-3"><h5>WIN RATE</h5><h4>{win_rate:.1f}%</h4></div></div>
                <div class="col-md-3"><div class="card p-3"><h5>TODAY TRADES</h5><h4>{today_count}/{MAX_TRADES_PER_DAY}</h4></div></div>
            </div>
        </div>
    </body>
    </html>
    """, 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)