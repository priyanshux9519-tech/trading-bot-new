import os
import json
import logging
import time
from datetime import datetime, time as dtime
import requests
from flask import Flask, request, render_template_string
import pandas as pd
import numpy as np
import threading

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOG_FILE = "trade_history.json"
TRADE_MODE = os.getenv("TRADE_MODE", "PAPER")  
STARTING_BALANCE = 10000.0  
MAX_TRADES_PER_DAY = 2

# 🔄 24/7 SELF-PING SYSTEM
def keep_alive():
    time.sleep(15)
    SERVER_URL = os.getenv("SERVER_URL", "https://trading-bot-new-oxf5.onrender.com")
    while True:
        try:
            res = requests.get(SERVER_URL, timeout=10)
            logging.info(f"Self-Ping Status: {res.status_code}")
        except Exception as e:
            logging.error(f"Self-Ping Error: {e}")
        time.sleep(180)

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
        logging.error(f"Fetch Error: {e}")
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
    if not (dtime(9, 15) <= now.time() <= dtime(15, 30)):
        return df_info if 'df_info' in locals() else None
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

        macd_bullish = macd > macd_signal
        macd_bearish = macd < macd_signal

        signal = None
        if (close > vwap) and (close > ema20) and macd_bullish and (rsi > 50):
            signal = "BUY"
        elif (close < vwap) and (close < ema20) and macd_bearish and (rsi < 50):
            signal = "SELL"

        if signal:
            stop_loss = close - (1.0 * atr) if signal == "BUY" else close + (1.0 * atr)
            target = close + (2.5 * atr) if signal == "BUY" else close - (2.5 * atr)

            msg = (f"🚀 [HIGH-FI INTRADAY SIGNAL]\nStock: RELIANCE (NSE)\nSignal: {signal}\nEntry: ₹{close:.2f}\nStop Loss: ₹{stop_loss:.2f}\nTarget: ₹{target:.2f}")
            send_telegram(msg)

            win_loss = 1 if (signal == "BUY" and close > prev['Close']) or (signal == "SELL" and close < prev['Close']) else 0
            trades.append({"date": today_date, "time": time.strftime("%H:%M:%S"), "price": round(close, 2), "signal": signal, "win_loss": win_loss, "pnl": 500.0 if win_loss == 1 else -200.0})
            save_trade_history(trades)
        return latest
    except Exception as e:
        logging.error(f"Error: {e}")
        return None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS HIGH-FI TERMINAL</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: sans-serif; }
        .card { background-color: #161b22; border: 1px solid #30363d; margin-bottom: 15px; }
        .table { color: #c9d1d9; }
        .badge-buy { background-color: #238636; }
        .badge-sell { background-color: #da3633; }
    </style>
</head>
<body>
    <div class="container py-4">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h2>🚀 NEXUS HIGH-FI DASHBOARD</h2>
            <span class="badge bg-primary fs-6">Mode: {{ trade_mode }}</span>
        </div>
        
        <div class="row">
            <div class="col-md-3">
                <div class="card p-3">
                    <small text-muted>STOCK</small>
                    <h4>RELIANCE.NS</h4>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small text-muted>STATUS</small>
                    <h4 class="text-success">24/7 ACTIVE</h4>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small text-muted>MAX TRADES/DAY</small>
                    <h4>{{ max_trades }}</h4>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small text-muted>TOTAL LOGGED TRADES</small>
                    <h4>{{ total_trades }}</h4>
                </div>
            </div>
        </div>

        <div class="card p-3 mt-3">
            <h5>Trade History Logs</h5>
            <table class="table table-dark table-striped mt-2">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Time</th>
                        <th>Signal</th>
                        <th>Price</th>
                        <th>P&L (Est.)</th>
                    </tr>
                </thead>
                <tbody>
                    {% for trade in trades %}
                    <tr>
                        <td>{{ trade.date }}</td>
                        <td>{{ trade.time }}</td>
                        <td>
                            <span class="badge {{ 'badge-buy' if trade.signal == 'BUY' else 'badge-sell' }}">
                                {{ trade.signal }}
                            </span>
                        </td>
                        <td>₹{{ trade.price }}</td>
                        <td class="{{ 'text-success' if trade.pnl > 0 else 'text-danger' }}">₹{{ trade.pnl }}</td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="5" class="text-center text-muted">No trades executed today yet.</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""

@app.route('/', methods=['GET', 'HEAD'])
def home():
    analyze_and_trade()
    trades = load_trade_history()
    return render_template_string(
        HTML_TEMPLATE, 
        trades=trades, 
        total_trades=len(trades), 
        max_trades=MAX_TRADES_PER_DAY,
        trade_mode=TRADE_MODE
    ), 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)
