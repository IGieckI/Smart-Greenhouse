from telegram import Update
from telegram.ext import ContextTypes
from config import CONTROLLER_URL, BOARD_MAP, logger
from utils import build_keyboard, fetch_api

async def handle_actuator_routing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "act_menu":
        buttons = [[(f"🌿 Board {k}", f"act_board_{k}")] for k in BOARD_MAP.keys()]
        await query.edit_message_text(
            "🚰 **Actuator Control**\nSelect the greenhouse (Board) to control:", 
            reply_markup=build_keyboard(buttons, "menu_main"),
            parse_mode='Markdown'
        )

    
    elif data.startswith("act_board_"):
        board_key = data.split("_")[2]
        node_id = BOARD_MAP[board_key]
        
        
        buttons = [
            [("🟢 Turn On Pump (60s)", f"act_cmd_{node_id}_pump_255_60")],
            [("🔴 Turn Off Pump", f"act_cmd_{node_id}_pump_0_0")]
        ]
        await query.edit_message_text(
            f"🎛 **Control Board {board_key}** (Node: `{node_id}`)\nChoose an action:", 
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