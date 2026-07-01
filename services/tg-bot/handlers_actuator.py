from telegram import Update
from telegram.ext import ContextTypes
from config import CONTROLLER_URL, BOARD_MAP, logger
from utils import build_keyboard, fetch_api

async def handle_actuator_routing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # 1. Mostra la scelta delle Board
    if data == "act_menu":
        buttons = [[(f"🌿 Board {k}", f"act_board_{k}")] for k in BOARD_MAP.keys()]
        await query.edit_message_text(
            "🚰 **Actuator Control**\nSeleziona la serra (Board) da controllare:", 
            reply_markup=build_keyboard(buttons, "menu_main"),
            parse_mode='Markdown'
        )

    # 2. Mostra i comandi della pompa per la Board selezionata
    elif data.startswith("act_board_"):
        board_key = data.split("_")[2]
        node_id = BOARD_MAP[board_key]
        
        # Struttura del callback: act_cmd_NODEID_ACTUATOR_VALUE_DURATION
        buttons = [
            [("🟢 Accendi Pompa (60s)", f"act_cmd_{node_id}_pump_255_60")],
            [("🔴 Spegni Pompa", f"act_cmd_{node_id}_pump_0_0")]
        ]
        await query.edit_message_text(
            f"🎛 **Controllo Board {board_key}** (Node: `{node_id}`)\nScegli un'azione:", 
            reply_markup=build_keyboard(buttons, "act_menu"),
            parse_mode='Markdown'
        )

    # 3. Invia il payload al controller
    elif data.startswith("act_cmd_"):
        # Estrai i parametri dal callback_data
        parts = data.split("_")
        node_id = parts[2]
        actuator = parts[3]
        value = int(parts[4])
        duration = int(parts[5])

        wait_msg = await query.message.reply_text("⏳ Invio del comando al Controller in corso...")
        
        # ATTENZIONE: Usiamo le chiavi esatte che si aspetta server.js alla riga 168
        payload = {
            "node_id": int(node_id),
            "actuator": actuator,
            "value": value,
            "duration_s": duration
        }

        logger.info(f"Invio comando attuatore: {payload}")
        
        # Usa fetch_api (che supporta le richieste POST passando 'payload')
        res = await fetch_api(f"{CONTROLLER_URL}/api/command", payload=payload)

        if res and res.get("status") == "sent":
            await wait_msg.edit_text(
                f"✅ **Comando Inviato!**\n"
                f"**Star ID:** `{res.get('star_id')}`\n"
                f"**Topic MQTT:** `{res.get('topic')}`\n"
                f"**Azione:** {actuator.upper()} -> {value} (Durata: {duration}s)",
                parse_mode='Markdown'
            )
        else:
            error_msg = res.get("error", "Impossibile contattare il Controller.") if res else "Impossibile contattare il Controller."
            await wait_msg.edit_text(f"⚠️ **Errore HTTP:** {error_msg}", parse_mode='Markdown')