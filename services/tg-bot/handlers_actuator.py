from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from config import CONTROLLER_URL, AWAIT_PUMP_VALUE, AWAIT_PUMP_DURATION, logger
from utils import build_keyboard, fetch_api

async def handle_actuator_routing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "act_menu":
        # DYNAMIC DISCOVERY: Fetch from Controller
        topology = await fetch_api(f"{CONTROLLER_URL}/api/topology")
        node_ids = sorted(list(topology.keys())) if topology else []
        
        buttons = [[(f"Unit {i+1} ({n_id})", f"act_board_{n_id}")] for i, n_id in enumerate(node_ids)]
        await query.edit_message_text(
            "🚰 **Actuator Control**\nSelect the greenhouse (Board) to control:", 
            reply_markup=build_keyboard(buttons, "menu_main"),
            parse_mode='Markdown'
        )

    elif data.startswith("act_board_"):
        node_id = data.split("_")[2] # This is now the actual board ID, not an index
        
        buttons = [
            [("🟢 Turn On Pump (60s @ 50%)", f"act_cmd_{node_id}_pump_127_60")],
            [("⚙️ Custom Pump Settings", f"act_custom_{node_id}")]
        ]
        await query.edit_message_text(
            f"🎛 **Control Unit ({node_id})**\nChoose an action:", 
            reply_markup=build_keyboard(buttons, "act_menu"),
            parse_mode='Markdown'
        )

    elif data.startswith("act_cmd_"):
        parts = data.split("_")
        node_id = parts[2]
        actuator = parts[3]
        value = int(parts[4])
        duration = int(parts[5])

        wait_msg = await query.message.reply_text("⏳ Sending command to the Controller...")
        
        payload = {
            "node_id": int(node_id),
            "actuator": actuator,
            "value": value,
            "duration_s": duration
        }

        logger.info(f"Sending actuator command: {payload}")
        res = await fetch_api(f"{CONTROLLER_URL}/api/command", payload=payload)

        if res and res.get("status") == "sent":
            await wait_msg.edit_text(
                f"✅ **Command Sent!**\n"
                f"**Star ID:** `{res.get('star_id')}`\n"
                f"**MQTT Topic:** `{res.get('topic')}`\n"
                f"**Action:** {actuator.upper()} -> {value} (Duration: {duration}s)",
                parse_mode='Markdown'
            )
        else:
            error_msg = res.get("error", "Unable to contact the Controller.") if res else "Unable to contact the Controller."
            await wait_msg.edit_text(f"⚠️ **HTTP Error:** {error_msg}", parse_mode='Markdown')

# --- Interactive Custom Pump Flow ---

async def ask_pump_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    node_id = query.data.split("_")[2]
    context.user_data['pump_node'] = node_id

    text = (
        "⚙️ **Custom Pump Config**\n\n"
        "At what power percentage should the pump run?\n"
        "Enter a value between **0.00** and **100.00**:\n\n"
        "_(Type /cancel to abort)_"
    )
    await query.edit_message_text(text, parse_mode='Markdown')
    return AWAIT_PUMP_VALUE

async def ask_pump_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        val = float(text.replace(',', '.'))
        if not (0.0 <= val <= 100.0):
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Invalid format or out of bounds. Please enter a percentage between 0 and 100:")
        return AWAIT_PUMP_VALUE

    esp_val = int((val / 100.0) * 255)
    context.user_data['pump_val_esp'] = esp_val
    context.user_data['pump_val_pct'] = val

    await update.message.reply_text(
        f"✅ Power calculated: **{val:.1f}%** (Protocol Value: `{esp_val}/255`).\n\n"
        "Now, for how many seconds should it run?\n"
        "Enter an integer between **1** and **120**:",
        parse_mode='Markdown'
    )
    return AWAIT_PUMP_DURATION

async def process_custom_pump(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        duration = int(text.strip())
        if not (1 <= duration <= 120):
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Invalid duration. Please enter an integer between 1 and 120:")
        return AWAIT_PUMP_DURATION

    node_id = context.user_data['pump_node']
    esp_val = context.user_data['pump_val_esp']
    pct_val = context.user_data['pump_val_pct']

    wait_msg = await update.message.reply_text("⏳ Sending custom command to the Controller...")
    
    payload = {
        "node_id": int(node_id),
        "actuator": "pump",
        "value": esp_val,
        "duration_s": duration
    }

    res = await fetch_api(f"{CONTROLLER_URL}/api/command", payload=payload)

    if res and res.get("status") == "sent":
        await wait_msg.edit_text(
            f"✅ **Custom Command Sent!**\n"
            f"**Star ID:** `{res.get('star_id')}`\n"
            f"**Action:** PUMP -> **{pct_val:.1f}%** (`{esp_val}`) for **{duration}s**",
            parse_mode='Markdown'
        )
    else:
        error_msg = res.get("error", "Unable to contact the Controller.") if res else "Unable to contact the Controller."
        await wait_msg.edit_text(f"⚠️ **HTTP Error:** {error_msg}", parse_mode='Markdown')

    return ConversationHandler.END

async def cancel_actuator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Actuator setup cancelled. Send /menu to restart.")
    return ConversationHandler.END