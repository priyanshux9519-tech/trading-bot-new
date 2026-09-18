import os
import json
import logging
import time
from datetime import datetime, time as dtime
import requests
from flask import Flask, render_template_string
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
        logging.error(f"Fetch Error ({symbol}): {e}")
        return None

def get_external_market_sentiment():
    nifty_df = fetch_market_data("^NSEI")
    vix_df = fetch_market_data("^INDIAVIX")
    
    nifty_signal = "NEUTRAL"
    high_volatility = False

    if nifty_df is not None and len(nifty_df) >= 20:
        nifty_ema20 = nifty_df['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
        nifty_close = nifty_df['Close'].iloc[-1]
        if nifty_close > nifty_ema20:
            nifty_signal = "BULLISH"
        elif nifty_close < nifty_ema20:
            nifty_signal = "BEARISH"

    if vix_df is not None and len(vix_df) > 0:
        current_vix = vix_df['Close'].iloc[-1]
        if current_vix > 22.0:
            high_volatility = True

    return nifty_signal, high_volatility

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

        nifty_sentiment, high_volatility = get_external_market_sentiment()

        if high_volatility:
            return

        signal = None
        if (close > vwap) and (close > ema20) and (macd > macd_signal) and (rsi > 50):
            if nifty_sentiment == "BULLISH":
                signal = "BUY"
        elif (close < vwap) and (close < ema20) and (macd < macd_signal) and (rsi < 50):
            if nifty_sentiment == "BEARISH":
                signal = "SELL"

        if signal:
            stop_loss = close - (1.0 * atr) if signal == "BUY" else close + (1.0 * atr)
            target = close + (2.5 * atr) if signal == "BUY" else close - (2.5 * atr)

            msg = (f"🎮 [NEXUS CYBER-SIGNAL]\nStock: RELIANCE (NSE)\nTrend: {nifty_sentiment}\nSignal: {signal}\nEntry: ₹{close:.2f}\nSL: ₹{stop_loss:.2f}\nTarget: ₹{target:.2f}")
            send_telegram(msg)

            win_loss = 1 if (signal == "BUY" and close > prev['Close']) or (signal == "SELL" and close < prev['Close']) else 0
            trades.append({"date": today_date, "time": time.strftime("%H:%M:%S"), "price": round(close, 2), "signal": signal, "win_loss": win_loss, "pnl": 500.0 if win_loss == 1 else -200.0})
            save_trade_history(trades)
    except Exception as e:
        logging.error(f"Error: {e}")

# 🎮 GAMING CYBERPUNK DASHBOARD HTML
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEXUS CYBER TERMINAL</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet">
    <style>
        body {
            background-color: #050811;
            color: #00ffcc;
            font-family: 'Share Tech Mono', monospace;
            background-image: radial-gradient(circle, #0d1b2a 10%, #050811 90%);
        }
        h1, h2, h4, h5 {
            font-family: 'Orbitron', sans-serif;
            text-shadow: 0 0 10px #00ffcc, 0 0 20px #00ffcc;
        }
        .card-cyber {
            background: rgba(13, 27, 42, 0.85);
            border: 1px solid #00ffcc;
            box-shadow: 0 0 15px rgba(0, 255, 204, 0.2);
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .neon-box {
            border-left: 4px solid #ff0055;
        }
        .table-cyber {
            color: #00ffcc;
            background-color: transparent;
        }
        .table-cyber th {
            border-bottom: 2px solid #00ffcc;
            color: #ff0055;
            font-family: 'Orbitron', sans-serif;
        }
        .badge-buy {
            background-color: #00ffcc;
            color: #000;
            font-weight: bold;
            box-shadow: 0 0 10px #00ffcc;
        }
        .badge-sell {
            background-color: #ff0055;
            color: #fff;
            font-weight: bold;
            box-shadow: 0 0 10px #ff0055;
        }
        .glow-text {
            animation: pulse 2s infinite alternate;
        }
        @keyframes pulse {
            0% { opacity: 0.7; }
            100% { opacity: 1; text-shadow: 0 0 15px #00ffcc; }
        }
    </style>
</head>
<body>
    <div class="container py-4">
        <div class="d-flex justify-content-between align-items-center mb-4 pb-2 border-bottom border-info">
            <h2 class="glow-text">⚡ NEXUS CYBER TERMINAL</h2>
            <span class="badge badge-buy p-2">MODE: {{ trade_mode }}</span>
        </div>
        
        <div class="row">
            <div class="col-md-3">
                <div class="card card-cyber p-3">
                    <small class="text-secondary">TARGET ASSET</small>
                    <h4 class="text-white">RELIANCE.NS</h4>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card card-cyber p-3 neon-box">
                    <small class="text-secondary">SYSTEM STATUS</small>
                    <h4 class="text-success">24/7 ONLINE</h4>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card card-cyber p-3">
                    <small class="text-secondary">DAILY TRADES LIMIT</small>
                    <h4 class="text-warning">{{ max_trades }} MAX</h4>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card card-cyber p-3">
                    <small class="text-secondary">LOGGED TRADES</small>
                    <h4 class="text-info">{{ total_trades }}</h4>
                </div>
            </div>
        </div>

        <div class="card card-cyber p-4 mt-3">
            <h5 class="mb-3 text-warning">🎮 EXECUTED TRADE LOGS</h5>
            <table class="table table-cyber table-hover">
                <thead>
                    <tr>
                        <th>DATE</th>
                        <th>TIME</th>
                        <th>SIGNAL</th>
                        <th>PRICE</th>
                        <th>EST. P&L</th>
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
                        <td colspan="5" class="text-center text-muted">SYSTEM INITIALIZED. WAITING FOR MARKET HOURS...</td>
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
