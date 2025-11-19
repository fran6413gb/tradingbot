import os
import time
import math
import logging
from typing import Dict, Any, List, Optional

import ccxt
import pandas as pd
from flask import Flask, request

# ----------------------------
# Configuración general
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("bot-momentum-v6")

app = Flask(__name__)

# Credenciales Bybit (directas, según tu instrucción)
BYBIT_API_KEY = "QkCy2ZIzsESTRFDaqs"
BYBIT_SECRET_KEY = "zSX1Db1t2KJLxZP2JUgb8qdssSnSBCrVLX0U"

# Parámetros del bot
SYMBOL = "BNB/USDT"
TIMEFRAME = "1h"
OHLCV_LIMIT = 300

# Parámetros de trading (simulación)
FEE_RATE = 0.0006         # comisión aproximada
POSITION_USDT = 100       # tamaño fijo por operación (simulado)
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Backoff y reintentos CCXT
MAX_RETRIES = 3
RETRY_SLEEP_SEC = 2

# ----------------------------
# Utilidades de indicadores
# ----------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    # EMA rápida y lenta
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # RSI (implementación simple sin lib externa)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    rs = avg_gain / (avg_loss.replace(0, 1e-9))
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


def generate_signal(df: pd.DataFrame) -> Optional[str]:
    # Señal por cruce EMA con filtro RSI
    if len(df) < max(EMA_SLOW, RSI_PERIOD) + 2:
        return None

    prev_fast = df["ema_fast"].iloc[-2]
    prev_slow = df["ema_slow"].iloc[-2]
    curr_fast = df["ema_fast"].iloc[-1]
    curr_slow = df["ema_slow"].iloc[-1]
    rsi = df["rsi"].iloc[-1]

    # Cruce al alza con RSI confirmando no sobrecompra severa
    if prev_fast <= prev_slow and curr_fast > curr_slow and rsi < RSI_OVERBOUGHT:
        return "BUY"

    # Cruce a la baja con RSI confirmando no sobreventa severa
    if prev_fast >= prev_slow and curr_fast < curr_slow and rsi > RSI_OVERSOLD:
        return "SELL"

    return None


def simulate_trade(side: str, entry_price: float, exit_price: float) -> float:
    # PnL en USDT con fee por lado
    qty = POSITION_USDT / entry_price
    gross_pnl = (exit_price - entry_price) * qty if side == "BUY" else (entry_price - exit_price) * qty
    fees = (entry_price * qty + exit_price * qty) * FEE_RATE
    net_pnl = gross_pnl - fees
    return net_pnl


# ----------------------------
# CCXT inicialización y fetch
# ----------------------------
def init_exchange() -> ccxt.Exchange:
    exchange = ccxt.bybit({
        "apiKey": BYBIT_API_KEY,
        "secret": BYBIT_SECRET_KEY,
        "enableRateLimit": True,
    })
    # Evitar endpoint privado fetch_currencies en V5
    exchange.has["fetchCurrencies"] = False
    return exchange


def fetch_ohlcv_with_retry(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int) -> List[List[Any]]:
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt > 1:
                logger.info(f"Reintento {attempt}/{MAX_RETRIES} para OHLCV…")
            # Asegura mercados con protección
            try:
                exchange.load_markets()
            except Exception as e:
                logger.warning(f"No se pudo load_markets: {e}")
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 10:
                raise RuntimeError("OHLCV insuficiente o vacío.")
            return ohlcv
        except Exception as e:
            last_exc = e
            logger.warning(f"Fallo fetch_ohlcv: {e}")
            time.sleep(RETRY_SLEEP_SEC)
    raise RuntimeError(f"Fallo persistente al obtener OHLCV: {last_exc}")


# ----------------------------
# Núcleo del bot
# ----------------------------
def ejecutar_bot() -> Dict[str, Any]:
    logger.info("=== Inicio de ciclo de trading ===")

    exchange = init_exchange()
    ohlcv = fetch_ohlcv_with_retry(exchange, SYMBOL, TIMEFRAME, OHLCV_LIMIT)

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = compute_indicators(df)

    last_price = float(df["close"].iloc[-1])
    last_rsi = float(df["rsi"].iloc[-1]) if not math.isnan(df["rsi"].iloc[-1]) else None
    logger.info(f"[Mercado] {SYMBOL} @ {last_price:.4f} | RSI={last_rsi:.2f}" if last_rsi is not None else f"[Mercado] {SYMBOL} @ {last_price:.4f}")

    signal = generate_signal(df)
    if signal is None:
        logger.info("🔎 Sin señal válida en este ciclo.")
        return {
            "status": "no_signal",
            "price": last_price,
            "rsi": last_rsi,
        }

    logger.info(f"🎯 Señal detectada: {signal}")

    # Simulación: entrar y salir en la siguiente vela (si existiera).
    # Para robustez, usamos una salida artificial con pequeño movimiento.
    entry_price = last_price
    # Supón un slippage 0.05% y movimiento posterior 0.2%
    slippage = 0.0005
    move = 0.002 if signal == "BUY" else -0.002
    exit_price = entry_price * (1 + move - slippage if signal == "BUY" else 1 + move + slippage)

    pnl_usdt = simulate_trade(signal, entry_price, exit_price)
    logger.info(f"💹 PnL simulado: {pnl_usdt:.4f} USDT (entry={entry_price:.4f}, exit={exit_price:.4f})")

    # Conteo de racha de pérdidas (en este ciclo stateless)
    loss_streak = 0
    if pnl_usdt < 0:
        loss_streak += 1

    if loss_streak >= 5:
        logger.error("🚨 ALERTA: 5 pérdidas consecutivas detectadas.")
    else:
        logger.info(f"📊 Racha de pérdidas actual: {loss_streak}")

    return {
        "status": "executed",
        "signal": signal,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_usdt": pnl_usdt,
        "loss_streak": loss_streak,
    }


# ----------------------------
# Endpoints HTTP
# ----------------------------
@app.route("/", methods=["GET"])
def health():
    logger.info("GET / healthcheck OK")
    return "OK bot-momentum-v6", 200


@app.route("/robots.txt", methods=["GET"])
def robots():
    return "User-agent: *\nDisallow: /", 200, {"Content-Type": "text/plain"}


@app.route("/ejecutar", methods=["POST"])
def ejecutar_desde_scheduler():
    logger.info("✅ POST /ejecutar recibido")
    try:
        result = ejecutar_bot()
        return {
            "message": "Bot de Momentum ejecutado en Bybit (simulación)",
            "result": result,
        }, 200
    except Exception as e:
        logger.error(f"🚨 Error en /ejecutar: {e}")
        return {"error": str(e)}, 500
