import sys
import os
import threading
import queue
import time
import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException
import uvicorn


from shared_core.data_sync import sync_clean_bucket
from train import fetch_clean_data, train_environmental_arimas, run_pipeline_for_task
from shared_core.config import *
from shared_core.tasks import TASKS

app = FastAPI(title="ML Trainer API Worker")

INFERENCE_API_URL = "http://ml-inference:8000"

# ==========================================
# GESTIONE CODA DI ADDESTRAMENTO SEQUENZIALE
# ==========================================
# Coda FIFO per gestire i task uno alla volta
training_queue = queue.Queue()

def worker_daemon():
    """
    Questo demone gira in background perennemente.
    Prende un task dalla coda, lo esegue e poi passa al successivo.
    Garantisce che non ci siano MAI due training in esecuzione simultanea.
    """
    print("[Worker Daemon] Inizializzato e in attesa di task...")
    while True:
        # get() è bloccante: il thread dorme finché non c'è qualcosa in coda
        freq_minutes = training_queue.get() 
        
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] [Worker Daemon] Estratto task dalla coda: Addestramento {freq_minutes}m")
            run_full_pipeline_for_freq(freq_minutes)
        except Exception as e:
            print(f"[Worker Daemon] Errore imprevisto durante l'esecuzione del task {freq_minutes}m: {e}")
        finally:
            # Segnala alla coda che questo specifico task è stato completato
            training_queue.task_done()
            print(f"[{time.strftime('%H:%M:%S')}] [Worker Daemon] Task {freq_minutes}m completato. Attesa prossimi task...")


def run_full_pipeline_for_freq(freq_minutes: int):
    """La logica core del training (uguale a prima, ma ora chiamata dal demone)."""
    try:
        print(f"[Pipeline] Avvio processo per frequenza {freq_minutes}m...")
        
        # 1. Pulizia e Sincronizzazione
        sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq_minutes)
        
        # 2. Estrazione dati
        df_clean = fetch_clean_data(freq_minutes)
        if df_clean.empty:
            print(f"[Pipeline] Errore: Dati insufficienti per {freq_minutes}m.")
            return
            
        # 3. Training Ambientale
        all_env_features = TASKS["t1"]["features"]
        env_output_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", "env_forecasters")
        train_environmental_arimas(df_clean, all_env_features, env_output_dir, freq_minutes)
        
        # 4. Training Modelli ML
        for task_name, config in TASKS.items():
            run_pipeline_for_task(task_name, config, df_clean, freq_minutes)
            
        print(f"[Pipeline] Processo per {freq_minutes}m completato con successo!")
        
        # 5. Avvisiamo l'Inference
        try:
            res = requests.post(f"{INFERENCE_API_URL}/reload-models", timeout=5)
            if res.status_code == 200:
                print(f"[Pipeline] ml-inference aggiornato con i nuovi modelli a {freq_minutes}m!")
        except Exception as ping_err:
            print(f"[Pipeline] Impossibile contattare ml-inference per il reload: {ping_err}")
            
    except Exception as e:
        print(f"[Pipeline] Errore critico durante il training a {freq_minutes}m: {e}")



@app.on_event("startup")
def bootstrap_check():
    """Controlla i modelli e avvia il demone della coda."""
    
    # 1. Avvia il thread demone in background (daemon=True significa che morirà quando muore l'app FastAPI)
    threading.Thread(target=worker_daemon, daemon=True).start()

    # 2. Controlla il cold start
    needs_training = False
    if not os.path.exists(BASE_MODEL_DIR):
        needs_training = True
    else:
        subdirs = [d for d in os.listdir(BASE_MODEL_DIR) if os.path.isdir(os.path.join(BASE_MODEL_DIR, d))]
        if not subdirs:
            needs_training = True

    if needs_training:
        print("\n[Trainer API] ⚠️ Modelli non trovati al boot (Cold Start).")
        print(f"[Trainer API] Accodamento auto-addestramento bootstrap: {DEFAULT_FREQS}")
        # Inseriamo i task nella coda (verranno processati uno alla volta)
        for freq in DEFAULT_FREQS:
            training_queue.put(freq)
    else:
        print("\n[Trainer API] ✅ Modelli esistenti rilevati. Sistema pronto per richieste on-demand.")


@app.post("/train/standard")
def trigger_standard_training():
    """Accoda il training per le frequenze standard (6m e 2m)."""
    for freq in DEFAULT_FREQS:
        training_queue.put(freq)
    
    q_size = training_queue.qsize()
    return {"message": f"Training accodato per le frequenze: {DEFAULT_FREQS} minuti.", "queue_size": q_size}


@app.post("/train/custom/{freq_minutes}")
def trigger_custom_training(freq_minutes: int):
    """Accoda il training per una frequenza customizzata dall'utente (es. 3m)."""
    if freq_minutes <= 0 or freq_minutes > TARGET_FREQ_MINUTES:
        raise HTTPException(status_code=400, detail=f"Frequenza non valida. Deve essere tra 1 e {TARGET_FREQ_MINUTES}.")
    
    # NUOVO BLOCCO: Controllo Divisore
    if TARGET_FREQ_MINUTES % freq_minutes != 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Operazione non consentita. La frequenza {freq_minutes}m non è un divisore esatto del target {TARGET_FREQ_MINUTES}m."
        )
    training_queue.put(freq_minutes)
    
    q_size = training_queue.qsize()
    return {"message": f"Training custom ({freq_minutes}m) accodato.", "queue_size": q_size}


@app.get("/queue/status")
def get_queue_status():
    """Restituisce il numero di task attualmente in coda."""
    return {"tasks_in_queue": training_queue.qsize()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)