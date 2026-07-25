import asyncio
import pandas as pd
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes, ConversationHandler
from config import INFERENCE_URL, AWAIT_WHATIF_MODE, AWAIT_WHATIF_TASK, AWAIT_WHATIF_BOARD, AWAIT_WHATIF_VALUES, logger
from utils import fetch_api, build_keyboard, check_spam_lock
from data_fetcher import fetch_available_boards, fetch_history_with_preds
from plotting import create_series_plot, create_vpd_plot, PlotLabels


def _build_prediction_media(df_hist: pd.DataFrame, data: dict, mode: str, task: str, board_id: str, is_whatif: bool = False):
    leaf_data = data.get("leaf_temperature", {})
    env_data = data.get("environmental_data", {})
    vpd_data = data.get("vpd", {})
    ens_details = data.get("ensemble_details", {})

    series_temp = {}
    prophet_series = {}

    if est_hist := leaf_data.get("historical", []):
        series_temp[PlotLabels.SOFT_HIST] = est_hist

    proj_name = PlotLabels.WHATIF_PROJ if is_whatif else (PlotLabels.BLENDED if mode == "ensemble" else PlotLabels.STD_PRED)
    
    if mode == "ensemble":
        if p := leaf_data.get("forecast", []):
            series_temp[proj_name] = p
        if p := ens_details.get("forecast_env", []):
            series_temp[PlotLabels.ENV] = p
        if p := ens_details.get("forecast_auto", []):
            series_temp[PlotLabels.AUTO] = p
    elif mode == "standard":
        if p := leaf_data.get("forecast", []):
            series_temp[proj_name] = p

    if env_hist := env_data.get("historical", {}):
        if air_h := env_hist.get("air_temp", []): prophet_series[PlotLabels.AIR_HIST] = air_h
        if hum_h := env_hist.get("humidity", []): prophet_series[PlotLabels.HUM_HIST] = hum_h
        
    if env_forecast := env_data.get("forecast", {}):
        if air_f := env_forecast.get("air_temp", []): prophet_series[PlotLabels.AIR_FC] = air_f
        if hum_f := env_forecast.get("humidity", []): prophet_series[PlotLabels.HUM_FC] = hum_f

    if leaf_h := leaf_data.get("historical", []): prophet_series[PlotLabels.LEAF_HIST] = leaf_h
    if leaf_f := leaf_data.get("forecast", []): prophet_series[PlotLabels.LEAF_FC] = leaf_f

    historical_vpd = vpd_data.get("historical", [])
    future_vpd = vpd_data.get("forecast", [])

    plots = []
    hide_real = bool(series_temp.get(PlotLabels.SOFT_HIST))
    title_prefix = "What-If Simulation" if is_whatif else "Temp. Prediction"

    plots.append(InputMediaPhoto(media=create_series_plot(df_hist, series_temp, f"{title_prefix}: {task.upper()}", hide_real_history=hide_real)))
    
    if prophet_series:
        plots.append(InputMediaPhoto(media=create_series_plot(pd.DataFrame(), prophet_series, f"{title_prefix} Prophet Forecast", hide_real_history=True)))

    if (historical_vpd) or (future_vpd): 
        plots.append(InputMediaPhoto(media=create_vpd_plot(df_hist, future_vpd, historical_vpd)))

    action_type = "What-If Simulation" if is_whatif else "ML Prediction"
    summary = (
        f"✅ **Request Completed**\n"
        f"**Action:** {action_type} ({mode.capitalize()})\n"
        f"**Target:** Unit ({board_id})\n"
        f"**Task/Group:** {task.upper()}\n"
    )

    weights = ens_details.get("weights", {})
    if (mode == "ensemble") and (weights):
        w_auto = weights.get("autoregressive", 0) * 100
        w_env = weights.get("environmental", 0) * 100
        summary += (
            f"\n⚖️ **Ensemble Weights:**\n"
            f" • Autoregressive: {w_auto:.1f}%\n"
            f" • Environmental: {w_env:.1f}%\n"
        )
        
    return plots, summary


async def _process_prediction(update: Update, mode: str, task_or_group: str, board_id: str, wait_message, freq_min: int = 6):
    endpoint = f"{INFERENCE_URL}/predict/{freq_min}m/{mode}/{task_or_group}/latest?board_id={board_id}"
    data = await fetch_api(endpoint)
    
    if not data:
        await wait_message.edit_text("⚠️ **Timeout or Network Error from API Server.**")
        return

    df_hist = await asyncio.to_thread(fetch_history_with_preds, board_id, 3, 3, 6)

    plots, summary = _build_prediction_media(df_hist, data, mode, task_or_group, board_id, is_whatif=False)
    
    await update.get_bot().send_media_group(chat_id=wait_message.chat_id, media=plots)
    await update.get_bot().send_message(chat_id=wait_message.chat_id, text=summary, parse_mode='Markdown')
    await wait_message.delete()






async def handle_predict_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "menu_predict":
        keyboard = build_keyboard([[("🤝🏻 Ensemble Model", "pred_ens")], [("🧍🏻‍♂️ Single Model", "pred_std")]], "menu_main")
        await query.edit_message_text("Which predictive engine do you want to use?", reply_markup=keyboard)
    
    elif data == "pred_ens":
        keyboard = build_keyboard([
            [("Group A (Uses TDS)", "pred_sel_ens_A")], 
            [("Group B (No TDS)", "pred_sel_ens_B")],
            [("Group C (No TDS, Lags 15)", "pred_sel_ens_C")]
        ], "menu_predict")
        await query.edit_message_text("Select the strategy:", reply_markup=keyboard)

    elif data == "pred_std":
        keyboard = build_keyboard([
            [("T1 (Now)", "pred_sel_std_t1"), ("T4 (No TDS)", "pred_sel_std_t4")],
            [("T2 (Env. 3h)", "pred_sel_std_t2"), ("T5 (Env. No TDS)", "pred_sel_std_t5")],
            [("T3 (Auto 3h)", "pred_sel_std_t3"), ("T6 (Auto No TDS)", "pred_sel_std_t6")],
            [("T8 (Env. Lags 15)", "pred_sel_std_t8"), ("T9 (Auto Lags 15)", "pred_sel_std_t9")]
        ], "menu_predict")
        await query.edit_message_text("Choose a specific Task:", reply_markup=keyboard)

    elif data.startswith("pred_sel_"):
        mode = data.replace("pred_sel_", "")
        boards = await asyncio.to_thread(fetch_available_boards)
        buttons = [[(f"Unit {i+1} ({b_id})", f"pred_go_{mode}_{b_id}")] for i, b_id in enumerate(boards)]
        await query.edit_message_text("Which greenhouse (Board)?", reply_markup=build_keyboard(buttons, "menu_predict"))

    elif data.startswith("pred_go_"):
        if await check_spam_lock(update, context):
            return None
        try:
            _, _, type_mod, param, board_id = data.split("_")
            mode = "ensemble" if type_mod == "ens" else "standard"
            wait_msg = await query.message.reply_text(f"🔄 Starting {mode.upper()} engine ({param}) for Unit ({board_id})...")
            await query.message.delete()
            await _process_prediction(update, mode, param, board_id, wait_msg)
        finally:
            context.user_data['is_processing'] = False










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
        await query.edit_message_text("❌ Simulation cancelled. Send /menu to restart.")
        return ConversationHandler.END

    mode = query.data.split("_")[2]
    context.user_data['wi_mode'] = mode
    
    if mode == "ensemble":
        keyboard = build_keyboard([
            [("Group A (Uses TDS)", "whatif_task_A")], 
            [("Group B (No TDS)", "whatif_task_B")],
            [("Group C (No TDS, Lags 15)", "whatif_task_C")]
        ], "whatif_cancel")
    else:
        keyboard = build_keyboard([
            [("T1", "whatif_task_t1"), ("T4", "whatif_task_t4")],
            [("T2", "whatif_task_t2"), ("T5", "whatif_task_t5")],
            [("T3", "whatif_task_t3"), ("T6", "whatif_task_t6")],
            [("T8", "whatif_task_t8"), ("T9", "whatif_task_t9")]
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
    boards = await asyncio.to_thread(fetch_available_boards)
    buttons = [[(f"Unit {i+1} ({b_id})", f"whatif_board_{b_id}")] for i, b_id in enumerate(boards)]
    await query.edit_message_text("Select the greenhouse to apply the context to:", reply_markup=build_keyboard(buttons, "whatif_cancel"))
    return AWAIT_WHATIF_BOARD



async def whatif_ask_values(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "whatif_cancel":
        await query.edit_message_text("❌ Simulation cancelled. Send /menu to restart.")
        return ConversationHandler.END

    board_id = query.data.split("_")[2]
    context.user_data['wi_board'] = board_id

    text = (
        f"✅ Context: **{context.user_data['wi_mode'].upper()} {context.user_data['wi_task'].upper()}** on **Unit ({board_id})**.\n\n"
        "Please provide the **7 values** (separated by spaces):\n"
        "`[Air Temp] [Humidity] [Pressure] [Water Temp] [TDS] [Soil Moisture] [Luminosity]`\n\n"
        "📝 _Example:_\n`25.5 60 1013 22.0 400 45 10000`\n"
        "_(Type /cancel to exit)_"
    )

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=None)
    return AWAIT_WHATIF_VALUES



async def process_whatif_values(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        vals = [float(x.strip().replace(',', '.')) for x in text.split()]
        if len(vals) != 7: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Invalid format. Exactly 7 numbers are required. Try again:")
        return AWAIT_WHATIF_VALUES

    mode, task, board_id = context.user_data.get('wi_mode'), context.user_data.get('wi_task'), context.user_data.get('wi_board')
    if (not (mode and task) and (board_id)):
        await update.message.reply_text("⚠️ Simulation context lost. Send /menu to restart.")
        return ConversationHandler.END

    wait_msg = await update.message.reply_text("🧪 Contacting the ML Server for simulation...")
    payload = {
        "air_temp": vals[0], "humidity": vals[1], "pressure": vals[2],
        "water_temp": vals[3], "tds": vals[4], "soil_moisture": vals[5], "light_lux": vals[6]
    }
    endpoint = f"{INFERENCE_URL}/predict/6m/{mode}/{task}/manual?board_id={board_id}"

    try:
        data = await fetch_api(endpoint, payload=payload)
        if not data:
            await wait_msg.edit_text("⚠️ **Timeout or Network Error from API Server.**")
            return ConversationHandler.END

        df_hist = await asyncio.to_thread(fetch_history_with_preds, board_id, 3, 3, 6)
        
        plots, summary = _build_prediction_media(df_hist, data, mode, task, board_id, is_whatif=True)
        
        await update.get_bot().send_media_group(chat_id=wait_msg.chat_id, media=plots)
        await update.get_bot().send_message(chat_id=wait_msg.chat_id, text=summary, parse_mode='Markdown')
        await wait_msg.delete()
        
    except Exception as e:
        logger.error(f"What-If simulation failed: {e}")
        await wait_msg.edit_text("⚠️ Simulation failed while building the results. Send /menu to restart.")
    
    await update.message.reply_text("🧪 **Want to test another configuration?**\nSend 7 new values or /cancel to exit.")
    return AWAIT_WHATIF_VALUES


async def cancel_whatif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Simulation cancelled. Send /menu to restart.")
    return ConversationHandler.END