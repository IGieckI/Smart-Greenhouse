import asyncio
import pandas as pd
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes
from utils import build_keyboard, check_spam_lock
from data_fetcher import fetch_history_with_preds, fetch_available_boards 
from plotting import create_history_plots

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
                await query.message.reply_text("⚠️ No data found in InfluxDB.")
                return

            # Use the dedicated history plotter
            plots = create_history_plots(df_hist)
            await update.get_bot().send_media_group(chat_id=query.message.chat_id, media=[InputMediaPhoto(media=b) for b in plots])
            
            summary = (
                f"✅ **Request Completed**\n"
                f"**Target:** Unit ({board_id})\n"
                f"**Timeframe:** Past {hours} Hours"
            )
            await update.get_bot().send_message(chat_id=query.message.chat_id, text=summary, parse_mode='Markdown')
            await query.message.delete()
        finally:
            context.user_data['is_processing'] = False