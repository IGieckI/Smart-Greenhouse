import os
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://ml-inference:8000/predict")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Sono il tuo AgriBot. Usa /predict <temp_aria> per predire la temperatura fogliare.")

async def predict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Per favore, inserisci un valore. Esempio: /predict 25.5")
        return

    try:
        air_temp = float(context.args[0])
        # BUG FIX: Invia air_temp al posto di air_temperature
        response = requests.post(INFERENCE_URL, json={"air_temp": air_temp})
        
        if response.status_code == 200:
            pred = response.json().get("predicted_leaf_temperature")
            await update.message.reply_text(f"🌿 Temperatura fogliare predetta: {pred}°C")
        else:
            await update.message.reply_text("⚠️ Il modello non è ancora pronto o si è verificato un errore.")
            
    except ValueError:
        await update.message.reply_text("⚠️ Inserisci un numero valido.")
    except Exception as e:
        await update.message.reply_text("⚠️ Errore di connessione al servizio di inferenza.")

def main():
    if not TOKEN:
        print("[TG-Bot] TELEGRAM_BOT_TOKEN mancante. Esco.")
        return

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("predict", predict))

    print("[TG-Bot] Bot in ascolto...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()