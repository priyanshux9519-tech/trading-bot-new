import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- UPSTOX & BOT CONFIGURATION ---
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
CLIENT_ID = "6a6e736c-824b-4d58-adb6-34092ecd371a"

# --- FREE PROXY CONFIGURATION ---
# (अगर आपके पास अपना Static Proxy IP/Port हो तो यहाँ रिप्लेस करें)
PROXIES = {
    "http": "http://185.199.229.156:7492",
    "https": "http://185.199.229.156:7492"
}

# --- OUTBOUND IP CHECK ---
def get_outbound_ip():
    try:
        # प्रॉक्सी के माध्यम से आउटबाउंड IP चेक करना
        ip = requests.get('https://api.ipify.org', proxies=PROXIES, timeout=5).text
        print(f"RENDER_PUBLIC_IP (Via Proxy): {ip}")
        return ip
    except Exception as e:
        # अगर प्रॉक्सी फ़ेल हो तो डायरेक्ट IP चेक
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
        # proxies=PROXIES के ज़रिए Upstox को रिक्वेस्ट भेजना
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