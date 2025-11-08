#!/usr/bin/env python3
# coding: utf-8
"""
Bot Telegram actualizado (async) + MetaApi
Compatible con python-telegram-bot v20/21+ y metaapi-cloud-sdk asíncrono.
"""

import os
import math
import logging
from typing import Dict, List, Optional

from metaapi_cloud_sdk import MetaApi
from prettytable import PrettyTable

from telegram import Update, ParseMode
from telegram.constants import ParseMode as PM
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Estados del ConversationHandler
CALCULATE, TRADE, DECISION = range(3)

# Símbolos permitidos (mantené o adaptá según lo necesites)
SYMBOLS = [
    "AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURCHF",
    "EURGBP", "EURJPY", "EURNZD", "EURUSD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "NOW",
    "NZDCAD", "NZDCHF", "NZDJPY", "NZDUSD", "USDCAD", "USDCHF", "USDJPY", "XAGUSD", "XAUUSD"
]

# Carga de environment variables
API_KEY = os.environ.get("API_KEY")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID")
TOKEN = os.environ.get("TOKEN")
TELEGRAM_USER = os.environ.get("TELEGRAM_USER")  # sin '@'
APP_URL = os.environ.get("APP_URL", "")
PORT = int(os.environ.get("PORT", "8443"))
try:
    RISK_FACTOR = float(os.environ.get("RISK_FACTOR", "0.01"))
except Exception:
    RISK_FACTOR = 0.01

if not all([API_KEY, ACCOUNT_ID, TOKEN, TELEGRAM_USER, APP_URL]):
    logger.warning(
        "Faltan variables de entorno importantes. Asegurate de setear API_KEY, ACCOUNT_ID, TOKEN, TELEGRAM_USER y APP_URL")

# ---------- Helpers ----------


def parse_signal(signal_text: str) -> Dict:
    """
    Parsea el texto del signal y devuelve dict con OrderType, Symbol, Entry, StopLoss, TP(list), RiskFactor
    Retorna {} si inválido.
    """
    lines = [l.strip() for l in signal_text.splitlines() if l.strip()]
    if not lines:
        return {}

    first = lines[0].lower()

    order_type = None
    if "buy limit" in first:
        order_type = "Buy Limit"
    elif "sell limit" in first:
        order_type = "Sell Limit"
    elif "buy stop" in first:
        order_type = "Buy Stop"
    elif "sell stop" in first:
        order_type = "Sell Stop"
    elif first.startswith("buy"):
        order_type = "Buy"
    elif first.startswith("sell"):
        order_type = "Sell"
    else:
        return {}

    # Symbol: última palabra de la primera línea
    parts = lines[0].split()
    symbol = parts[-1].upper()

    if symbol not in SYMBOLS:
        return {}

    # Entry: segunda línea, última palabra (puede ser NOW)
    if len(lines) < 4:
        return {}  # necesitamos al menos Entry, SL y TP

    entry_raw = lines[1].split()[-1].upper()
    entry: Optional[float] = entry_raw
    if entry_raw != "NOW":
        try:
            entry = float(entry_raw)
        except Exception:
            return {}

    # Stop Loss
    try:
        stoploss = float(lines[2].split()[-1])
    except Exception:
        return {}

    # TP (1 o 2)
    tp_list: List[float] = []
    try:
        tp_list.append(float(lines[3].split()[-1]))
        if len(lines) > 4:
            tp_list.append(float(lines[4].split()[-1]))
    except Exception:
        return {}

    return {
        "OrderType": order_type,
        "Symbol": symbol,
        "Entry": entry,        # float o 'NOW' (str)
        "StopLoss": stoploss,
        "TP": tp_list,
        "RiskFactor": RISK_FACTOR,
    }


def _get_multiplier(symbol: str, entry_value) -> float:
    """
    Determina el multiplicador para convertir diferencia en pips.
    XAUUSD -> 0.1, XAGUSD -> 0.001, otros -> 0.0001 o 0.01 según decimales.
    """
    if symbol == "XAUUSD":
        return 0.1
    if symbol == "XAGUSD":
        return 0.001

    # Si entry_value es cadena (NOW) devolvemos 0.0001 por defecto
    try:
        if isinstance(entry_value, str):
            return 0.0001
        s = f"{entry_value}"
        if "." in s:
            decimals = len(s.split(".")[1])
            # pares con 3 o más decimales -> 0.01 (estoy manteniendo la lógica anterior)
            if decimals >= 3:
                return 0.01
    except Exception:
        pass
    return 0.0001


def create_table(trade: Dict, balance: float, stop_loss_pips: int, tp_pips: List[int]) -> str:
    """Construye una tabla tipo PrettyTable y la devuelve como string (para enviar por Telegram)."""
    table = PrettyTable()
    table.title = "Trade Information"
    table.field_names = ["Key", "Value"]
    table.align["Key"] = "l"
    table.align["Value"] = "l"

    table.add_row([trade["OrderType"], trade["Symbol"]])
    table.add_row(["Entry", trade["Entry"]])
    table.add_row(["Stop Loss", f"{stop_loss_pips} pips"])

    for i, p in enumerate(tp_pips):
        table.add_row([f"TP {i+1}", f"{p} pips"])

    table.add_row(["Risk Factor", f"{trade['RiskFactor'] * 100:.0f} %"])
    table.add_row(["Position Size (lots)", f"{trade.get('PositionSize', 0)}"])
    table.add_row(["Current Balance", f"$ {balance:,.2f}"])
    potential_loss = round(
        (trade.get("PositionSize", 0) * 10) * stop_loss_pips, 2)
    table.add_row(["Potential Loss", f"$ {potential_loss:,.2f}"])

    total_profit = 0.0
    for i, p in enumerate(tp_pips):
        profit = round((trade.get("PositionSize", 0) *
                       10 * (1 / len(tp_pips))) * p, 2)
        table.add_row([f"TP {i+1} Profit", f"$ {profit:,.2f}"])
        total_profit += profit

    table.add_row(["Total Profit", f"$ {total_profit:,.2f}"])

    return str(table)

# ---------- MetaTrader connection & trade logic (async) ----------


async def connect_and_process(update: Update, trade: Dict, enter_trade: bool):
    """
    Conecta via MetaApi y calcula/ejecuta trade según enter_trade boolean.
    """
    api = MetaApi(API_KEY)
    try:
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        # deploy si necesita
        if account.state not in ("DEPLOYED", "DEPLOYING"):
            logger.info("Deploying account...")
            await account.deploy()

        logger.info("Waiting for account to connect to broker...")
        await account.wait_connected()

        connection = account.get_rpc_connection()
        await connection.connect()
        logger.info("Waiting for SDK to synchronize to terminal state ...")
        await connection.wait_synchronized()

        account_info = await connection.get_account_information()
        await update.effective_message.reply_text("✅ Conectado a MetaTrader. Calculando riesgo ...")

        # si entry es NOW, obtener precio actual
        if isinstance(trade["Entry"], str) and trade["Entry"].upper() == "NOW":
            symbol_price = await connection.get_symbol_price(symbol=trade["Symbol"])
            if trade["OrderType"] == "Buy":
                trade["Entry"] = float(symbol_price["bid"])
            else:
                trade["Entry"] = float(symbol_price["ask"])

        # calcular pips y position size
        multiplier = _get_multiplier(trade["Symbol"], trade["Entry"])
        # prevenir division por cero
        if multiplier == 0:
            await update.effective_message.reply_text("Error interno: multiplier es 0.")
            return

        stop_loss_pips = abs(
            round((trade["StopLoss"] - trade["Entry"]) / multiplier))
        if stop_loss_pips == 0:
            await update.effective_message.reply_text("Stop loss calculado en 0 pips -> revisar valores de Entry/SL.")
            return

        # position size formula original adaptada y redondeada a 2 decimales
        pos_size = ((account_info["balance"] *
                    trade["RiskFactor"]) / stop_loss_pips) / 10
        pos_size = math.floor(pos_size * 100) / 100  # floor a 2 decimales
        trade["PositionSize"] = pos_size

        # take profit pips
        tp_pips = [abs(round((tp - trade["Entry"]) / multiplier))
                   for tp in trade["TP"]]

        # enviar tabla con info
        table_str = create_table(
            trade, account_info["balance"], stop_loss_pips, tp_pips)
        await update.effective_message.reply_text(f"<pre>{table_str}</pre>", parse_mode=ParseMode.HTML)

        # ejecutar trade si corresponde
        if enter_trade:
            await update.effective_message.reply_text("Entrando trade en MetaTrader...")
            try:
                results = []
                share = trade["PositionSize"] / max(1, len(trade["TP"]))
                if trade["OrderType"] == "Buy":
                    for tp in trade["TP"]:
                        res = await connection.create_market_buy_order(trade["Symbol"], share, trade["StopLoss"], tp)
                        results.append(res)
                elif trade["OrderType"] == "Sell":
                    for tp in trade["TP"]:
                        res = await connection.create_market_sell_order(trade["Symbol"], share, trade["StopLoss"], tp)
                        results.append(res)
                elif trade["OrderType"] == "Buy Limit":
                    for tp in trade["TP"]:
                        res = await connection.create_limit_buy_order(trade["Symbol"], share, trade["Entry"], trade["StopLoss"], tp)
                        results.append(res)
                elif trade["OrderType"] == "Sell Limit":
                    for tp in trade["TP"]:
                        res = await connection.create_limit_sell_order(trade["Symbol"], share, trade["Entry"], trade["StopLoss"], tp)
                        results.append(res)
                elif trade["OrderType"] == "Buy Stop":
                    for tp in trade["TP"]:
                        res = await connection.create_stop_buy_order(trade["Symbol"], share, trade["Entry"], trade["StopLoss"], tp)
                        results.append(res)
                elif trade["OrderType"] == "Sell Stop":
                    for tp in trade["TP"]:
                        res = await connection.create_stop_sell_order(trade["Symbol"], share, trade["Entry"], trade["StopLoss"], tp)
                        results.append(res)
                await update.effective_message.reply_text("✅ Trade ejecutado correctamente.")
                logger.info("Trade results: %s", results)
            except Exception as e:
                logger.exception("Error al ejecutar trade: %s", e)
                await update.effective_message.reply_text(f"Hubo un error al ejecutar el trade:\n{e}")

    except Exception as e:
        logger.exception("Error de conexión a MetaApi: %s", e)
        await update.effective_message.reply_text(f"Hubo un problema con la conexión a MetaTrader:\n{e}")

# ---------- Handlers (async) ----------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Welcome to the FX Signal Copier Telegram Bot! 💻💸\n\n"
        "Usá /help para ver instrucciones."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Comandos:\n"
        "/trade - Ingresar trade y ejecutar\n"
        "/calculate - Calcular tamaños y riesgos (no ejecuta)\n"
        "/cancel - Cancelar\n\n"
        "Formato ejemplo:\n"
        "BUY GBPUSD\nEntry NOW\nSL 1.14336\nTP 1.28930\nTP 1.29845\n\n"
        "Usá 'NOW' para market execution."
    )
    await update.effective_message.reply_text(help_text)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde a mensajes que no son parte del flujo si el usuario está autorizado."""
    username = update.effective_user.username or ""
    if username != TELEGRAM_USER:
        await update.effective_message.reply_text("No estás autorizado para usar este bot.")
        return
    await update.effective_message.reply_text("Comando desconocido. /help para instrucciones.")


async def trade_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pide el trade (estado TRADE)."""
    username = update.effective_user.username or ""
    if username != TELEGRAM_USER:
        await update.effective_message.reply_text("No estás autorizado para usar este bot.")
        return ConversationHandler.END
    context.user_data["trade"] = None
    await update.effective_message.reply_text("Por favor ingresá el trade (formato en /help).")
    return TRADE


async def calculate_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = update.effective_user.username or ""
    if username != TELEGRAM_USER:
        await update.effective_message.reply_text("No estás autorizado para usar este bot.")
        return ConversationHandler.END
    context.user_data["trade"] = None
    await update.effective_message.reply_text("Por favor ingresá el trade para calcular.")
    return CALCULATE


async def place_trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler para colocar trade (entrada final)."""
    if context.user_data.get("trade") is None:
        try:
            trade = parse_signal(update.effective_message.text)
            if not trade:
                raise ValueError("Invalid trade format")
            context.user_data["trade"] = trade
            await update.effective_message.reply_text("Trade parseado. Conectando a MetaTrader...")
        except Exception as e:
            logger.exception("Parse error: %s", e)
            await update.effective_message.reply_text(
                "Error al parsear el trade. Revisá el formato.\nEjemplo:\nBUY GBPUSD\nEntry NOW\nSL 1.14336\nTP 1.28930"
            )
            return TRADE

    # Ejecutar conexión y trade (enter_trade=True)
    await connect_and_process(update, context.user_data["trade"], enter_trade=True)
    context.user_data["trade"] = None
    return ConversationHandler.END


async def calculate_trade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler para calcular info pero NO ejecutar."""
    if context.user_data.get("trade") is None:
        try:
            trade = parse_signal(update.effective_message.text)
            if not trade:
                raise ValueError("Invalid trade format")
            context.user_data["trade"] = trade
            await update.effective_message.reply_text("Trade parseado. Conectando a MetaTrader para calcular...")
        except Exception as e:
            logger.exception("Parse error: %s", e)
            await update.effective_message.reply_text(
                "Error al parsear el trade. Revisá el formato.\nEjemplo:\nBUY GBPUSD\nEntry NOW\nSL 1.14336\nTP 1.28930"
            )
            return CALCULATE

    # Calcular (enter_trade=False)
    await connect_and_process(update, context.user_data["trade"], enter_trade=False)
    await update.effective_message.reply_text("¿Deseás entrar este trade? /yes o /no")
    return DECISION


async def yes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Si el usuario confirma, ejecuta el trade ya parseado (estaba en user_data)."""
    if context.user_data.get("trade") is None:
        await update.effective_message.reply_text("No hay trade pendiente. Reintentá con /trade o /calculate.")
        return ConversationHandler.END

    await connect_and_process(update, context.user_data["trade"], enter_trade=True)
    context.user_data["trade"] = None
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["trade"] = None
    await update.effective_message.reply_text("Acción cancelada.")
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Update caused error: %s", context.error)

# ---------- App / main ----------


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Comandos simples
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # Conversation handler (trade / calculate)
    conv = ConversationHandler(
        entry_points=[CommandHandler("trade", trade_entry), CommandHandler(
            "calculate", calculate_entry)],
        states={
            TRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, place_trade_handler)],
            CALCULATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, calculate_trade_handler)],
            DECISION: [CommandHandler("yes", yes_handler), CommandHandler("no", cancel_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        per_user=True,
    )
    app.add_handler(conv)

    # Mensajes no reconocidos
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, unknown_command))

    # Error handler
    app.add_error_handler(error_handler)

    # Ejecutar webhook (Render / Heroku)
    logger.info("Starting webhook...")
    # webhook: escuchamos en 0.0.0.0:PORT con path = TOKEN y webhook_url = APP_URL + TOKEN
    app.run_webhook(listen="0.0.0.0", port=PORT,
                    url_path=TOKEN, webhook_url=APP_URL + TOKEN)


if __name__ == "__main__":
    main()
