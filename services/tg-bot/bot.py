import os
import io
import logging
import httpx
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg') # FIX CRITICO: Impedisce a Matplotlib di bloccarsi aspettando un monitor grafico!
import matplotlib.pyplot as plt
from datetime import timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InputMediaPhoto, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from influxdb_client import InfluxDBClient

import asyncio

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://ml-inference:8000")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET = "sensor_data"

BOARD_MAP = {"1": "3750846324", "2": "3750866944"}
REVERSE_BOARD_MAP = {v: f"Board {k}" for k, v in BOARD_MAP.items()}
TZ_ROME = ZoneInfo("Europe/Rome")

# Stati per la conversazione What-If estesa
AWAIT_WHATIF_MODE, AWAIT_WHATIF_TASK, AWAIT_WHATIF_BOARD, AWAIT_WHATIF_VALUES = range(4)

# ==========================================
# FUNZIONI DATI E GRAFICI
# ==========================================

def calculate_vpd(df: pd.DataFrame) -> pd.DataFrame:
    if all(col in df.columns for col in ['air_temp', 'humidity', 'leaf_temp']):
        es_leaf = 0.61078 * np.exp((17.27 * df['leaf_temp']) / (df['leaf_temp'] + 237.3))
        es_air = 0.61078 * np.exp((17.27 * df['air_temp']) / (df['air_temp'] + 237.3))
        ea_air = es_air * (df['humidity'] / 100.0)
        df['vpd'] = es_leaf - ea_air
    return df

def calculate_future_vpd(temp_list, hum_list, leaf_temp_list):
    vpd_list = []
    for t, h, l in zip(temp_list, hum_list, leaf_temp_list):
        # Formula VPD
        es_leaf = 0.61078 * np.exp((17.27 * l) / (l + 237.3))
        es_air = 0.61078 * np.exp((17.27 * t) / (t + 237.3))
        ea_air = es_air * (h / 100.0)
        vpd_list.append(max(0, es_leaf - ea_air))
    return vpd_list

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
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            df.index = df.index.tz_convert(TZ_ROME)
            df = calculate_vpd(df)
        return df
    except Exception as e:
        logger.error(f"Errore recupero storico Influx: {e}")
        return pd.DataFrame()

def create_prediction_plot(df_hist: pd.DataFrame, processed_preds: list, is_whatif=False) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    
    if not df_hist.empty and 'leaf_temp' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['leaf_temp'])
        if not df_plot.empty:
            plt.plot(df_plot.index, df_plot['leaf_temp'], label='Storico Reale', color='green', linewidth=2)
            last_time = df_plot.index[-1]
    
    future_times = [p[0] for p in processed_preds]
    future_vals = [p[1] for p in processed_preds]
    
    # Se è un what-if, colleghiamo visivamente lo storico con la proiezione futura
    if is_whatif and not df_hist.empty:
        future_times = [last_time] + future_times
        # Prendi l'ultimo valore reale della foglia come starting point grafico
        future_vals = [df_plot['leaf_temp'].iloc[-1]] + future_vals

    plt.plot(future_times, future_vals, label='Proiezione', color='orange', linestyle='dashed', marker='o', linewidth=2)
    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Inizio Simulazione' if is_whatif else 'Adesso')

    title = 'Temperatura Fogliare: Simulazione What-If' if is_whatif else 'Temperatura Fogliare: Storico vs Predizione'
    plt.title(title)
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

def create_prediction_plot_tmp(df_hist: pd.DataFrame, series_dict: dict, title: str) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    
    # Plot storico se presente
    if not df_hist.empty and 'leaf_temp' in df_hist.columns:
        plt.plot(df_hist.index, df_hist['leaf_temp'], label='Storico', color='black', alpha=0.3, linewidth=1.5)

    # Plot dinamico delle serie
    for label, data in series_dict.items():
        # Parsing timestamp ISO in datetime
        times = [pd.to_datetime(d['timestamp']) for d in data]
        vals = [d['value'] for d in data]
        plt.plot(times, vals, label=label, marker='o', markersize=4, linestyle='--')

    plt.title(title)
    plt.xlabel('Orario')
    plt.ylabel('Valore')
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
        if not available_cols: continue
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
# CORE MENU & ANTI-SPAM LOCK
# ==========================================

async def setup_commands(application: Application):
    commands = [BotCommand("menu", "🎛 Apri il Pannello di Controllo")]
    await application.bot.set_my_commands(commands)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['is_processing'] = False 
    keyboard = [
        [InlineKeyboardButton("🔮 Avvia Predizione ML", callback_data="menu_predict")],
        [InlineKeyboardButton("📊 Visualizza Storico", callback_data="menu_history")],
        [InlineKeyboardButton("🧪 Simulazione What-If", callback_data="menu_whatif")]
    ]
    text = "🤖 **AgriBot - Centro di Controllo**\nScegli un'operazione:"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def interactive_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_history":
        keyboard = [
            [InlineKeyboardButton("3 Ore", callback_data="hist_3"), InlineKeyboardButton("6 Ore", callback_data="hist_6")],
            [InlineKeyboardButton("12 Ore", callback_data="hist_12"), InlineKeyboardButton("24 Ore", callback_data="hist_24")],
            [InlineKeyboardButton("⬅️ Indietro", callback_data="menu_main")]
        ]
        await query.edit_message_text("Seleziona l'arco temporale dello storico:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    elif data.startswith("hist_") and len(data.split("_")) == 2:
        hours = data.split("_")[1]
        keyboard = [[InlineKeyboardButton(f"🌿 Board {k}", callback_data=f"hist_{hours}_{k}")] for k in BOARD_MAP.keys()]
        keyboard.append([InlineKeyboardButton("⬅️ Indietro", callback_data="menu_history")])
        await query.edit_message_text("Seleziona la serra (Board):", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "menu_predict":
        keyboard = [
            [InlineKeyboardButton("🎲 Ensemble", callback_data="pred_ens")],
            [InlineKeyboardButton("🔬 Singolo Modello", callback_data="pred_std")],
            [InlineKeyboardButton("⬅️ Indietro", callback_data="menu_main")]
        ]
        await query.edit_message_text("Quale motore predittivo vuoi usare?", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    elif data == "pred_ens":
        keyboard = [
            [InlineKeyboardButton("Gruppo A (Usa TDS)", callback_data="pred_sel_ens_A")],
            [InlineKeyboardButton("Gruppo B (Senza TDS)", callback_data="pred_sel_ens_B")],
            [InlineKeyboardButton("⬅️ Indietro", callback_data="menu_predict")]
        ]
        await query.edit_message_text("Seleziona la strategia:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    elif data == "pred_std":
        keyboard = [
            [InlineKeyboardButton("T1 (Adesso)", callback_data="pred_sel_std_t1"), InlineKeyboardButton("T4 (No TDS)", callback_data="pred_sel_std_t4")],
            [InlineKeyboardButton("T2 (Amb. 3h)", callback_data="pred_sel_std_t2"), InlineKeyboardButton("T5 (Amb. No TDS)", callback_data="pred_sel_std_t5")],
            [InlineKeyboardButton("T3 (Auto 3h)", callback_data="pred_sel_std_t3"), InlineKeyboardButton("T6 (Auto No TDS)", callback_data="pred_sel_std_t6")],
            [InlineKeyboardButton("⬅️ Indietro", callback_data="menu_predict")]
        ]
        await query.edit_message_text("Scegli un Task specifico:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    elif data.startswith("pred_sel_"):
        mode = data.replace("pred_sel_", "")
        keyboard = [[InlineKeyboardButton(f"🌿 Board {k}", callback_data=f"pred_go_{mode}_{k}")] for k in BOARD_MAP.keys()]
        keyboard.append([InlineKeyboardButton("⬅️ Indietro", callback_data="menu_predict")])
        await query.edit_message_text("Su quale serra applichiamo la predizione?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "menu_main":
        await show_main_menu(update, context)
        return

    # Blocco Anti-Spam
    if context.user_data.get('is_processing'):
        await update.callback_query.answer("⏳ Un'operazione è già in corso! Attendi...", show_alert=True)
        return

    context.user_data['is_processing'] = True
    try:
        if data.startswith("hist_") and len(data.split("_")) == 3:
            hours, board_key = int(data.split("_")[1]), data.split("_")[2]
            await query.edit_message_text(f"📊 Generazione grafici per Board {board_key} ({hours}h)...")
            await process_history(update, BOARD_MAP[board_key], hours, query.message)

        elif data.startswith("pred_go_ens_"):
            group, board_key = data.split("_")[3], data.split("_")[4]
            await query.edit_message_text(f"🔄 Avvio motore ENSEMBLE ({group}) per Board {board_key}...")
            await process_prediction(update, "ensemble", group, BOARD_MAP[board_key], query.message)

        elif data.startswith("pred_go_std_"):
            task, board_key = data.split("_")[3], data.split("_")[4]
            await query.edit_message_text(f"🔄 Predizione Modello Singolo ({task.upper()}) per Board {board_key}...")
            await process_prediction(update, "standard", task, BOARD_MAP[board_key], query.message)
            
    except Exception as e:
        logger.error(f"Errore grave: {e}")
        await query.message.reply_text("⚠️ Si è verificato un errore critico.")
    finally:
        context.user_data['is_processing'] = False


# ==========================================
# FLUSSO CHATBOT PER "WHAT-IF" COMPLETO
# ==========================================

async def start_whatif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🎲 Ensemble", callback_data="whatif_mode_ensemble")],
        [InlineKeyboardButton("🔬 Singolo", callback_data="whatif_mode_standard")],
        [InlineKeyboardButton("❌ Annulla", callback_data="whatif_cancel")]
    ]
    await query.edit_message_text("🧪 **Simulazione What-If**\nSeleziona la tipologia di motore:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return AWAIT_WHATIF_MODE

async def choose_whatif_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "whatif_cancel":
        await show_main_menu(update, context)
        return ConversationHandler.END

    mode = query.data.split("_")[2] # ensemble o standard
    context.user_data['wi_mode'] = mode
    
    if mode == "ensemble":
        keyboard = [
            [InlineKeyboardButton("Gruppo A (Usa TDS)", callback_data="whatif_task_A")],
            [InlineKeyboardButton("Gruppo B (Senza TDS)", callback_data="whatif_task_B")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("T1", callback_data="whatif_task_t1"), InlineKeyboardButton("T4", callback_data="whatif_task_t4")],
            [InlineKeyboardButton("T2", callback_data="whatif_task_t2"), InlineKeyboardButton("T5", callback_data="whatif_task_t5")],
            [InlineKeyboardButton("T3", callback_data="whatif_task_t3"), InlineKeyboardButton("T6", callback_data="whatif_task_t6")],
        ]
    keyboard.append([InlineKeyboardButton("❌ Annulla", callback_data="whatif_cancel")])
    await query.edit_message_text("Quale configurazione testiamo?", reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAIT_WHATIF_TASK

async def choose_whatif_board(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "whatif_cancel":
        await show_main_menu(update, context)
        return ConversationHandler.END

    task = query.data.split("_")[2]
    context.user_data['wi_task'] = task

    keyboard = [[InlineKeyboardButton(f"🌿 Board {k}", callback_data=f"whatif_board_{k}")] for k in BOARD_MAP.keys()]
    keyboard.append([InlineKeyboardButton("❌ Annulla", callback_data="whatif_cancel")])
    await query.edit_message_text("Seleziona la serra su cui applicare il contesto:", reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAIT_WHATIF_BOARD

async def whatif_ask_values(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "whatif_cancel":
        await show_main_menu(update, context)
        return ConversationHandler.END

    board_key = query.data.split("_")[2]
    context.user_data['wi_board'] = BOARD_MAP[board_key]

    text = (
        f"✅ Contesto: **{context.user_data['wi_mode'].upper()} {context.user_data['wi_task'].upper()}** su **Board {board_key}**.\n\n"
        "Scrivimi i **7 valori** (separati da spazio):\n"
        "`[Aria] [Umidità] [Pressione] [Temp. Acqua] [TDS] [Umidità Suolo] [Luminosità]`\n\n"
        "📝 _Esempio:_\n`25.5 60 1013 22.0 400 45 10000`\n"
        "_(Scrivi /annulla per uscire)_"
    )
    await query.edit_message_text(text, parse_mode='Markdown')
    return AWAIT_WHATIF_VALUES

async def process_whatif_values(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        vals = [float(x.strip()) for x in text.split()]
        if len(vals) != 7: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Formato errato (Servono 7 numeri. Usa il punto per i decimali). Riprova:")
        return AWAIT_WHATIF_VALUES

    wait_msg = await update.message.reply_text("🧪 Contatto il Server ML per la simulazione...")
    payload = {
        "air_temp": vals[0], "humidity": vals[1], "pressure": vals[2],
        "water_temp": vals[3], "tds": vals[4], "soil_moisture": vals[5], "light_lux": vals[6]
    }

    mode, task, board_id = context.user_data['wi_mode'], context.user_data['wi_task'], context.user_data['wi_board']
    endpoint = f"{INFERENCE_URL}/predict/6m/{mode}/{task}/manual?board_id={board_id}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=90.0)
            
        if response.status_code == 200:
            data = response.json()
            raw_preds = data.get("forecast_blended", data.get("predictions", []))
            
            df_hist = await asyncio.to_thread(fetch_history_data, board_id, 3)
            last_dt = df_hist.index[-1] if not df_hist.empty else pd.Timestamp.now(tz=TZ_ROME)
            
            processed_preds = []
            for i, p in enumerate(raw_preds):
                if isinstance(p, dict):
                    dt = pd.to_datetime(p["timestamp"]).astimezone(TZ_ROME)
                    val = p["value"]
                else:
                    dt = last_dt + timedelta(minutes=6 * (i + 1))
                    val = p
                processed_preds.append((dt, val))

            # Creazione Plot Speciale What-If
            photo_buf = create_prediction_plot(df_hist, processed_preds, is_whatif=True)

            lines = [f"🕒 {dt.strftime('%H:%M')} ➔ **{val}°C**" for i, (dt, val) in enumerate(processed_preds) if (i+1) % 5 == 0]
            await update.get_bot().send_photo(
                chat_id=wait_msg.chat_id, photo=photo_buf, 
                caption=f"🧪 **Risultato Simulazione ({mode.upper()} {task.upper()})**\n\n_Snapshot future (ogni 30m):_\n" + "\n".join(lines), 
                parse_mode='Markdown'
            )
            await wait_msg.delete()
        else:
            await wait_msg.edit_text(f"⚠️ Errore API: {response.text}")
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Errore di rete o Timeout.")

    await show_main_menu(update, context)
    return ConversationHandler.END

async def cancel_whatif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Simulazione annullata.")
    await show_main_menu(update, context)
    return ConversationHandler.END

# ==========================================
# ESECUTORI LOGICI (REST DI RETE)
# ==========================================

async def process_history(update: Update, board_id: str, hours: int, wait_message):
    df_hist = await asyncio.to_thread(fetch_history_data, board_id, hours)
    if df_hist.empty:
        await wait_message.edit_text("⚠️ Nessun dato presente nel DB Influx.")
        return
    plots = create_semantic_category_plots(df_hist)
    await update.get_bot().send_media_group(chat_id=wait_message.chat_id, media=[InputMediaPhoto(media=b) for b in plots])
    await wait_message.delete()

async def process_prediction(update: Update, mode: str, task_or_group: str, board_id: str, wait_message, freq_min: int = 6):
    endpoint = f"{INFERENCE_URL}/predict/{freq_min}m/{mode}/{task_or_group}/latest?board_id={board_id}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, timeout=120.0) 
            
        if response.status_code == 200:
            data = response.json()
            
            # 1. Estrazione Serie
            blended = data.get("forecast_blended", [])
            env = data.get("forecast_env", [])
            auto = data.get("forecast_auto", [])
            arima_proj = data.get("arima_projections", {})
            
            df_hist = await asyncio.to_thread(fetch_history_data, board_id, 3)
            
            # 2. Calcolo VPD Futuro (se abbiamo i dati ARIMA)
            future_vpd = []
            if arima_proj and blended:
                air_t = [x['value'] for x in arima_proj['air_temp']]
                hum = [x['value'] for x in arima_proj['humidity']]
                leaf_t = [x['value'] for x in blended]
                vpd_vals = calculate_future_vpd(air_t, hum, leaf_t)
                
                # Creiamo la lista per il plot VPD
                for i, vpd in enumerate(vpd_vals):
                    future_vpd.append({"timestamp": blended[i]['timestamp'], "value": vpd})

            # 3. Preparazione Plot
            series_temp = {"Blended": blended, "Env": env, "Auto": auto}
            plot_temp = create_prediction_plot_tmp(df_hist, series_temp, f"Predizione Temp. {task_or_group.upper()}")
            
            plots = [InputMediaPhoto(media=plot_temp)]
            
            # Aggiungiamo plot VPD se calcolato
            if future_vpd:
                plot_vpd = create_prediction_plot_tmp(pd.DataFrame(), {"VPD": future_vpd}, "Proiezione VPD Futuro (kPa)")
                plots.append(InputMediaPhoto(media=plot_vpd))

            # 4. Invio
            await update.get_bot().send_media_group(chat_id=wait_message.chat_id, media=plots)
            await wait_message.delete()
        else:
            await wait_message.edit_text(f"⚠️ **Errore del Server:** {response.text}")
    except Exception as e:
        logger.error(f"Errore in process_prediction: {e}")
        await wait_message.edit_text("⚠️ **Timeout o Errore di Rete.**")

    
def main():
    if not TOKEN: return logger.error("TELEGRAM_BOT_TOKEN mancante nel file .env!")
    application = Application.builder().token(TOKEN).post_init(setup_commands).build()
    
    application.add_handler(CommandHandler(["start", "menu"], show_main_menu))
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_whatif, pattern='^menu_whatif$')],
        states={
            AWAIT_WHATIF_MODE: [CallbackQueryHandler(choose_whatif_task, pattern='^(whatif_mode_|whatif_cancel)')],
            AWAIT_WHATIF_TASK: [CallbackQueryHandler(choose_whatif_board, pattern='^(whatif_task_|whatif_cancel)')],
            AWAIT_WHATIF_BOARD: [CallbackQueryHandler(whatif_ask_values, pattern='^(whatif_board_|whatif_cancel)')],
            AWAIT_WHATIF_VALUES: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_whatif_values)]
        },
        fallbacks=[CommandHandler('annulla', cancel_whatif)]
    )
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(interactive_callbacks))

    logger.info("AgriBot in ascolto...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()