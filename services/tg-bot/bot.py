import os
import io
import logging
import httpx
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InputMediaPhoto, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from influxdb_client import InfluxDBClient

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://ml-inference:8000")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET = "sensor_data"

# Mappatura Semplificata Board
BOARD_MAP = {
    "1": "3750846324",
    "2": "3750866944"
}
REVERSE_BOARD_MAP = {v: f"Board {k}" for k, v in BOARD_MAP.items()}
DEFAULT_BOARD = BOARD_MAP["1"]
TZ_ROME = ZoneInfo("Europe/Rome")


def calculate_vpd(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcola il Leaf Vapor Pressure Deficit (VPD) in kPa.
    Utilizza la temperatura della foglia (se disponibile) per la saturazione,
    e i parametri dell'aria per la pressione di vapore attuale.
    """
    if all(col in df.columns for col in ['air_temp', 'humidity', 'leaf_temp']):
        # Pressione di Vapore Saturo (SVP) basata sulla temperatura fogliare
        es_leaf = 0.61078 * np.exp((17.27 * df['leaf_temp']) / (df['leaf_temp'] + 237.3))
        # Pressione di Vapore Saturo dell'aria
        es_air = 0.61078 * np.exp((17.27 * df['air_temp']) / (df['air_temp'] + 237.3))
        # Pressione di Vapore Attuale (AVP)
        ea_air = es_air * (df['humidity'] / 100.0)
        
        # Deficit di Pressione Vapore della Foglia (VPDL)
        df['vpd'] = es_leaf - ea_air
    return df


def fetch_history_data(board_id: str, hours: int) -> pd.DataFrame:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if len(df) == 0: return pd.DataFrame()
            df = pd.concat(df, ignore_index=True)
        if not df.empty:
            df.set_index('_time', inplace=True)
            df.sort_index(inplace=True)
            
            # GESTIONE FUSO ORARIO E LOCALE (Da UTC a Roma)
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            df.index = df.index.tz_convert(TZ_ROME)
            
            # Aggiunta calcolo VPD
            df = calculate_vpd(df)
            
        return df
    except Exception as e:
        logger.error(f"Errore recupero storico Influx: {e}")
        return pd.DataFrame()


def create_prediction_plot(df_hist: pd.DataFrame, processed_preds: list) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    
    if not df_hist.empty and 'leaf_temp' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['leaf_temp'])
        if not df_plot.empty:
            plt.plot(df_plot.index, df_plot['leaf_temp'], label='Storico Reale', color='green', linewidth=2)
            last_time = df_plot.index[-1]
    
    future_times = [p[0] for p in processed_preds]
    future_vals = [p[1] for p in processed_preds]
    
    plt.plot(future_times, future_vals, label='Predizione Modello', color='orange', linestyle='dashed', marker='o', linewidth=2)
    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Adesso')

    plt.title('Temperatura Fogliare: Storico vs Predizione Futura')
    plt.xlabel('Orario (Locale)')
    plt.ylabel('Temperatura (°C)')
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf


def create_semantic_category_plots(df_hist: pd.DataFrame) -> list[io.BytesIO]:
    plots = []
    categories = {
        "Temperature (°C)": (['air_temp', 'leaf_temp', 'water_temp'], ['red', 'green', 'blue']),
        "Luminosità (Lux)": (['light_lux'], ['orange']),
        "Pressione (hPa)": (['pressure'], ['purple']),
        "Umidità e Umidità Suolo (%)": (['humidity', 'soil_moisture'], ['cyan', 'brown']),
        "Qualità Acqua (TDS - ppm)": (['tds'], ['olive']),
        "VPD - Deficit Pressione Vapore (kPa)": (['vpd'], ['magenta'])
    }

    for title, (columns, colors) in categories.items():
        available_cols = [c for c in columns if c in df_hist.columns]
        if not available_cols:
            continue
            
        plt.figure(figsize=(10, 4))
        for idx, col in enumerate(available_cols):
            df_plot = df_hist.dropna(subset=[col])
            if not df_plot.empty:
                plt.plot(df_plot.index, df_plot[col], label=col, color=colors[idx % len(colors)], linewidth=2)
        
        plt.title(title)
        plt.xlabel('Orario (Locale)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.xticks(rotation=45)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        plots.append(buf)
        
    return plots


# ==========================================
# GESTIONE COMANDI E MENU INTERATTIVO
# ==========================================

async def setup_commands(application: Application):
    commands = [
        BotCommand("start", "Messaggio di benvenuto"),
        BotCommand("menu", "🎛 Pannello di Controllo Interattivo"),
        BotCommand("predict", "Esegui predizione rapida di default"),
        BotCommand("history", "Mostra storico di default (6h)"),
        BotCommand("manual", "Predizione manuale (simulazione what-if)")
    ]
    await application.bot.set_my_commands(commands)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **AgriBot - Pannello di Controllo**\n\n"
        "💡 **Modo Interattivo:** Usa /menu per farti guidare nelle scelte!\n\n"
        "⚡ **Comandi Rapidi (Default):**\n"
        "🔹 `/predict` - Predizione (Ensemble Gruppo B, Board 1, 6m)\n"
        "🔹 `/history` - Grafici storici (Board 1, ultime 6 ore)\n"
        "🔹 `/manual [t1|t2..] [air_t] [hum] [press] [wat_t] [tds] [soil] [lux]`\n"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

# --- MENU INTERATTIVO (CALLBACKS) ---

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔮 Avvia Predizione", callback_data="menu_predict")],
        [InlineKeyboardButton("📊 Visualizza Storico", callback_data="menu_history")]
    ]
    await update.message.reply_text("🎛 **Scegli l'operazione:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def interactive_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- FLUSSO STORICO ---
    if data == "menu_history":
        keyboard = [
            [InlineKeyboardButton("3 Ore", callback_data="hist_3"), InlineKeyboardButton("6 Ore", callback_data="hist_6")],
            [InlineKeyboardButton("12 Ore", callback_data="hist_12"), InlineKeyboardButton("24 Ore", callback_data="hist_24")]
        ]
        await query.edit_message_text("Seleziona l'arco temporale:", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("hist_"):
        parts = data.split("_")
        if len(parts) == 2: # Scelto orario, chiedi board
            hours = parts[1]
            keyboard = [[InlineKeyboardButton(f"🌿 Board {k}", callback_data=f"hist_{hours}_{k}")] for k in BOARD_MAP.keys()]
            await query.edit_message_text("Seleziona la Board:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif len(parts) == 3: # Esegui storico
            hours = int(parts[1])
            board_key = parts[2]
            board_id = BOARD_MAP[board_key]
            await query.edit_message_text(f"📊 Generazione grafici per Board {board_key} ({hours}h) in corso...")
            await process_history(update, board_id, hours, query.message)

    # --- FLUSSO PREDIZIONE ---
    elif data == "menu_predict":
        keyboard = [
            [InlineKeyboardButton("🎲 Ensemble (Consigliato)", callback_data="pred_ens")],
            [InlineKeyboardButton("🔬 Singolo Task (Standard)", callback_data="pred_std")]
        ]
        await query.edit_message_text("Seleziona il motore di predizione:", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data == "pred_ens":
        keyboard = [
            [InlineKeyboardButton("Gruppo A (con TDS)", callback_data="pred_go_ens_A")],
            [InlineKeyboardButton("Gruppo B (Senza TDS)", callback_data="pred_go_ens_B")]
        ]
        await query.edit_message_text("Seleziona il Gruppo Ensemble:", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data == "pred_std":
        keyboard = [
            [InlineKeyboardButton("T1 (Adesso)", callback_data="pred_go_std_t1"), InlineKeyboardButton("T4 (Adesso, No TDS)", callback_data="pred_go_std_t4")],
            [InlineKeyboardButton("T2 (Ambiente 3h)", callback_data="pred_go_std_t2"), InlineKeyboardButton("T5 (Ambiente 3h, No TDS)", callback_data="pred_go_std_t5")],
            [InlineKeyboardButton("T3 (Auto 3h)", callback_data="pred_go_std_t3"), InlineKeyboardButton("T6 (Auto 3h, No TDS)", callback_data="pred_go_std_t6")]
        ]
        await query.edit_message_text("Seleziona il Task Specifico:", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("pred_go_"):
        # La stringa è es: "pred_go_ens_B" o "pred_go_std_t3"
        # Chiediamo la board per finalizzare
        mode = data
        keyboard = [[InlineKeyboardButton(f"🌿 Board {k}", callback_data=f"{mode}_{k}")] for k in BOARD_MAP.keys()]
        await query.edit_message_text("Scegli la Board target:", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("pred_go_ens_") and len(data.split("_")) == 5:
        # Esecuzione ENSEMBLE
        parts = data.split("_")
        group = parts[3]
        board_key = parts[4]
        await query.edit_message_text(f"🔄 Elaborazione predizione ENSEMBLE ({group}) per Board {board_key}...")
        await process_prediction(update, "ensemble", group, BOARD_MAP[board_key], query.message)

    elif data.startswith("pred_go_std_") and len(data.split("_")) == 5:
        # Esecuzione STANDARD
        parts = data.split("_")
        task = parts[3]
        board_key = parts[4]
        await query.edit_message_text(f"🔄 Elaborazione predizione STANDARD ({task.upper()}) per Board {board_key}...")
        await process_prediction(update, "standard", task, BOARD_MAP[board_key], query.message)


# ==========================================
# ESECUTORI LOGICI (CORE)
# ==========================================

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando rapido di default"""
    wait_message = await update.message.reply_text("📊 Recupero lo storico di default (Ultime 6h, Board 1)...")
    await process_history(update, DEFAULT_BOARD, 6, wait_message)

async def predict_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando rapido di default"""
    wait_message = await update.message.reply_text("🔄 Elaborazione predizione di default (Ensemble B, Board 1)...")
    await process_prediction(update, "ensemble", "B", DEFAULT_BOARD, wait_message)


async def process_history(update: Update, board_id: str, hours: int, wait_message):
    df_hist = fetch_history_data(board_id, hours)
    if df_hist.empty:
        await wait_message.edit_text("⚠️ Nessun dato trovato per questa board nel periodo selezionato.")
        return

    plots = create_semantic_category_plots(df_hist)
    if not plots:
        await wait_message.edit_text("⚠️ Dati presenti ma insufficienti per generare i grafici.")
        return

    media_group = [InputMediaPhoto(media=buf) for buf in plots]
    # Reply al messaggio originale
    chat_id = wait_message.chat_id
    await update.get_bot().send_media_group(chat_id=chat_id, media=media_group)
    await wait_message.delete()


async def process_prediction(update: Update, mode: str, task_or_group: str, board_id: str, wait_message, freq_min: int = 6):
    try:
        if mode == "ensemble":
            endpoint = f"{INFERENCE_URL}/predict/{freq_min}m/ensemble/{task_or_group}/latest?board_id={board_id}"
        else:
            endpoint = f"{INFERENCE_URL}/predict/{freq_min}m/standard/{task_or_group}/latest?board_id={board_id}"
            
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, timeout=30.0)
            
        if response.status_code == 200:
            data = response.json()
            
            # Normalizzazione risposta (l'Ensemble ritorna dicts, lo Standard potrebbe ritornare float o dicts)
            if "forecast_blended" in data:
                raw_preds = data["forecast_blended"]
            else:
                raw_preds = data.get("predictions", [])

            df_hist = fetch_history_data(board_id, hours=3)
            last_dt = df_hist.index[-1] if not df_hist.empty else pd.Timestamp.now(tz=TZ_ROME)
            
            # Parsing UTC -> Local Time (Roma)
            processed_preds = []
            for i, p in enumerate(raw_preds):
                if isinstance(p, dict):
                    dt = pd.to_datetime(p["timestamp"]).astimezone(TZ_ROME)
                    val = p["value"]
                else:
                    dt = last_dt + timedelta(minutes=freq_min * (i + 1))
                    val = p
                processed_preds.append((dt, val))

            # Creazione Plot
            photo_buf = create_prediction_plot(df_hist, processed_preds)
            
            # Formattazione Testo
            pred_lines = []
            for i, (dt, val) in enumerate(processed_preds):
                time_str = dt.strftime('%H:%M')
                if len(processed_preds) > 10:
                    if (i + 1) % 5 == 0:  # Ogni 30 min se freq=6
                        pred_lines.append(f"🕒 {time_str} ➔ **{val}°C**")
                else:
                    pred_lines.append(f"🕒 {time_str} ➔ **{val}°C**")

            header = "_Snapshot predittivo (ogni 30 min):_\n" if len(processed_preds) > 10 else ""
            pred_text = header + "\n".join(pred_lines)
            
            b_name = REVERSE_BOARD_MAP.get(board_id, board_id)
            messaggio_finale = f"🌿 **Predizione Fogliare ({mode.upper()} {task_or_group.upper()})**\n📍 {b_name}\n\n{pred_text}"
            
            await update.get_bot().send_photo(chat_id=wait_message.chat_id, photo=photo_buf, caption=messaggio_finale, parse_mode='Markdown')
            await wait_message.delete()
        else:
            err = response.json().get("detail", "Errore sconosciuto")
            await wait_message.edit_text(f"⚠️ **Errore API:** {err}", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Errore /predict elaborazione: {e}")
        await wait_message.edit_text("⚠️ Errore imprevisto di connessione o analisi.")


def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN mancante nel file .env! Esco.")
        return

    logger.info("Avvio di AgriBot in corso...")
    application = Application.builder().token(TOKEN).post_init(setup_commands).build()
    
    application.add_handler(CommandHandler("start", help_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("predict", predict_cmd))
    application.add_handler(CommandHandler("history", history_cmd))
    
    # Handler per i bottoni interattivi
    application.add_handler(CallbackQueryHandler(interactive_callbacks))

    logger.info("Bot in ascolto...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()