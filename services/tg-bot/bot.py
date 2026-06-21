import os
import io
import logging
import httpx
import pandas as pd
import matplotlib.pyplot as plt
from datetime import timedelta
from telegram import Update, InputMediaPhoto, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from influxdb_client import InfluxDBClient

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://ml-inference:8000")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_org")
BUCKET = "sensor_data"

def fetch_history_data(board_id: str, hours: int) -> pd.DataFrame:
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query = f'''
        from(bucket: "{BUCKET}")
          |> range(start: -{hours}h)
          |> filter(fn: (r) => r._measurement == "sensor_measurements")
          |> filter(fn: (r) => r.id_board == "{board_id}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
    '''
    try:
        df = client.query_api().query_data_frame(query)
        if isinstance(df, list):
            if len(df) == 0: return pd.DataFrame()
            df = pd.concat(df, ignore_index=True)
        if not df.empty:
            df.set_index('_time', inplace=True)
            df.sort_index(inplace=True)
        return df
    except Exception as e:
        logger.error(f"Errore recupero storico Influx: {e}")
        return pd.DataFrame()


def create_prediction_plot(df_hist: pd.DataFrame, predictions: list) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz='UTC')
    
    if not df_hist.empty and 'leaf_temp' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['leaf_temp'])
        if not df_plot.empty:
            plt.plot(df_plot.index, df_plot['leaf_temp'], label='Storico Reale', color='green', linewidth=2)
            last_time = df_plot.index[-1]
    
    # CORREZIONE: Step a 6 minuti anziché 5!
    future_times = [last_time + timedelta(minutes=6 * (i + 1)) for i in range(len(predictions))]
    plt.plot(future_times, predictions, label='Predizione Modello', color='orange', linestyle='dashed', marker='o', linewidth=2)
    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Adesso')

    plt.title('Temperatura Fogliare: Storico vs Predizione Futura')
    plt.xlabel('Orario')
    plt.ylabel('Temperatura (°C)')
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf


def create_semantic_category_plots(df_hist: pd.DataFrame) -> list[io.BytesIO]:
    plots = []
    categories = {
        "Temperature (°C)": (['air_temp', 'leaf_temp', 'water_temp'], ['red', 'green', 'blue']),
        "Luminosità (Lux)": (['light_lux'], ['orange']),
        "Pressione (hPa)": (['pressure'], ['purple']),
        "Umidità e Umidità Suolo (%)": (['humidity', 'soil_moisture'], ['cyan', 'brown']),
        "Qualità Acqua (TDS - ppm)": (['tds'], ['olive'])
    }

    for title, (columns, colors) in categories.items():
        available_cols = [c for c in columns if c in df_hist.columns]
        if not available_cols:
            continue
            
        plt.figure(figsize=(10, 4))
        for idx, col in enumerate(available_cols):
            df_plot = df_hist.dropna(subset=[col])
            if not df_plot.empty:
                plt.plot(df_plot.index, df_plot[col], label=col, color=colors[idx % len(colors)], linewidth=2)
        
        plt.title(title)
        plt.xlabel('Orario')
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.xticks(rotation=45)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        buf.seek(0)
        plt.close()
        plots.append(buf)
        
    return plots


# --- COMANDI TELEGRAM ---

async def setup_commands(application: Application):
    """Inizializza il menu a tendina di Telegram per l'autocompletamento comandi."""
    commands = [
        BotCommand("start", "Mostra il messaggio di benvenuto e la guida"),
        BotCommand("help", "Lista dei comandi disponibili"),
        BotCommand("predict", "Avvia una predizione automatica [t1|t2|t3] [board_id]"),
        BotCommand("history", "Visualizza i grafici dei sensori [ore] [board_id]"),
        BotCommand("info", "Mostra le metriche di un modello [t1|t2|t3]"),
        BotCommand("manual", "Predizione manuale (simulazione what-if)")
    ]
    await application.bot.set_my_commands(commands)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **AgriBot - Pannello di Controllo**\n\n"
        "Ecco cosa posso fare per te:\n"
        "🔹 `/predict [t1|t2|t3] [board_id]` - Predizione (es. `/predict t3 9`)\n"
        "🔹 `/history [ore] [board_id]` - Grafici storici (es. `/history 12 9`)\n"
        "🔹 `/info [t1|t2|t3]` - Mostra le metriche tecniche del modello\n"
        "🔹 `/manual [t1|t2|t3] [board_id] [air_t] [hum] [press] [wat_t] [tds] [soil] [lux]` - Predizione manuale\n\n"
        "**Info Task:**\n"
        "`t1`: Puntuale adesso\n"
        "`t2`: Forecast 3h (Senza storia target)\n"
        "`t3`: Forecast 3h (Autoregressivo)"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = context.args[0].lower() if len(context.args) > 0 else "t3"
    
    if task not in ["t1", "t2", "t3"]:
        await update.message.reply_text("⚠️ Task non valido. Usa 't1', 't2' o 't3'.")
        return

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(f"{INFERENCE_URL}/info/{task}", timeout=10.0)
            
        if res.status_code == 200:
            data = res.json()
            metrics = data.get("metrics", {})
            
            text = (
                f"🧠 **Info Modello ({task.upper()})**\n"
                f"Modello Selezionato: `{data.get('best_model')}`\n\n"
                f"📊 **Metriche:**\n"
                f"- MAE: {metrics.get('MAE', 'N/D')} °C\n"
                f"- RMSE: {metrics.get('RMSE', 'N/D')} °C\n"
                f"- R_squared: {metrics.get('R_squared', 'N/D')}\n"
            )
            await update.message.reply_text(text, parse_mode='Markdown')
        else:
            await update.message.reply_text("⚠️ Metriche non trovate. Il training è stato eseguito?")
    except Exception as e:
        logger.error(f"Errore /info: {e}")
        await update.message.reply_text("⚠️ Impossibile contattare il server ML.")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hours = int(context.args[0]) if len(context.args) > 0 else 6
    board_id = context.args[1] if len(context.args) > 1 else "9"
    
    wait_message = await update.message.reply_text(f"📊 Recupero i dati delle ultime {hours} ore per la board {board_id}...")

    df_hist = fetch_history_data(board_id, hours)
    if df_hist.empty:
        await wait_message.edit_text("⚠️ Nessun dato trovato per questa board nel periodo selezionato.")
        return

    plots = create_semantic_category_plots(df_hist)
    
    if not plots:
        await wait_message.edit_text("⚠️ Dati presenti ma insufficienti per generare i grafici.")
        return

    media_group = [InputMediaPhoto(media=buf) for buf in plots]
    await update.message.reply_media_group(media=media_group)
    await wait_message.delete()


async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task = context.args[0].lower() if len(context.args) > 0 else "t3"
    board_id = context.args[1] if len(context.args) > 1 else "9"
    
    wait_message = await update.message.reply_text(f"🔄 Elaborazione predizione {task.upper()} per board {board_id}...")

    try:
        endpoint = f"{INFERENCE_URL}/predict/{task}/latest?board_id={board_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, timeout=30.0)
            
        if response.status_code == 200:
            data = response.json()
            predictions = data.get("predictions", [])
            model_used = data.get("model_used", "Sconosciuto")
            
            df_hist = fetch_history_data(board_id, hours=3)
            photo_buf = create_prediction_plot(df_hist, predictions)
            
            # CORREZIONE: Stampa l'output usando lo step corretto di 6 min
            pred_text = "\n".join([f"+{6*(i+1)} min: **{p}°C**" for i, p in enumerate(predictions)])
            # Se la lista è lunga (30 step), mostra solo un estratto o formatta diversamente per non intasare la chat
            if len(predictions) > 10:
                pred_text = "\n".join([f"+{6*(i+1)} min: **{p}°C**" for i, p in enumerate(predictions) if (i+1) % 5 == 0])
                pred_text = "_Mostro uno snapshot ogni 30 minuti:_\n" + pred_text

            messaggio_finale = f"🌿 **Predizione Fogliare ({task.upper()})**\nModello: `{model_used}`\n\n{pred_text}"
            
            await update.message.reply_photo(photo=photo_buf, caption=messaggio_finale, parse_mode='Markdown')
            await wait_message.delete()
        else:
            err = response.json().get("detail", "Errore sconosciuto")
            await wait_message.edit_text(f"⚠️ **Errore API:** {err}", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Errore /predict: {e}")
        await wait_message.edit_text("⚠️ Errore imprevisto di connessione.")


async def predict_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 9:
        await update.message.reply_text(
            "⚠️ Formato errato. Uso corretto:\n"
            "`/manual [task] [board] [air_t] [hum] [press] [wat_t] [tds] [soil] [lux]`\n"
            "Esempio: `/manual t3 9 25.5 60.0 1013 22.0 400 45 10000`", 
            parse_mode='Markdown'
        )
        return
        
    task, board_id = context.args[0], context.args[1]
    wait_message = await update.message.reply_text(f"🧪 Simulazione manuale {task.upper()} in corso...")

    try:
        payload = {
            "air_temp": float(context.args[2]),
            "humidity": float(context.args[3]),
            "pressure": float(context.args[4]),
            "water_temp": float(context.args[5]),
            "tds": float(context.args[6]),
            "soil_moisture": float(context.args[7]),
            "light_lux": float(context.args[8])
        }

        endpoint = f"{INFERENCE_URL}/predict/{task}/manual?board_id={board_id}"
        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=30.0)
            
        if response.status_code == 200:
            predictions = response.json().get("predictions", [])
            
            # CORREZIONE a 6 minuti e troncamento logico
            if len(predictions) > 10:
                pred_text = "\n".join([f"+{6*(i+1)} min: **{p}°C**" for i, p in enumerate(predictions) if (i+1) % 5 == 0])
                pred_text = "_Snapshot predittivo (ogni 30 min):_\n" + pred_text
            else:
                pred_text = "\n".join([f"+{6*(i+1)} min: **{p}°C**" for i, p in enumerate(predictions)])
                
            await wait_message.edit_text(f"🧪 **Simulazione Completata ({task.upper()})**\n\n{pred_text}", parse_mode='Markdown')
        else:
            await wait_message.edit_text(f"⚠️ **Errore:** {response.json().get('detail')}")

    except ValueError:
        await wait_message.edit_text("⚠️ Assicurati di inserire solo numeri per i valori (usa il punto per i decimali).")
    except Exception as e:
        logger.error(f"Errore /manual: {e}")
        await wait_message.edit_text("⚠️ Errore di rete.")


def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN mancante nel file .env! Esco.")
        return

    logger.info("Avvio di AgriBot in corso...")
    
    # Agganciamo setup_commands al post_init
    application = Application.builder().token(TOKEN).post_init(setup_commands).build()
    
    application.add_handler(CommandHandler("start", help_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("predict", predict))
    application.add_handler(CommandHandler("manual", predict_manual))

    logger.info("Bot in ascolto...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()