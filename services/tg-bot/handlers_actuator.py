from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from config import CONTROLLER_URL, AWAIT_ACT_CUSTOM, logger
from utils import build_keyboard, fetch_api


async def _dispatch_command(node_id, actuator: str, value: int, duration: int) -> dict:
    """POST an actuator command to the controller and return its (possibly error) response."""
    payload = {
        "node_id": int(node_id),
        "actuator": actuator,
        "value": value,
        "duration_s": duration,
    }
    logger.info(f"Sending actuator command: {payload}")
    return await fetch_api(f"{CONTROLLER_URL}/api/command", payload=payload, surface_errors=True)


def _format_command_result(res: dict, actuator: str, value: int, duration: int) -> str:
    """Build the user-facing message for a command attempt (success or the real error reason)."""
    if res and res.get("status") == "sent":
        return (
            f"✅ **Command Sent!**\n"
            f"**Star ID:** `{res.get('star_id')}`\n"
            f"**MQTT Topic:** `{res.get('topic')}`\n"
            f"**Action:** {actuator.upper()} -> {value} (Duration: {duration}s)"
        )
    error_msg = res.get("error", "Unable to contact the Controller.") if res else "Unable to contact the Controller."
    return f"⚠️ **Error:** {error_msg}"


async def handle_actuator_routing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "act_menu":
        topology = await fetch_api(f"{CONTROLLER_URL}/api/topology")
        node_ids = sorted(list(topology.keys())) if topology else []
        buttons = [[(f"Unit {i+1} ({n_id})", f"act_board_{n_id}")] for i, n_id in enumerate(node_ids)]
        await query.edit_message_text(
            "🚰 **Actuator Control**\nSelect the greenhouse (Board) to control:",
            reply_markup=build_keyboard(buttons, "menu_main"),
            parse_mode='Markdown'
        )

    elif data.startswith("act_board_"):
        node_id = data.split("_")[2]

        buttons = [
            [("🟢 Turn On Pump (10s)", f"act_cmd_{node_id}_pump_255_10")],
            [("✏️ Custom Command", f"act_custom_{node_id}")]
        ]
        await query.edit_message_text(
            f"🎛 **Control Unit** (Node: `{node_id}`)\nChoose an action:",
            reply_markup=build_keyboard(buttons, "act_menu"),
            parse_mode='Markdown'
        )

    elif data.startswith("act_cmd_"):
        parts = data.split("_")
        node_id, actuator, value, duration = parts[2], parts[3], int(parts[4]), int(parts[5])

        wait_msg = await query.message.reply_text("⏳ Sending command to the Controller...")
        res = await _dispatch_command(node_id, actuator, value, duration)
        await wait_msg.edit_text(_format_command_result(res, actuator, value, duration), parse_mode='Markdown')


# Custom command conversation

async def start_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: user tapped 'Custom Command' for a board. Ask for the free-form command."""
    query = update.callback_query
    await query.answer()
    node_id = query.data.split("_")[2]
    context.user_data['ac_node'] = node_id

    await query.edit_message_text(
        f"✏️ **Custom Command** for node `{node_id}`\n\n"
        "Send it as: `<actuator> <value 0-255> <duration_s>`\n\n"
        "📝 _Example:_\n"
        "`pump 200 30`\n\n"
        "_(Type /cancel to exit)_",
        parse_mode='Markdown'
    )
    return AWAIT_ACT_CUSTOM


async def process_custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse and dispatch the typed '<actuator> <value> <duration>' command."""
    node_id = context.user_data.get('ac_node')
    if not node_id:
        await update.message.reply_text("⚠️ Context lost. Send /menu to restart.")
        return ConversationHandler.END

    parts = update.message.text.split()
    if len(parts) != 3:
        await update.message.reply_text(
            "⚠️ Format: `<actuator> <value 0-255> <duration_s>`\nExample: `pump 200 30`. Try again:",
            parse_mode='Markdown'
        )
        return AWAIT_ACT_CUSTOM

    actuator = parts[0].lower()

    if len(actuator) > 4:
        await update.message.reply_text("⚠️ Actuator name must be at most 4 characters. Try again:")
        return AWAIT_ACT_CUSTOM

    try:
        value, duration = int(parts[1]), int(parts[2])
    except ValueError:
        await update.message.reply_text("⚠️ Value and duration must be whole numbers. Try again:")
        return AWAIT_ACT_CUSTOM

    if not (0 <= value <= 255) or duration < 0:
        await update.message.reply_text("⚠️ Value must be 0–255 and duration ≥ 0. Try again:")
        return AWAIT_ACT_CUSTOM

    wait_msg = await update.message.reply_text("⏳ Sending command to the Controller...")
    res = await _dispatch_command(node_id, actuator, value, duration)
    await wait_msg.edit_text(_format_command_result(res, actuator, value, duration), parse_mode='Markdown')
    return ConversationHandler.END


async def cancel_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled. Send /menu to restart.")
    return ConversationHandler.END
