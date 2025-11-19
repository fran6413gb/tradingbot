import os
import logging
from datetime import datetime
from flask import Flask, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# === Configuración de logging ===
if not os.path.exists("logs"):
    os.makedirs("logs")

log_filename = datetime.now().strftime("logs/%Y-%m-%d.log")
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# === Configuración de Bybit ===
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

PAIR = os.getenv("PAIR", "BNBUSDT")
QTY = os.getenv("QTY", "0.1")  # cantidad por trade
RSI_BUY = float(os.getenv("RSI_BUY", "30"))
RSI_SELL = float(os.getenv("RSI_SELL", "70"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.02"))   # 2% por defecto
TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.04"))  # 4% por defecto

session = HTTP(
    testnet=BYBIT_TESTNET,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

# === Función de cálculo RSI ===
def calculate_rsi(prices, period=14):
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period

    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    return 100 - (100 / (1 + rs))

# === Función para revisar posiciones abiertas ===
def get_open_positions():
    try:
        positions = session.get_positions(category="spot", symbol=PAIR)
        return positions["result"]["list"]
    except Exception as e:
        logging.error(f"Error obteniendo posiciones: {e}")
        return []

# === Endpoint raíz ===
@app.route("/", methods=["GET"])
def index():
    return "OK bot-momentum-v6-advanced"

# === Endpoint de ejecución ===
@app.route("/ejecutar", methods=["POST"])
def ejecutar():
    try:
        # Obtener precios recientes
        kline = session.get_kline(
            category="spot",
            symbol=PAIR,
            interval="1",
            limit=100
        )
        prices = [float(candle[4]) for candle in kline["result"]["list"]]
        current_price = prices[-1]
        rsi = calculate_rsi(prices)

        signal = "no_signal"
        order_result = None

        # Revisar si hay posiciones abiertas
        open_positions = get_open_positions()
        has_position = len(open_positions) > 0

        if not has_position:
            if rsi < RSI_BUY:
                signal = "buy"
                order_result = session.place_order(
                    category="spot",
                    symbol=PAIR,
                    side="Buy",
                    orderType="Market",
                    qty=QTY
                )
            elif rsi > RSI_SELL:
                signal = "sell"
                order_result = session.place_order(
                    category="spot",
                    symbol=PAIR,
                    side="Sell",
                    orderType="Market",
                    qty=QTY
                )

        # Log diario
        logging.info(
            f"Ejecutado: precio={current_price}, RSI={rsi:.2f}, señal={signal}, orden={order_result}"
        )

        return jsonify({
            "message": "Bot de Momentum ejecutado en Bybit (real)",
            "result": {
                "price": current_price,
                "rsi": rsi,
                "status": signal,
                "order": order_result,
                "open_positions": open_positions
            }
        })

    except Exception as e:
        logging.error(f"Error en ejecución: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
