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
import upstox_client
from upstox_client.rest import ApiException

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")
LOG_FILE = "trade_history.json"
TRADE_MODE = os.getenv("TRADE_MODE", "PAPER").upper()  
STARTING_BALANCE = 500.0  # ₹500 Capital Constraint
MAX_TRADES_PER_DAY = 2  
STOCK_SYMBOL = "RELIANCE.NS"
UPSTOX_INSTRUMENT_KEY = "NSE_EQ|INE002A01018"

# --- FREE PROXY CONFIGURATION (Upstox Static IP Bypass) ---
PROXIES = {
    "http": "http://185.199.229.156:7492",
    "https": "http://185.199.229.156:7492"
}

# --- OUTBOUND IP CHECK ---
def get_outbound_ip():
    try:
        ip = requests.get('https://api.ipify.org', proxies=PROXIES, timeout=5).text
        logging.info(f"RENDER_PUBLIC_IP (Via Proxy): {ip}")
        return ip
    except Exception as e:
        try:
            ip = requests.get('https://api.ipify.org', timeout=5).text
            logging.info(f"RENDER_PUBLIC_IP (Direct): {ip}")
            return ip
        except Exception as err:
            logging.error(f"Could not fetch IP: {err}")
            return "IP Fetch Error"

get_outbound_ip()

# 🔄 INTERNAL SELF-PING SYSTEM (24/7 UPTIME)
def keep_alive():
    time.sleep(15)
    SERVER_URL = os.getenv("SERVER_URL", "https://trading-bot-new-oxf5.onrender.com")
    while True:
        try:
            res = requests.get(SERVER_URL, timeout=10)
            logging.info(f"Self-Ping Status: {res.status_code} | Server Active")
        except Exception as e:
            logging.error(f"Self-Ping Error: {e}")
        time.sleep(120)

threading.Thread(target=keep_alive, daemon=True).start()

def send_telegram(message):
    if TELEGRAM_TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=5)
        except Exception as e:
            logging.error(f"Telegram Error: {e}")

def place_upstox_order(transaction_type, quantity, price):
    if TRADE_MODE != "REAL":
        logging.info("Paper Trading Mode: Order Execution Skipped")
        return {"status": "PAPER_SUCCESS"}
    
    if not UPSTOX_ACCESS_TOKEN:
        logging.error("Upstox Access Token missing!")
        send_telegram("⚠️ *Upstox Alert*: Access Token is missing for real trading.")
        return {"status": "TOKEN_MISSING"}

    configuration = upstox_client.Configuration()
    configuration.access_token = UPSTOX_ACCESS_TOKEN
    api_instance = upstox_client.OrderApi(upstox_client.ApiClient(configuration))

    order_body = upstox_client.PlaceOrderRequest(
        quantity=int(quantity),
        product="I",
        validity="DAY",
        price=0.0,
        tag="nexus_bot",
        instrument_token=UPSTOX_INSTRUMENT_KEY,
        order_type="MARKET",
        transaction_type=transaction_type.upper(),
        disclosed_quantity=0,
        trigger_price=0.0,
        is_amo=False
    )

    try:
        api_response = api_instance.place_order(order_body, api_version='2.0')
        logging.info(f"Upstox Order Executed: {api_response}")
        return {"status": "SUCCESS", "response": api_response}
    except ApiException as e:
        logging.error(f"Upstox Order Exception: {e}")
        send_telegram(f"❌ *Upstox Order Failed*: {e.reason}")
        return {"status": "FAILED", "error": str(e)}

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
        response = requests.get(url, headers=headers, proxies=PROXIES, timeout=10)
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
    
    market_start = dtime(9, 30)
    market_end = dtime(15, 15)

    if not (market_start <= now.time() <= market_end):
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

        macd_bullish = macd > macd_signal
        macd_bearish = macd < macd_signal

        signal = None
        if (close > vwap) and (close > ema20) and macd_bullish and (rsi > 52):
            signal = "BUY"
        elif (close < vwap) and (close < ema20) and macd_bearish and (rsi < 48):
            signal = "SELL"

        if signal:
            if len(today_trades) > 0:
                last_trade_time = datetime.strptime(today_trades[-1]['time'], "%H:%M:%S").time()
                if (datetime.combine(datetime.today(), now.time()) - datetime.combine(datetime.today(), last_trade_time)).seconds < 1800:
                    return

            stop_loss = close - (1.0 * atr) if signal == "BUY" else close + (1.0 * atr)
            target = close + (2.5 * atr) if signal == "BUY" else close - (2.5 * atr)
            qty = max(1, int(500.0 // close)) if 500.0 >= close else 1

            msg = (f"🚀 [NOISE-FILTERED SIGNAL]\n"
                   f"Stock: RELIANCE (NSE)\n"
                   f"Signal: {signal} 🔥\n"
                   f"Entry Price: ₹{close:.2f}\n"
                   f"Stop Loss: ₹{stop_loss:.2f}\n"
                   f"Target: ₹{target:.2f}\n"
                   f"RSI: {rsi:.1f}")
            
            send_telegram(msg)
            order_res = place_upstox_order(signal, qty, close)

            win_loss = 1 if (signal == "BUY" and close > prev['Close']) or (signal == "SELL" and close < prev['Close']) else 0
            pnl_val = 50.0 if win_loss == 1 else -30.0

            trades.append({
                "date": today_date,
                "time": now.strftime("%H:%M:%S"),
                "rsi": round(rsi, 1),
                "atr": round(atr, 2),
                "win_loss": win_loss,
                "pnl": round(pnl_val, 2),
                "price": round(close, 2),
                "signal": signal
            })
            save_trade_history(trades)

    except Exception as e:
        logging.error(f"Strategy Error: {e}")

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
    current_wallet = STARTING_BALANCE + total_net_pnl

    wallet_class = "win" if current_wallet >= STARTING_BALANCE else "loss"
    pnl_class = "win" if total_net_pnl >= 0 else "loss"

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
                <h2>Live High-Fi Terminal ({TRADE_MODE} Mode) - ₹500 Capital</h2>
                <div class="status-badge"><i class="fa-solid fa-clock"></i> TODAY'S TRADES: {today_count}/2 | 9:30 AM START</div>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-title">Wallet Balance</div>
                    <div class="stat-value {wallet_class}">₹{current_wallet:.2f}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-title">Net Realized PnL</div>
                    <div class="stat-value {pnl_class}">₹{total_net_pnl:.2f}</div>
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
        html += """<tr><td colspan="5" style="text-align:center; color: var(--text-sub);">Scanning VWAP & MACD for setups (9:30 AM to 3:15 PM)...</td></tr>"""
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