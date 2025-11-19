import os
import logging
from datetime import datetime
from flask import Flask, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# === Logging diario ===
if not os.path.exists("logs"):
    os.makedirs("logs")

log_filename = datetime.now().strftime("logs/%Y-%m-%d.log")
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# === Variables de entorno ===
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

PAIR = os.getenv("PAIR", "BNBUSDT")
RSI_BUY = float(os.getenv("RSI_BUY", "30"))
RSI_SELL = float(os.getenv("RSI_SELL", "70"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.02"))
TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.04"))

session = HTTP(
    testnet=BYBIT_TESTNET,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

# === RSI ===
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

# === Calcular QTY dinámico (1% del balance) ===
def calcular_qty_porcentaje(session, symbol, porcentaje=0.01):
    try:
        coin = symbol.replace("USDT", "")
        balance = session.get_wallet_balance(accountType="UNIFIED", coin=coin)
        disponible = float(balance["result"]["list"][0]["availableBalance"])
        qty = disponible * porcentaje
        return round(qty, 6)
    except Exception as e:
        logging.error(f"Error al calcular QTY dinámico: {e}")
        return 0.0

# === Posiciones abiertas ===
def get_open_positions():
    try:
        positions = session.get_positions(category="spot", symbol=PAIR)
        return positions["result"]["list"]
    except Exception as e:
        logging.error(f"Error obteniendo posiciones: {e}")
        return []

# === Endpoints ===
@app.route("/", methods=["GET"])
def index():
    return "OK bot-momentum-v6-dynamic"

@app.route("/ejecutar", methods=["POST"])
def ejecutar():
    try:
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

        open_positions = get_open_positions()
        has_position = len(open_positions) > 0

        if not has_position:
            qty = calcular_qty_porcentaje(session, PAIR)
            if qty > 0:
                if rsi < RSI_BUY:
                    signal = "buy"
                    order_result = session.place_order(
                        category="spot",
                        symbol=PAIR,
                        side="Buy",
                        orderType="Market",
                        qty=qty
                    )
                elif rsi > RSI_SELL:
                    signal = "sell"
                    order_result = session.place_order(
                        category="spot",
                        symbol=PAIR,
                        side="Sell",
                        orderType="Market",
                        qty=qty
                    )
            else:
                logging.warning("Saldo insuficiente para operar.")

        logging.info(
            f"Ejecutado: precio={current_price}, RSI={rsi:.2f}, señal={signal}, orden={order_result}"
        )

        return jsonify({
            "message": "Bot ejecutado",
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

@app.route("/status", methods=["GET"])
def status():
    try:
        kline = session.get_kline(
            category="spot",
            symbol=PAIR,
            interval="1",
            limit=100
        )
        prices = [float(candle[4]) for candle in kline["result"]["list"]]
        current_price = prices[-1]
        rsi = calculate_rsi(prices)
        open_positions = get_open_positions()

        return jsonify({
            "status": "ok",
            "pair": PAIR,
            "price": current_price,
            "rsi": rsi,
            "open_positions": open_positions
        })
    except Exception as e:
        logging.error(f"Error en /status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/resumen", methods=["GET"])
def resumen():
    try:
        log_file = datetime.now().strftime("logs/%Y-%m-%d.log")
        resumen_data = {"buy": 0, "sell": 0, "no_signal": 0, "total": 0}

        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                for line in f:
                    if "señal=buy" in line:
                        resumen_data["buy"] += 1
                    elif "señal=sell" in line:
                        resumen_data["sell"] += 1
                    elif "señal=no_signal" in line:
                        resumen_data["no_signal"] += 1
                    resumen_data["total"] += 1

        return jsonify({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "summary": resumen_data
        })
    except Exception as e:
        logging.error(f"Error en /resumen: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
