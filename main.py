import os
import time
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- UPSTOX & BOT CONFIGURATION ---
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
CLIENT_ID = "6a6e736c-824b-4d58-adb6-34092ecd371a"
APP_URL = "https://trading-bot-new-oxf5.onrender.com"

# --- FREE PROXY CONFIGURATION ---
PROXIES = {
    "http": "http://185.199.229.156:7492",
    "https": "http://185.199.229.156:7492"
}

# --- SELF PING FUNCTION (EVERY 2.5 MINUTES) ---
def self_ping():
    while True:
        time.sleep(150)  # 2.5 मिनट (150 सेकंड)
        try:
            res = requests.get(APP_URL, timeout=10)
            print(f"Self-ping success: Status {res.status_code}")
        except Exception as e:
            print(f"Self-ping failed: {e}")

# बैकग्राउंड थ्रेड स्टार्ट करें
threading.Thread(target=self_ping, daemon=True).start()

# --- OUTBOUND IP CHECK ---
def get_outbound_ip():
    try:
        ip = requests.get('https://api.ipify.org', proxies=PROXIES, timeout=5).text
        print(f"RENDER_PUBLIC_IP (Via Proxy): {ip}")
        return ip
    except Exception as e:
        try:
            ip = requests.get('https://api.ipify.org', timeout=5).text
            print(f"RENDER_PUBLIC_IP (Direct): {ip}")
            return ip
        except Exception as err:
            print(f"Could not fetch IP: {err}")
            return "IP Fetch Error"

# सर्वर स्टार्ट होते ही IP प्रिंट करें
get_outbound_ip()

# --- UPSTOX ORDER FUNCTION ---
def place_upstox_order(instrument_token, quantity, transaction_type, order_type="MARKET", price=0):
    url = "https://api.upstox.com/v2/order/place"
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}"
    }
    
    payload = {
        "quantity": int(quantity),
        "product": "I",  # Intraday
        "validity": "DAY",
        "price": float(price),
        "tag": "RenderTradingBot",
        "instrument_token": instrument_token,
        "order_type": order_type,
        "transaction_type": transaction_type,
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False
    }

    try:
        response = requests.post(url, json=payload, headers=headers, proxies=PROXIES, timeout=10)
        res_data = response.json()
        print("Upstox Response:", res_data)
        return res_data
    except Exception as e:
        print("Order Placement Error:", str(e))
        return {"status": "error", "message": str(e)}

# --- FLASK ROUTES ---
@app.route('/', methods=['GET'])
def home():
    current_ip = get_outbound_ip()
    return jsonify({
        "status": "online",
        "bot": "Reliance Intraday Bot",
        "server_ip": current_ip
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)