import asyncio
import math
import pandas as pd
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes
from config import INFERENCE_URL
from utils import build_keyboard, check_spam_lock, fetch_api
from data_fetcher import fetch_history_with_preds, fetch_available_boards
from plotting import create_history_plots


def _svp(t: float) -> float:
    """ Saturation vapor pressure (kPa) — same formula used by the inference service. """
    return 0.61078 * math.exp((17.27 * t) / (t + 237.3))

async def _fetch_vpd_forecast(board_id: str, task: str = "t5", freq_min: int = 6, horizon_hours: int = 2):
    """ Ask the ML inference service (best model for `task`) for the next-`horizon_hours`
        VPD forecast. Returns {"air": [...], "leaf": [...]} of {timestamp, value}, or None. """
    endpoint = f"{INFERENCE_URL}/predict/{freq_min}m/standard/{task}/latest?board_id={board_id}"
    data = await fetch_api(endpoint)
    if not data or "error" in data:
        return None

    env_fc = data.get("environmental_data", {}).get("forecast", {})
    air_fc = env_fc.get("air_temp", []) or []
    hum_fc = env_fc.get("humidity", []) or []
    leaf_vpd_fc = data.get("vpd", {}).get("forecast", []) or []

    air_vpd_fc = [
        {"timestamp": a["timestamp"], "value": round(max(0.0, _svp(a["value"]) * (1 - h["value"] / 100.0)), 4)}
        for a, h in zip(air_fc, hum_fc)
    ]

    def _limit(series):
        if not series:
            return []
        cutoff = pd.to_datetime(series[0]["timestamp"]) + pd.Timedelta(hours=horizon_hours)
        return [d for d in series if pd.to_datetime(d["timestamp"]) <= cutoff]

    air_vpd_fc, leaf_vpd_fc = _limit(air_vpd_fc), _limit(leaf_vpd_fc)
    if not air_vpd_fc and not leaf_vpd_fc:
        return None
    return {"air": air_vpd_fc, "leaf": leaf_vpd_fc}

async def handle_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    
    if len(parts) == 2:
        boards = await asyncio.to_thread(fetch_available_boards)
        buttons = [[(f"Unit {i+1} ({b_id})", f"hist_{parts[1]}_{b_id}")] for i, b_id in enumerate(boards)]
        await query.edit_message_text("Select the greenhouse (Board):", reply_markup=build_keyboard(buttons, "menu_history"))
        
    elif len(parts) == 3:
        if await check_spam_lock(update, context):
            return
        try:
            hours, board_id = int(parts[1]), parts[2]
            await query.edit_message_text(f"📊 Generating charts for Unit ({board_id}) ({hours}h)...")
            
            # Use the dedicated history fetcher
            df_hist = await asyncio.to_thread(fetch_history_with_preds, board_id, hours, 3, 6)
            
            if df_hist.empty:
                await query.message.reply_text(f"⚠️ No data found in InfluxDB for Unit ({board_id}).")
                return

            vpd_forecast = await _fetch_vpd_forecast(board_id, task="t5", freq_min=6, horizon_hours=2)

            plots = create_history_plots(df_hist, vpd_forecast)
            await update.get_bot().send_media_group(chat_id=query.message.chat_id, media=[InputMediaPhoto(media=b) for b in plots])

            latest_local = df_hist.index.max()
            is_stale = (pd.Timestamp.now(tz=latest_local.tz) - latest_local) > pd.Timedelta(hours=hours)
            timeframe = (
                f"Latest {hours}h available (last reading {latest_local:%Y-%m-%d %H:%M}, board is lagging)"
                if is_stale else f"Past {hours} Hours"
            )
            summary = (
                f"✅ **Request Completed**\n"
                f"**Target:** Unit ({board_id})\n"
                f"**Timeframe:** {timeframe}"
            )
            await update.get_bot().send_message(chat_id=query.message.chat_id, text=summary, parse_mode='Markdown')
            await query.message.delete()
        finally:
            context.user_data['is_processing'] = False