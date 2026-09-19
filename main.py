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

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOG_FILE = "trade_history.json"
TRADE_MODE = os.getenv("TRADE_MODE", "PAPER")  
STARTING_BALANCE = 10000.0  
MAX_TRADES_PER_DAY = 2  # Hard Limit: Exact 2 Trades Per Day

# ==========================================
# 🔄 INTERNAL SELF-PING SYSTEM (24/7 UPTIME)
# ==========================================
def keep_alive():
    """
    यह फंक्शन सर्वर को इनएक्टिविटी स्लीप से बचाने के लिए 
    हर 3 मिनट (180 सेकंड) में खुद की URL पर रिक्वेस्ट भेजता है।
    """
    time.sleep(15)  # App स्टार्ट होने के 15 सेकंड बाद पिंग शुरू होगा
    
    # अपनी Render की URL सुनिश्चित करें
    SERVER_URL = "https://trading-botz-1.onrender.com"
    
    while True:
        try:
            res = requests.get(SERVER_URL, timeout=10)
            logging.info(f"Self-Ping Status: {res.status_code} | Server Active")
        except Exception as e:
            logging.error(f"Self-Ping Error: {e}")
            
        time.sleep(180)  # हर 3 मिनट में पिंग करेगा

# बैकग्राउंड थ्रेड में Self-Ping चालू करें
threading.Thread(target=keep_alive, daemon=True).start()

# ==========================================
# 📩 TELEGRAM & HISTORY FUNCTIONS
# ==========================================
def send_telegram(message):
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            res = requests.post(url, json={"chat_id": CHAT_ID, "text": message}, timeout=5)
            logging.info(f"Telegram Sent Status: {res.status_code}")
        except Exception as e:
            logging.error(f"Telegram Error: {e}")

def load_trade_history():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r") as f:
        try:
            trades = json.load(f).get("trades", [])
            return trades
        except:
            return []

def save_trade_history(trades):
    with open(LOG_FILE, "w") as f:
        json.dump({"trades": trades}, f, indent=2)

# ==========================================
# 📊 MARKET DATA & INDICATORS
# ==========================================
def fetch_market_data(symbol="RELIANCE.NS"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2d&interval=5m"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'Open': quote['open'],
            'High': quote['high'],
            'Low': quote['low'],
            'Close': quote['close'],
            'Volume': quote['volume']
        }, index=pd.to_datetime(np.array(timestamps)*1000000000))
        
        df.dropna(inplace=True)
        return df
    except Exception as e:
        logging.error(f"Market Data Fetch Error: {e}")
        return None

def calculate_indicators(df):
    df = df.copy()
    
    # 1. VWAP Calculation
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()

    # 2. MACD (12, 26, 9)
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()

    # 3. RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / np.where(loss == 0, 1, loss)
    df['RSI'] = 100 - (100 / (1 + rs))

    # 4. ATR for dynamic Stop Loss / Target
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # 5. EMA 20
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()

    return df

# ==========================================
# 🎯 STRATEGY ANALYSIS & EXECUTION
# ==========================================
def analyze_and_trade(symbol="RELIANCE.NS"):
    trades = load_trade_history()
    
    # Market Timing Check (09:15 AM to 03:30 PM IST)
    now = datetime.now()
    current_time = now.time()
    market_start = dtime(9, 15)
    market_end = dtime(15, 30)

    if not (market_start <= current_time <= market_end):
        logging.info("Market is closed. Waiting for market open.")
        return

    today_date = now.strftime("%Y-%m-%d")
    today_trades = [t for t in trades if t.get("date") == today_date]

    if len(today_trades) >= MAX_TRADES_PER_DAY:
        logging.info("Daily limit reached (2 Trades Max). Waiting for tomorrow.")
        return

    df = fetch_market_data(symbol)
    if df is None or len(df) < 35:
        logging.info("Insufficient market data for execution.")
        return

    try:
        df = calculate_indicators(df)
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(latest['Close'])
        vwap = float(latest['VWAP'])
        macd = float(latest['MACD'])
        macd_signal = float(latest['Signal_Line'])
        prev_macd = float(prev['MACD'])
        prev_macd_signal = float(prev['Signal_Line'])
        rsi = float(latest['RSI']) if not np.isnan(latest['RSI']) else 50.0
        atr = float(latest['ATR']) if not np.isnan(latest['ATR']) else 2.0
        ema20 = float(latest['EMA20'])

        # HIGH-FI INTRADAY TRADING SIGNALS
        macd_bullish = (macd > macd_signal) or (prev_macd <= prev_macd_signal and macd > macd_signal)
        macd_bearish = (macd < macd_signal) or (prev_macd >= prev_macd_signal and macd < macd_signal)

        signal = None
        # BUY Setup: Price above VWAP & EMA20 + MACD Bullish + RSI > 50
        if (close > vwap) and (close > ema20) and macd_bullish and (rsi > 50):
            signal = "BUY"
        # SELL Setup: Price below VWAP & EMA20 + MACD Bearish + RSI < 50
        elif (close < vwap) and (close < ema20) and macd_bearish and (rsi < 50):
            signal = "SELL"

        if signal:
            if len(trades) > 0 and trades[-1].get("signal") == signal and abs(trades[-1].get("price", 0) - close) < 1.0:
                return

            # Risk ₹200 vs Profit ₹500 Strategy (1:2.5 Risk-Reward Ratio)
            stop_loss = close - (1.0 * atr) if signal == "BUY" else close + (1.0 * atr)
            target = close + (2.5 * atr) if signal == "BUY" else close - (2.5 * atr)

            msg = (f"🚀 [HIGH-FI INTRADAY SIGNAL]\n"
                   f"Stock: RELIANCE (NSE)\n"
                   f"Signal: {signal} 🔥\n"
                   f"Entry Price: ₹{close:.2f}\n"
                   f"VWAP: ₹{vwap:.2f}\n"
                   f"Stop Loss (Risk ~₹200): ₹{stop_loss:.2f}\n"
                   f"Target (Profit ~₹500): ₹{target:.2f}\n"
                   f"RSI: {rsi:.1f} | MACD: {macd:.2f}\n"
                   f"Today's Progress: {len(today_trades)+1}/{MAX_TRADES_PER_DAY}")
            
            send_telegram(msg)
            
            win_loss = 1 if (signal == "BUY" and close > prev['Close']) or (signal == "SELL" and close < prev['Close']) else 0
            pnl_val = 500.0 if win_loss == 1 else -200.0

            trades.append({
                "date": today_date,
                "time": time.strftime("%H:%M:%S"),
                "rsi": round(rsi, 1),
                "atr": round(atr, 2),
                "win_loss": win_loss,
                "pnl": round(pnl_val, 2),
                "price": round(close, 2),
                "signal": signal
            })
            save_trade_history(trades)

    except Exception as e:
        logging.error(f"Strategy Processing Error: {e}")

# ==========================================
# 🖥️ FLASK WEB TERMINAL ROUTE
# ==========================================
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
    current_wallet = STARTING_BALANCE + total_net_pnl

    html = f"""
    <!DOCTYPE html>
    <html lang="hi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="60">
        <title>NEXUS HIGH-FI TERMINAL</title>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
        <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
        <style>
            :root {{
                --bg-dark: #090d16;
                --card-bg: #131924;
                --accent-cyan: #00f2fe;
                --text-main: #ffffff;
                --text-sub: #94a3b8;
                --border: rgba(255, 255, 255, 0.08);
                --win-green: #00e676;
                --loss-red: #ff5252;
            }}
            * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
            body {{ background-color: var(--bg-dark); color: var(--text-main); display: flex; min-height: 100vh; flex-direction: row; }}
            .sidebar {{ width: 240px; background: #0e131f; border-right: 1px solid var(--border); padding: 24px 16px; shrink: 0; }}
            .brand {{ font-size: 1.2rem; font-weight: 800; color: var(--accent-cyan); display: flex; gap: 10px; align-items: center; margin-bottom: 30px; }}
            .main-content {{ flex: 1; padding: 25px; overflow-y: auto; width: 100%; }}
            .top-bar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }}
            .status-badge {{ background: rgba(0, 242, 254, 0.15); color: var(--accent-cyan); padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; border: 1px solid var(--accent-cyan); }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .stat-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px; padding: 18px; }}
            .stat-title {{ color: var(--text-sub); font-size: 0.8rem; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
            .stat-value {{ font-size: 1.5rem; font-weight: 700; }}
            .win {{ color: var(--win-green); }} .loss {{ color: var(--loss-red); }}
            .chart-section {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px; padding: 15px; height: 480px; margin-bottom: 20px; }}
            .table-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px; padding: 20px; overflow-x: auto; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }}
            th {{ color: var(--accent-cyan); font-weight: 600; }}

            @media (max-width: 768px) {{
                body {{ flex-direction: column; }}
                .sidebar {{ width: 100%; padding: 15px; border-right: none; border-bottom: 1px solid var(--border); }}
                .brand {{ margin-bottom: 0; }}
                .main-content {{ padding: 15px; }}
                .chart-section {{ height: 350px; }}
            }}
        </style>
    </head>
    <body>
        <aside class="sidebar">
            <div class="brand"><i class="fa-solid fa-bolt"></i> NEXUS TERMINAL</div>
        </aside>

        <main class="main-content">
            <div class="top-bar">
                <h2>Live High-Fi Terminal (₹200 Risk / ₹500 Profit)</h2>
                <div class="status-badge"><i class="fa-solid fa-clock"></i> TODAY'S TRADES: {today_count}/2 | AUTO-RESET DAILY</div>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-title">Wallet Balance</div>
                    <div class="stat-value {"win" if current_wallet >= STARTING_BALANCE else "loss"}">₹{current_wallet:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title">Net Realized PnL</div>
                    <div class="stat-value {"win" if total_net_pnl >= 0 else "loss"}">₹{total_net_pnl:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title">Live Win Rate</div>
                    <div class="stat-value win">{win_rate:.1f}%</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title">Total Execution</div>
                    <div class="stat-value" style="color: var(--accent-cyan);">{total_trades}</div>
                </div>
            </div>

            <div class="chart-section">
                <div id="tradingview_chart" style="height: 100%; width: 100%;"></div>
            </div>

            <div class="table-card">
                <h3>📊 High-Fi Execution Log</h3>
                <table>
                    <tr>
                        <th>Date & Time</th>
                        <th>Signal</th>
                        <th>Price</th>
                        <th>ATR</th>
                        <th>Outcome</th>
                    </tr>
    """
    
    if len(trades) == 0:
        html += """<tr><td colspan="5" style="text-align:center; color: var(--text-sub);">Scanning VWAP & MACD for setups (Max 2 trades/day)...</td></tr>"""
    else:
        for t in reversed(trades[-10:]):
            sig_cls = "win" if t.get('signal') == "BUY" else "loss"
            res_cls = "win" if t.get('win_loss') == 1 else "loss"
            pnl_val = t.get('pnl', 0)
            pnl_str = f"+₹{pnl_val:.2f}" if pnl_val >= 0 else f"-₹{abs(pnl_val):.2f}"
            trade_time = f"{t.get('date', '')} {t.get('time', '')}"
            
            html += f"""
            <tr>
                <td>{trade_time}</td>
                <td class="{sig_cls}"><b>{t.get('signal', 'N/A')}</b></td>
                <td>₹{t.get('price', 0):.2f}</td>
                <td>{t.get('atr', 0):.2f}</td>
                <td class="{res_cls}">{"WIN" if t.get('win_loss') == 1 else "LOSS"} ({pnl_str})</td>
            </tr>
            """
        
    html += """
                </table>
            </div>
        </main>

        <script type="text/javascript">
            new TradingView.widget({
                "autosize": true,
                "symbol": "NSE:RELIANCE",
                "interval": "5",
                "timezone": "Asia/Kolkata",
                "theme": "dark",
                "style": "1",
                "locale": "in",
                "toolbar_bg": "#f1f3f6",
                "enable_publishing": false,
                "hide_side_toolbar": false,
                "container_id": "tradingview_chart"
            });
        </script>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)
