import asyncio
import pandas as pd
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes, ConversationHandler
from config import INFERENCE_URL, BOARD_MAP, REVERSE_BOARD_MAP, TZ_ROME, AWAIT_WHATIF_MODE, AWAIT_WHATIF_TASK, AWAIT_WHATIF_BOARD, AWAIT_WHATIF_VALUES
from utils import fetch_api, build_keyboard, check_spam_lock
from data_fetcher import fetch_history_data
from plotting import create_series_plot, create_vpd_plot, create_semantic_category_plots

async def handle_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) == 2:
        buttons = [[(f"🌿 Board {k} ({BOARD_MAP[k]})", f"hist_{parts[1]}_{k}")] for k in BOARD_MAP.keys()]
        await query.edit_message_text("Select the greenhouse (Board):", reply_markup=build_keyboard(buttons, "menu_history"))
    elif len(parts) == 3:
        if await check_spam_lock(update, context): return
        try:
            hours, board_key = int(parts[1]), parts[2]
            await query.edit_message_text(f"📊 Generating charts for Board {board_key} ({hours}h)...")
            df_hist = await asyncio.to_thread(fetch_history_data, BOARD_MAP[board_key], hours)
            if df_hist.empty:
                await query.message.reply_text("⚠️ No data found in InfluxDB.")
                return
                
            plots = create_semantic_category_plots(df_hist)
            await update.get_bot().send_media_group(chat_id=query.message.chat_id, media=[InputMediaPhoto(media=b) for b in plots])
            
            summary = (
                f"✅ **Request Completed**\n"
                f"**Target:** {REVERSE_BOARD_MAP[BOARD_MAP[board_key]]}\n"
                f"**Timeframe:** Past {hours} Hours"
            )
            await update.get_bot().send_message(chat_id=query.message.chat_id, text=summary, parse_mode='Markdown')
            await query.message.delete()
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
        await query.edit_message_text("Which greenhouse (Board)?", reply_markup=build_keyboard(buttons, "menu_predict"))

    elif data.startswith("pred_go_"):
        if await check_spam_lock(update, context): return
        try:
            _, _, type_mod, param, board_key = data.split("_")
            mode = "ensemble" if type_mod == "ens" else "standard"
            wait_msg = await query.message.reply_text(f"🔄 Starting {mode.upper()} engine ({param}) for Board {board_key}...")
            await query.message.delete()
            await _process_prediction(update, mode, param, BOARD_MAP[board_key], wait_msg)
        finally:
            context.user_data['is_processing'] = False

async def _process_prediction(update: Update, mode: str, task_or_group: str, board_id: str, wait_message, freq_min: int = 6):
    endpoint = f"{INFERENCE_URL}/predict/{freq_min}m/{mode}/{task_or_group}/latest?board_id={board_id}"
    data = await fetch_api(endpoint)
    
    if not data:
        await wait_message.edit_text("⚠️ **Timeout or Network Error from API Server.**")
        return

    df_hist = await asyncio.to_thread(fetch_history_data, board_id, 3)
    
    historical_api = data.get("historical", {})
    predictions_api = data.get("predictions", {})
    arima_proj = data.get("prophet_projections", {}) # Renamed to match API output
    
    series_temp = {}
    arima_series = {}
    
    est_hist = historical_api.get("leaf_temp_estimated", [])
    if est_hist: series_temp["T1/T4 Est. History (Soft Sensor)"] = est_hist
        
    future_vpd = predictions_api.get("vpd_forecast", [])
    
    if mode == "ensemble":
        if p := predictions_api.get("forecast_blended", []): series_temp["Blended (Final)"] = p
        if p := predictions_api.get("forecast_env", []): series_temp["Environment (Env)"] = p
        if p := predictions_api.get("forecast_auto", []): series_temp["Autoregressive (Auto)"] = p
        
        if arima_proj:
            arima_series["Air Temp Forecast (°C)"] = arima_proj.get("air_temp", [])
            arima_series["Humidity Forecast (%)"] = arima_proj.get("humidity", [])

    elif mode == "standard":
        if p := predictions_api.get("target_forecast", []): series_temp["Standard Prediction"] = p

    plots = []
    hide_real = bool(series_temp.get("T1/T4 Est. History (Soft Sensor)"))
    plots.append(InputMediaPhoto(media=create_series_plot(df_hist, series_temp, f"Temp. Prediction: {task_or_group.upper()}", hide_real)))
    
    if arima_series: plots.append(InputMediaPhoto(media=create_series_plot(pd.DataFrame(), arima_series, "Prophet Environment Forecast")))
    plots.append(InputMediaPhoto(media=create_vpd_plot(df_hist, future_vpd)))

    summary = (
        f"✅ **Request Completed**\n"
        f"**Action:** ML Prediction ({mode.capitalize()})\n"
        f"**Target:** {REVERSE_BOARD_MAP[board_id]}\n"
        f"**Task/Group:** {task_or_group.upper()}\n"
    )

    await update.get_bot().send_media_group(chat_id=wait_message.chat_id, media=plots)
    await update.get_bot().send_message(chat_id=wait_message.chat_id, text=summary, parse_mode='Markdown')
    await wait_message.delete()

# --- WHAT-IF LOGIC ---
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
        # Cannot easily call show_main_menu due to circular deps, just instruct user
        await query.edit_message_text("❌ Simulation cancelled. Send /menu to restart.")
        return ConversationHandler.END

    mode = query.data.split("_")[2]
    context.user_data['wi_mode'] = mode
    
    if mode == "ensemble":
        keyboard = build_keyboard([[("Group A (Uses TDS)", "whatif_task_A")], [("Group B (No TDS)", "whatif_task_B")]], "whatif_cancel")
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
        await query.edit_message_text("❌ Simulation cancelled. Send /menu to restart.")
        return ConversationHandler.END

    context.user_data['wi_task'] = query.data.split("_")[2]
    buttons = [[(f"🌿 Board {k}", f"whatif_board_{k}")] for k in BOARD_MAP.keys()]
    await query.edit_message_text("Select the greenhouse to apply the context to:", reply_markup=build_keyboard(buttons, "whatif_cancel"))
    return AWAIT_WHATIF_BOARD

async def whatif_ask_values(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "whatif_cancel":
        await query.edit_message_text("❌ Simulation cancelled. Send /menu to restart.")
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
        await update.message.reply_text("⚠️ Invalid format. Exactly 7 numbers are required. Try again:")
        return AWAIT_WHATIF_VALUES

    wait_msg = await update.message.reply_text("🧪 Contacting the ML Server for simulation...")
    payload = {
        "air_temp": vals[0], "humidity": vals[1], "pressure": vals[2],
        "water_temp": vals[3], "tds": vals[4], "soil_moisture": vals[5], "light_lux": vals[6]
    }

    mode, task, board_id = context.user_data['wi_mode'], context.user_data['wi_task'], context.user_data['wi_board']
    endpoint = f"{INFERENCE_URL}/predict/6m/{mode}/{task}/manual?board_id={board_id}"

    data = await fetch_api(endpoint, payload=payload)
    if not data:
        await wait_msg.edit_text("⚠️ **Timeout or Network Error from API Server.**")
        return ConversationHandler.END

    df_hist = await asyncio.to_thread(fetch_history_data, board_id, 3)
    
    historical_api = data.get("historical", {})
    predictions_api = data.get("predictions", {})
    arima_proj = data.get("prophet_projections", {})

    series_temp, arima_series = {}, {}

    if est_hist := historical_api.get("leaf_temp_estimated", []): series_temp["T1/T4 Est. History (Soft Sensor)"] = est_hist

    if mode == "ensemble":
        if blended := predictions_api.get("forecast_blended", []): series_temp["What-If Projection"] = blended
        if arima_proj:
            arima_series["Air Temp Forecast (°C)"] = arima_proj.get("air_temp", [])
            arima_series["Humidity Forecast (%)"] = arima_proj.get("humidity", [])
            
    elif mode == "standard":
        if raw_preds := predictions_api.get("target_forecast", []): series_temp["What-If Projection"] = raw_preds

    future_vpd = predictions_api.get("vpd_forecast", [])

    plots = []
    hide_real = bool(mode == "ensemble" and series_temp.get("T1/T4 Est. History (Soft Sensor)"))
    plots.append(InputMediaPhoto(media=create_series_plot(df_hist, series_temp, f"What-If Simulation: {task.upper()}", hide_real)))
    
    if arima_series: plots.append(InputMediaPhoto(media=create_series_plot(pd.DataFrame(), arima_series, "What-If Prophet Forecast")))
    if future_vpd: plots.append(InputMediaPhoto(media=create_vpd_plot(df_hist, future_vpd)))

    target_series = series_temp.get("What-If Projection", [])
    summary_lines = [f"🕒 {pd.to_datetime(p['timestamp']).astimezone(TZ_ROME).strftime('%H:%M')} ➔ **{p['value']:.2f}°C**" for i, p in enumerate(target_series) if (i+1) % 5 == 0]

    caption = f"🧪 **Simulation Result ({mode.upper()} {task.upper()})**\n\n_Future snapshots (every 30m):_\n" + "\n".join(summary_lines)

    await update.get_bot().send_media_group(chat_id=wait_msg.chat_id, media=plots)
    await update.get_bot().send_message(chat_id=wait_msg.chat_id, text=caption, parse_mode='Markdown')
    await wait_msg.delete()
    return ConversationHandler.END

async def cancel_whatif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Simulation cancelled. Send /menu to restart.")
    return ConversationHandler.END