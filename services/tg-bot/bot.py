import logging
from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

from handlers_actuator import (
    handle_actuator_routing, ask_pump_value, ask_pump_duration, 
    process_custom_pump, cancel_actuator
)
from config import (
    TOKEN, INFERENCE_URL, AWAIT_WHATIF_MODE, AWAIT_WHATIF_TASK, 
    AWAIT_WHATIF_BOARD, AWAIT_WHATIF_VALUES, AWAIT_PUMP_VALUE, 
    AWAIT_PUMP_DURATION, logger
)

from utils import build_keyboard, fetch_api

from handlers_inference import (
    handle_predict_menu, start_whatif,
    choose_whatif_task, choose_whatif_board, whatif_ask_values, process_whatif_values, cancel_whatif
)
from handlers_training import show_training_menu, handle_training_menu
from handlers_history import handle_history_menu

async def setup_commands(application: Application):
    await application.bot.set_my_commands([
        BotCommand("menu", "🎛 Open Control Panel"),
        BotCommand("info", "📊 View ML Model Metrics (Usage: /info <freq> <task>)"),
        BotCommand("reload", "🔄 Reload API Models into RAM")
    ])

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['is_processing'] = False 
    keyboard = build_keyboard([
        [("🔮 Start ML Prediction", "menu_predict")],
        [("📊 View History", "menu_history")],
        [("🧪 What-If Simulation", "menu_whatif")],
        [("⚙️ Training Center", "train_menu")],
        [("🚰 Actuator Control", "act_menu")]
    ])

    text = "🤖 **GJ greenhouse - Control Center**\nSelect an operation:"
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')
    elif update.callback_query:
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


async def handle_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = update.message.text.split()
    if len(args) != 3:
        await update.message.reply_text("ℹ️ **Usage:** `/info [freq_minutes] [task]`\n_Example:_ `/info 6 t1`", parse_mode='Markdown')
        return
    
    freq, task = args[1], args[2].lower()
    msg = await update.message.reply_text(f"🔍 Fetching metrics for **{freq}m {task.upper()}**...")
    data = await fetch_api(f"{INFERENCE_URL}/info/{freq}m/{task}")
    if data:
        await msg.edit_text(f"📊 **Model Metrics ({freq}m {task.upper()}):**\n```json\n{json.dumps(data, indent=2)}\n```", parse_mode='Markdown')
    else:
        await msg.edit_text("⚠️ **Model info not found.** Check if the task/frequency exists.")

async def handle_reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Requesting API server to reload models into RAM...")
    data = await fetch_api(f"{INFERENCE_URL}/reload-models", payload={})
    if data and data.get("status") == "ok":
        await msg.edit_text("✅ **Models reloaded successfully!**", parse_mode='Markdown')
    else:
        await msg.edit_text("⚠️ **Failed to reload models.** Check API logs.", parse_mode='Markdown')

def main():
    if not TOKEN: return logger.error("TELEGRAM_BOT_TOKEN missing in .env file!")
    application = Application.builder().token(TOKEN).post_init(setup_commands).build()
    
    
    application.add_handler(CommandHandler(["start", "menu"], show_main_menu))
    application.add_handler(CommandHandler("info", handle_info_command))
    application.add_handler(CommandHandler("reload", handle_reload_command))
    
    
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
    
    
    application.add_handler(CallbackQueryHandler(handle_main_menu, pattern="^menu_(predict|history|main)$"))
    application.add_handler(CallbackQueryHandler(handle_history_menu, pattern="^hist_"))
    application.add_handler(CallbackQueryHandler(handle_predict_menu, pattern="^pred_"))
    application.add_handler(CallbackQueryHandler(handle_training_menu, pattern="^train_"))
    
    actuator_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_pump_value, pattern='^act_custom_')],
        states={
            AWAIT_PUMP_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_pump_duration)],
            AWAIT_PUMP_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_custom_pump)]
        },
        fallbacks=[CommandHandler('cancel', cancel_actuator)]
    )
    application.add_handler(actuator_conv_handler)
    
    application.add_handler(CallbackQueryHandler(handle_actuator_routing, pattern="^act_(menu$|board_|cmd_)"))

    logger.info("GJGreenhousBot initialized and listening...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()