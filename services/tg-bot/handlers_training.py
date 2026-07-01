import io
import zipfile
from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes
from config import TRAINER_URL
from utils import fetch_api, fetch_api_raw, build_keyboard, check_spam_lock

async def show_training_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    
    keyboard = build_keyboard([
        [("▶️ Start Standard Training", "train_start_std")],
        [("📋 Queue Status", "train_queue")],
        [("🌍 Global Plots (All Tasks)", "train_global_freq")],
        [("🎯 Task Plots (Specific)", "train_task_freq")]
    ], "menu_main")

    text = "⚙️ **ML Trainer Control Center**\nManage background training queues and fetch analytics:"
    
    if query:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')

async def handle_training_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "train_menu":
        await show_training_menu(update, context)

    elif data == "train_start_std":
        msg = await query.message.reply_text("🚀 Triggering Standard Training...")
        res = await fetch_api(f"{TRAINER_URL}/train/standard", payload={})
        if res:
            await msg.edit_text(f"✅ **Training Started!**\nMessage: {res.get('message')}\nQueue size: {res.get('queue_size')}", parse_mode='Markdown')
        else:
            await msg.edit_text("⚠️ Failed to contact Trainer API.")

    elif data == "train_queue":
        res = await fetch_api(f"{TRAINER_URL}/queue/status")
        if res:
            await query.edit_message_text(f"📋 **Queue Status:** There are `{res.get('tasks_in_queue')}` tasks pending/processing.", parse_mode='Markdown', reply_markup=build_keyboard([], "train_menu"))
        else:
            await query.edit_message_text("⚠️ Failed to contact Trainer API.", reply_markup=build_keyboard([], "train_menu"))

    elif data == "train_global_freq":
        keyboard = build_keyboard([[("6 Minutes", "train_global_run_6")]], "train_menu")
        await query.edit_message_text("Select Frequency for Global Analytics:", reply_markup=keyboard)

    elif data.startswith("train_global_run_"):
        if await check_spam_lock(update, context): return
        try:
            freq = data.split("_")[-1]
            wait_msg = await query.message.reply_text(f"🌍 Fetching and extracting Global Analytics for {freq}m. Please wait...")
            
            zip_bytes = await fetch_api_raw(f"{TRAINER_URL}/analytics/{freq}/plots/global")
            if not zip_bytes:
                await wait_msg.edit_text("⚠️ Failed to fetch Global plots. Make sure models are trained.")
                return
                
            await _unzip_and_send(update, wait_msg, zip_bytes, f"Global Analytics ({freq}m)")
        finally:
            context.user_data['is_processing'] = False

    elif data == "train_task_freq":
        keyboard = build_keyboard([[("6 Minutes", "train_task_sel_6")]], "train_menu")
        await query.edit_message_text("Select Frequency for Task Analytics:", reply_markup=keyboard)

    elif data.startswith("train_task_sel_"):
        freq = data.split("_")[-1]
        buttons = [
            [(f"T1", f"train_task_run_{freq}_t1"), (f"T4", f"train_task_run_{freq}_t4")],
            [(f"T2", f"train_task_run_{freq}_t2"), (f"T5", f"train_task_run_{freq}_t5")],
            [(f"T3", f"train_task_run_{freq}_t3"), (f"T6", f"train_task_run_{freq}_t6")],
            [(f"T8", f"train_task_run_{freq}_t8"), (f"T9", f"train_task_run_{freq}_t9")]
        ]
        await query.edit_message_text("Select the Task:", reply_markup=build_keyboard(buttons, "train_task_freq"))

    elif data.startswith("train_task_run_"):
        if await check_spam_lock(update, context): return
        try:
            parts = data.split("_")
            freq, task = parts[-2], parts[-1]
            wait_msg = await query.message.reply_text(f"🎯 Fetching Analytics for Task {task.upper()} ({freq}m)...")
            
            zip_bytes = await fetch_api_raw(f"{TRAINER_URL}/analytics/{freq}/plots/task/{task}")
            if not zip_bytes:
                await wait_msg.edit_text("⚠️ Failed to fetch Task plots. Make sure the task is trained.")
                return
                
            await _unzip_and_send(update, wait_msg, zip_bytes, f"Task Analytics: {task.upper()} ({freq}m)")
        finally:
            context.user_data['is_processing'] = False

async def _unzip_and_send(update: Update, wait_msg, zip_bytes: bytes, title: str):
    """Unzips a byte payload in RAM and sends the images as an album to Telegram."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            media_group = []
            for filename in z.namelist():
                if filename.endswith(".png"):
                    img_data = z.read(filename)
                    media_group.append(InputMediaPhoto(media=img_data))

            if not media_group:
                await wait_msg.edit_text("⚠️ The ZIP archive is empty or contains no PNGs.")
                return
            
            # Telegram accepts max 10 photos per MediaGroup. We chunk them securely.
            chunks = [media_group[i:i + 10] for i in range(0, len(media_group), 10)]
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    # FIX: Immutable class workaround. Recreate the object with the caption.
                    chunk[0] = InputMediaPhoto(
                        media=chunk[0].media,
                        caption=f"📊 **{title}**",
                        parse_mode='Markdown'
                    )
                await update.get_bot().send_media_group(chat_id=wait_msg.chat_id, media=chunk)
                
            await wait_msg.delete()
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Error extracting ZIP: {e}")