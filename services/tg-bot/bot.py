import os
import io
import logging
import asyncio
import httpx
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # CRITICAL FIX: Prevents Matplotlib from blocking awaiting a display!
import matplotlib.pyplot as plt

from datetime import timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InputMediaPhoto, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from influxdb_client import InfluxDBClient

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
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

# Conversation States for the extended What-If flow
AWAIT_WHATIF_MODE, AWAIT_WHATIF_TASK, AWAIT_WHATIF_BOARD, AWAIT_WHATIF_VALUES = range(4)

# ==========================================
# UTILITY HELPER FUNCTIONS (DRY)
# ==========================================

async def fetch_inference_api(endpoint: str, payload: dict = None, timeout: float = 120.0) -> dict:
    url = f"{INFERENCE_URL}{endpoint}"
    try:
        async with httpx.AsyncClient() as client:
            if payload:
                response = await client.post(url, json=payload, timeout=timeout)
            else:
                response = await client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"API Error at {url}: {e}")
        return {}

def build_keyboard(buttons: list[list[tuple[str, str]]], back_data: str = None) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in buttons]
    if back_data:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=back_data)])
    return InlineKeyboardMarkup(keyboard)

def _finalize_and_save_plot(title: str, xlabel: str = 'Time (Local)', ylabel: str = 'Value') -> io.BytesIO:
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf

# ==========================================
# DATA & MATH PROCESSING
# ==========================================

def calculate_vpd_array(air_temp, humidity, leaf_temp) -> float:
    es_leaf = 0.61078 * np.exp((17.27 * leaf_temp) / (leaf_temp + 237.3))
    es_air = 0.61078 * np.exp((17.27 * air_temp) / (air_temp + 237.3))
    ea_air = es_air * (humidity / 100.0)
    return max(0, es_leaf - ea_air)

def calculate_vpd(df: pd.DataFrame) -> pd.DataFrame:
    if all(col in df.columns for col in ['air_temp', 'humidity', 'leaf_temp']):
        df['vpd'] = df.apply(lambda row: calculate_vpd_array(row['air_temp'], row['humidity'], row['leaf_temp']), axis=1)
    return df

def calculate_future_vpd(temp_list: list, hum_list: list, leaf_temp_list: list) -> list:
    return [calculate_vpd_array(t, h, l) for t, h, l in zip(temp_list, hum_list, leaf_temp_list)]

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
            if not df: return pd.DataFrame()
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
        logger.error(f"InfluxDB history fetch error: {e}")
        return pd.DataFrame()

# ==========================================
# PLOTTING FUNCTIONS
# ==========================================

def create_series_plot(df_hist: pd.DataFrame, series_dict: dict, title: str, hide_real_history: bool = False) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    
    if not df_hist.empty and 'leaf_temp' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['leaf_temp'])
        if not df_plot.empty:
            last_time = df_plot.index[-1]
            if not hide_real_history:
                plt.plot(df_plot.index, df_plot['leaf_temp'], label='Real History', color='black', alpha=0.4, linewidth=2)

    styles = {
        "Blended (Final)": {"color": "blue", "linewidth": 2.5, "marker": "o", "markersize": 6, "alpha": 1.0, "zorder": 5},
        "Environment (Env)": {"color": "orange", "linewidth": 1.5, "linestyle": "--", "marker": "x", "markersize": 6, "alpha": 0.8},
        "Autoregressive (Auto)": {"color": "green", "linewidth": 1.5, "linestyle": "--", "marker": "s", "markersize": 5, "alpha": 0.8},
        "T1/T4 Est. History (Soft Sensor)": {"color": "purple", "linewidth": 2.5, "linestyle": "-", "alpha": 0.8},
        "Standard Prediction": {"color": "red", "linewidth": 2.0, "linestyle": "--", "marker": "o", "markersize": 5},
        "What-If Projection": {"color": "orange", "linewidth": 2.0, "linestyle": "dashed", "marker": "o", "markersize": 5},
        "Air Temp Forecast (°C)": {"color": "red", "linewidth": 1.5, "linestyle": ":", "marker": "."},
        "Humidity Forecast (%)": {"color": "cyan", "linewidth": 1.5, "linestyle": ":", "marker": "."}
    }

    for label, data in series_dict.items():
        if not data: continue
        times = [pd.to_datetime(d['timestamp']).astimezone(TZ_ROME) for d in data]
        vals = [d['value'] for d in data]
        
        # Visually connect future projections to the last known point
        if "History" not in label and "Forecast" not in label and last_time and not df_hist.empty:
            times = [last_time] + times
            vals = [df_plot['leaf_temp'].iloc[-1]] + vals

        style = styles.get(label, {"marker": "o", "markersize": 4, "linestyle": "--"})
        plt.plot(times, vals, label=label, **style)

    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Now')
    return _finalize_and_save_plot(title)

def create_vpd_plot(df_hist: pd.DataFrame, future_vpd: list = None) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    has_data = False
    
    if not df_hist.empty and 'vpd' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['vpd'])
        if not df_plot.empty:
            plt.plot(df_plot.index, df_plot['vpd'], label='Historical VPD', color='magenta', linewidth=2)
            last_time = df_plot.index[-1]
            has_data = True

    if future_vpd:
        times = [pd.to_datetime(d['timestamp']).astimezone(TZ_ROME) for d in future_vpd]
        vals = [d['value'] for d in future_vpd]
        if last_time and not df_hist.empty:
            times = [last_time] + times
            vals = [df_plot['vpd'].iloc[-1]] + vals
        plt.plot(times, vals, label='Future VPD Projection', color='purple', linestyle='--', marker='o', markersize=4)
        has_data = True

    if not has_data:
        plt.text(0.5, 0.5, 'VPD Data Unavailable', horizontalalignment='center', verticalalignment='center', transform=plt.gca().transAxes)

    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Now')
    return _finalize_and_save_plot("Vapor Pressure Deficit (VPD)", ylabel="VPD (kPa)")

def create_semantic_category_plots(df_hist: pd.DataFrame) -> list[io.BytesIO]:
    plots = []
    categories = {
        "Temperatures (°C)": (['air_temp', 'leaf_temp', 'water_temp'], ['red', 'green', 'blue']),
        "Luminosity (Lux)": (['light_lux'], ['orange']),
        "Pressure (hPa)": (['pressure'], ['purple']),
        "Humidity & Soil Moisture (%)": (['humidity', 'soil_moisture'], ['cyan', 'brown']),
        "Water Quality (TDS - ppm)": (['tds'], ['olive'])
    }
    
    for title, (columns, colors) in categories.items():
        available_cols = [c for c in columns if c in df_hist.columns]
        if not available_cols: continue
        plt.figure(figsize=(10, 4))
        for idx, col in enumerate(available_cols):
            df_plot = df_hist.dropna(subset=[col])
            if not df_plot.empty:
                plt.plot(df_plot.index, df_plot[col], label=col, color=colors[idx % len(colors)], linewidth=2)
        plots.append(_finalize_and_save_plot(title))
        
    plots.append(create_vpd_plot(df_hist))
    return plots

# ==========================================
# TELEGRAM BOT HANDLERS & MENUS
# ==========================================

async def setup_commands(application: Application):
    await application.bot.set_my_commands([BotCommand("menu", "🎛 Open Control Panel")])

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['is_processing'] = False 
    keyboard = build_keyboard([
        [("🔮 Start ML Prediction", "menu_predict")],
        [("📊 View History", "menu_history")],
        [("🧪 What-If Simulation", "menu_whatif")]
    ])

    text = "🤖 **GJ greenhouse - Control Center**\nSelect an operation:"

    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')
    else:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "menu_history":
        keyboard = build_keyboard([[("3 Hours", "hist_3"), ("6 Hours", "hist_6")], [("12 Hours", "hist_12"), ("24 Hours", "hist_24")]], "menu_main")
        await query.edit_message_text("Select the history timeframe:", reply_markup=keyboard)
    
    elif query.data == "menu_predict":
        keyboard = build_keyboard([[("🤝🏻 Ensemble Model", "pred_ens")], [("🧍🏻‍♂️ Single Model", "pred_std")]], "menu_main")
        await query.edit_message_text("Which predictive engine do you want to use?", reply_markup=keyboard)
    
    elif query.data == "menu_main":
        await show_main_menu(update, context)

async def _check_spam_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Prevents users from spamming heavy backend processes."""
    if context.user_data.get('is_processing'):
        await update.callback_query.answer("⏳ An operation is already in progress! Please wait...", show_alert=True)
        return True
    context.user_data['is_processing'] = True
    return False

async def handle_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) == 2:
        buttons = [[(f"🌿 Board {k} ({BOARD_MAP[k]})", f"hist_{parts[1]}_{k}")] for k in BOARD_MAP.keys()]
        await query.edit_message_text("Select the greenhouse (Board):", reply_markup=build_keyboard(buttons, "menu_history"))
    elif len(parts) == 3:
        if await _check_spam_lock(update, context): return
        try:
            hours, board_key = int(parts[1]), parts[2]
            await query.edit_message_text(f"📊 Generating charts for Board {board_key} ({hours}h)...")
            await process_history(update, BOARD_MAP[board_key], hours, query.message)
        finally:
            context.user_data['is_processing'] = False 

async def handle_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "pred_ens":
        keyboard = build_keyboard([[("Group A (Uses TDS)", "pred_sel_ens_A")], [("Group B (No TDS)", "pred_sel_ens_B")]], "menu_predict")
        await query.edit_message_text("Select the strategy:", reply_markup=keyboard)

    elif data == "pred_std":
        keyboard = build_keyboard([
            [("T1 (Now)", "pred_sel_std_t1"), ("T4 (No TDS)", "pred_sel_std_t4")],
            [("T2 (Env. 3h)", "pred_sel_std_t2"), ("T5 (Env. No TDS)", "pred_sel_std_t5")],
            [("T3 (Auto 3h)", "pred_sel_std_t3"), ("T6 (Auto No TDS)", "pred_sel_std_t6")]
        ], "menu_predict")
        await query.edit_message_text("Choose a specific Task:", reply_markup=keyboard)

    elif data.startswith("pred_sel_"):
        mode = data.replace("pred_sel_", "")
        buttons = [[(f"🌿 Board {k} ({BOARD_MAP[k]})", f"pred_go_{mode}_{k}")] for k in BOARD_MAP.keys()]
        await query.edit_message_text("Which greenhouse (Board) should we apply the prediction to?", reply_markup=build_keyboard(buttons, "menu_predict"))

    elif data.startswith("pred_go_"):
        if await _check_spam_lock(update, context): return
        try:
            _, _, type_mod, param, board_key = data.split("_")
            mode = "ensemble" if type_mod == "ens" else "standard"
            await query.edit_message_text(f"🔄 Starting {mode.upper()} engine ({param}) for Board {board_key}...")
            await process_prediction(update, mode, param, BOARD_MAP[board_key], query.message)
        finally:
            context.user_data['is_processing'] = False

# ==========================================
# CHATBOT FLOW FOR "WHAT-IF"
# ==========================================

async def start_whatif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = build_keyboard([
        [("🤝🏻 Ensemble Model", "whatif_mode_ensemble")],
        [("🧍🏻‍♂️ Single Model", "whatif_mode_standard")],
        [("❌ Cancel", "whatif_cancel")]
    ])
    await query.edit_message_text("🧪 **What-If Simulation**\nSelect the engine type:", reply_markup=keyboard, parse_mode='Markdown')
    return AWAIT_WHATIF_MODE

async def choose_whatif_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "whatif_cancel":
        await show_main_menu(update, context)
        return ConversationHandler.END

    mode = query.data.split("_")[2] # ensemble or standard
    context.user_data['wi_mode'] = mode
    
    if mode == "ensemble":
        keyboard = build_keyboard([
            [("Group A (Uses TDS)", "whatif_task_A")],
            [("Group B (No TDS)", "whatif_task_B")]
        ], "whatif_cancel")
    else:
        keyboard = build_keyboard([
            [("T1", "whatif_task_t1"), ("T4", "whatif_task_t4")],
            [("T2", "whatif_task_t2"), ("T5", "whatif_task_t5")],
            [("T3", "whatif_task_t3"), ("T6", "whatif_task_t6")]
        ], "whatif_cancel")
        
    await query.edit_message_text("Which configuration should we test?", reply_markup=keyboard)
    return AWAIT_WHATIF_TASK

async def choose_whatif_board(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "whatif_cancel":
        await show_main_menu(update, context)
        return ConversationHandler.END

    task = query.data.split("_")[2]
    context.user_data['wi_task'] = task

    buttons = [[(f"🌿 Board {k}", f"whatif_board_{k}")] for k in BOARD_MAP.keys()]
    await query.edit_message_text("Select the greenhouse to apply the context to:", reply_markup=build_keyboard(buttons, "whatif_cancel"))
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
        f"✅ Context: **{context.user_data['wi_mode'].upper()} {context.user_data['wi_task'].upper()}** on **Board {board_key}**.\n\n"
        "Please provide the **7 values** (separated by spaces):\n"
        "`[Air Temp] [Humidity] [Pressure] [Water Temp] [TDS] [Soil Moisture] [Luminosity]`\n\n"
        "📝 _Example:_\n`25.5 60 1013 22.0 400 45 10000`\n"
        "_(Type /cancel to exit)_"
    )
    await query.edit_message_text(text, parse_mode='Markdown')
    return AWAIT_WHATIF_VALUES

async def process_whatif_values(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        vals = [float(x.strip()) for x in text.split()]
        if len(vals) != 7: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Invalid format. Exactly 7 numbers are required (use dots for decimals). Try again:")
        return AWAIT_WHATIF_VALUES

    wait_msg = await update.message.reply_text("🧪 Contacting the ML Server for simulation...")
    payload = {
        "air_temp": vals[0], "humidity": vals[1], "pressure": vals[2],
        "water_temp": vals[3], "tds": vals[4], "soil_moisture": vals[5], "light_lux": vals[6]
    }

    mode, task, board_id = context.user_data['wi_mode'], context.user_data['wi_task'], context.user_data['wi_board']
    freq_min = 6
    endpoint = f"/predict/{freq_min}m/{mode}/{task}/manual?board_id={board_id}"

    data = await fetch_inference_api(endpoint, payload=payload)
    if not data:
        await wait_msg.edit_text("⚠️ **Timeout or Network Error from API Server.**")
        await show_main_menu(update, context)
        return ConversationHandler.END

    df_hist = await asyncio.to_thread(fetch_history_data, board_id, 3)
    series_temp = {}
    arima_series = {}
    future_vpd = []

    # Map API output to our Graphing Dictionary just like in `process_prediction`
    if mode == "ensemble":
        blended = data.get("forecast_blended", [])
        env = data.get("forecast_env", [])
        auto = data.get("forecast_auto", [])
        arima_proj = data.get("arima_projections", {})

        if blended: series_temp["What-If Projection"] = blended
        if env: series_temp["Environment (Env)"] = env
        if auto: series_temp["Autoregressive (Auto)"] = auto
        
        if arima_proj:
            arima_series["Air Temp Forecast (°C)"] = arima_proj.get("air_temp", [])
            arima_series["Humidity Forecast (%)"] = arima_proj.get("humidity", [])
            
            # Calculate Future VPD
            if blended:
                air_t = [x['value'] for x in arima_proj['air_temp']]
                hum = [x['value'] for x in arima_proj['humidity']]
                leaf_t = [x['value'] for x in blended]
                vpd_vals = calculate_future_vpd(air_t, hum, leaf_t)
                for i, vpd in enumerate(vpd_vals):
                    future_vpd.append({"timestamp": blended[i]['timestamp'], "value": vpd})
                    
    elif mode == "standard":
        raw_preds = data.get("predictions", [])
        if raw_preds:
            last_dt = df_hist.index[-1] if not df_hist.empty else pd.Timestamp.now(tz=TZ_ROME)
            future_times = [last_dt + timedelta(minutes=freq_min * (i + 1)) for i in range(len(raw_preds))]
            series_temp["What-If Projection"] = [{"timestamp": t.isoformat(), "value": v} for t, v in zip(future_times, raw_preds)]

    # Generate Plots
    plots = []
    plots.append(InputMediaPhoto(media=create_series_plot(df_hist, series_temp, f"What-If Simulation: {task.upper()}")))
    
    if arima_series:
        plots.append(InputMediaPhoto(media=create_series_plot(pd.DataFrame(), arima_series, "What-If ARIMA Forecast")))
        
    if future_vpd:
        plots.append(InputMediaPhoto(media=create_vpd_plot(df_hist, future_vpd)))

    # Formatting Receipt and Summary
    target_series = series_temp.get("What-If Projection", [])
    summary_lines = []
    if target_series:
        summary_lines = [f"🕒 {pd.to_datetime(p['timestamp']).astimezone(TZ_ROME).strftime('%H:%M')} ➔ **{p['value']:.2f}°C**" for i, p in enumerate(target_series) if (i+1) % 5 == 0]

    caption = (
        f"🧪 **Simulation Result ({mode.upper()} {task.upper()})**\n\n"
        f"_Future snapshots (every 30m):_\n" + "\n".join(summary_lines)
    )

    await update.get_bot().send_media_group(chat_id=wait_msg.chat_id, media=plots)
    await update.get_bot().send_message(chat_id=wait_msg.chat_id, text=caption, parse_mode='Markdown')
    await wait_msg.delete()

    await show_main_menu(update, context)
    return ConversationHandler.END

async def cancel_whatif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Simulation cancelled.")
    await show_main_menu(update, context)
    return ConversationHandler.END

# ==========================================
# NETWORK EXECUTORS
# ==========================================

async def process_history(update: Update, board_id: str, hours: int, wait_message):
    df_hist = await asyncio.to_thread(fetch_history_data, board_id, hours)
    if df_hist.empty:
        await wait_message.edit_text("⚠️ No data found in InfluxDB.")
        return
        
    plots = create_semantic_category_plots(df_hist)
    await update.get_bot().send_media_group(chat_id=wait_message.chat_id, media=[InputMediaPhoto(media=b) for b in plots])
    
    summary = (
        f"✅ **Request Completed**\n"
        f"**Action:** Historical Data Visualization\n"
        f"**Target:** {REVERSE_BOARD_MAP[board_id]} (ID: `{board_id}`)\n"
        f"**Timeframe:** Past {hours} Hours"
    )
    await update.get_bot().send_message(chat_id=wait_message.chat_id, text=summary, parse_mode='Markdown')
    await wait_message.delete()

async def process_prediction(update: Update, mode: str, task_or_group: str, board_id: str, wait_message, freq_min: int = 6):
    endpoint = f"/predict/{freq_min}m/{mode}/{task_or_group}/latest?board_id={board_id}"
    data = await fetch_inference_api(endpoint)
    
    if not data:
        await wait_message.edit_text("⚠️ **Timeout or Network Error from API Server.**")
        return

    df_hist = await asyncio.to_thread(fetch_history_data, board_id, 3)
    series_temp = {}
    arima_series = {}
    future_vpd = []
    weights = data.get("weights", {})
    
    if mode == "ensemble":
        blended = data.get("forecast_blended", [])
        env = data.get("forecast_env", [])
        auto = data.get("forecast_auto", [])
        generated_hist = data.get("generated_history", [])
        arima_proj = data.get("arima_projections", {})
        
        if blended: series_temp["Blended (Final)"] = blended
        if env: series_temp["Environment (Env)"] = env
        if auto: series_temp["Autoregressive (Auto)"] = auto
        if generated_hist: series_temp["T1/T4 Est. History (Soft Sensor)"] = generated_hist
        
        if arima_proj:
            # Prepare ARIMA Environmental Data for a dedicated Plot
            arima_series["Air Temp Forecast (°C)"] = arima_proj.get("air_temp", [])
            arima_series["Humidity Forecast (%)"] = arima_proj.get("humidity", [])

            # Calculate Future VPD
            if blended:
                air_t = [x['value'] for x in arima_proj['air_temp']]
                hum = [x['value'] for x in arima_proj['humidity']]
                leaf_t = [x['value'] for x in blended]
                vpd_vals = calculate_future_vpd(air_t, hum, leaf_t)
                for i, vpd in enumerate(vpd_vals):
                    future_vpd.append({"timestamp": blended[i]['timestamp'], "value": vpd})

    elif mode == "standard":
        raw_preds = data.get("predictions", [])
        if raw_preds:
            last_dt = df_hist.index[-1] if not df_hist.empty else pd.Timestamp.now(tz=TZ_ROME)
            future_times = [last_dt + timedelta(minutes=freq_min * (i + 1)) for i in range(len(raw_preds))]
            series_temp["Standard Prediction"] = [{"timestamp": t.isoformat(), "value": v} for t, v in zip(future_times, raw_preds)]

    # Plotting
    plots = []
    
    # 1. Main Temp Plot (Hide Real History if Ensemble is generating its own Soft History to compare)
    hide_real = bool(mode == "ensemble" and series_temp.get("T1/T4 Est. History (Soft Sensor)"))
    plots.append(InputMediaPhoto(media=create_series_plot(df_hist, series_temp, f"Temp. Prediction: {task_or_group.upper()}", hide_real)))
    
    # 2. ARIMA Environment Plot (Only if ensemble)
    if arima_series:
        plots.append(InputMediaPhoto(media=create_series_plot(pd.DataFrame(), arima_series, "ARIMA Environment Forecast")))

    # 3. VPD Plot
    plots.append(InputMediaPhoto(media=create_vpd_plot(df_hist, future_vpd)))

    # Formatting Receipt
    summary = (
        f"✅ **Request Completed**\n"
        f"**Action:** ML Prediction ({mode.capitalize()})\n"
        f"**Target:** {REVERSE_BOARD_MAP[board_id]} (ID: `{board_id}`)\n"
        f"**Task/Group:** {task_or_group.upper()}\n"
    )
    if weights:
        summary += f"**Ensemble Weights:** Auto: `{weights.get('autoregressive', 0)}` | Env: `{weights.get('environmental', 0)}`\n"

    await update.get_bot().send_media_group(chat_id=wait_message.chat_id, media=plots)
    await update.get_bot().send_message(chat_id=wait_message.chat_id, text=summary, parse_mode='Markdown')
    await wait_message.delete()

def main():
    if not TOKEN: 
        return logger.error("TELEGRAM_BOT_TOKEN missing in .env file!")
    application = Application.builder().token(TOKEN).post_init(setup_commands).build()
    
    application.add_handler(CommandHandler(["start", "menu"], show_main_menu))
    
    # The conversation handler for What-If
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_whatif, pattern='^menu_whatif$')],
        states={
            AWAIT_WHATIF_MODE: [CallbackQueryHandler(choose_whatif_task, pattern='^(whatif_mode_|whatif_cancel)')],
            AWAIT_WHATIF_TASK: [CallbackQueryHandler(choose_whatif_board, pattern='^(whatif_task_|whatif_cancel)')],
            AWAIT_WHATIF_BOARD: [CallbackQueryHandler(whatif_ask_values, pattern='^(whatif_board_|whatif_cancel)')],
            AWAIT_WHATIF_VALUES: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_whatif_values)]
        },
        fallbacks=[CommandHandler('cancel', cancel_whatif)]
    )
    application.add_handler(conv_handler)
    
    # Standard handlers configured to not intercept the whatif entry point
    application.add_handler(CallbackQueryHandler(handle_main_menu, pattern="^menu_(predict|history|main)$"))
    application.add_handler(CallbackQueryHandler(handle_history_menu, pattern="^hist_"))
    application.add_handler(CallbackQueryHandler(handle_predict_menu, pattern="^pred_"))

    logger.info("AgriBot initialized and listening...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()