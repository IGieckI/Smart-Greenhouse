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
# SEQUENTIAL TRAINING QUEUE MANAGEMENT
# ==========================================
# FIFO Queue to process training tasks sequentially (prevents OOM)
training_queue = queue.Queue()

def worker_daemon():
    """
    Background daemon that constantly watches the queue.
    Pulls a task, executes it, and proceeds to the next.
    Guarantees no concurrent training processes run simultaneously.
    """
    print("[Worker Daemon] Initialized and waiting for tasks...")
    while True:
        # get() is blocking: the thread sleeps until a task arrives
        freq_minutes = training_queue.get() 
        
        try:
            print(f"\n[Worker Daemon] Extracted task from queue: Training {freq_minutes}m")
            run_full_pipeline_for_freq(freq_minutes)
        except Exception as e:
            print(f"[Worker Daemon] Unexpected error executing task {freq_minutes}m: {e}")
        finally:
            # Signal the queue that this specific task is complete
            training_queue.task_done()
            print(f"[Worker Daemon] Task {freq_minutes}m completed. Awaiting new tasks...")

def run_full_pipeline_for_freq(freq_minutes: int):
    """Core training logic called by the background daemon."""
    try:
        print(f"[Pipeline] Starting process for frequency {freq_minutes}m...")
        
        # 1. Clean & Sync
        sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq_minutes)
        
        # 2. Extract Data
        df_clean = fetch_clean_data(freq_minutes)
        if df_clean.empty:
            print(f"[Pipeline] Error: Insufficient data for {freq_minutes}m.")
            return
            
        # 3. Train Environmental Predictors
        all_env_features = TASKS["t1"]["features"]
        env_output_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", "env_forecasters")
        train_environmental_arimas(df_clean, all_env_features, env_output_dir, freq_minutes)
        
        # 4. Train ML Models
        for task_name, config in TASKS.items():
            run_pipeline_for_task(task_name, config, df_clean, freq_minutes)
            
        print(f"[Pipeline] Process for {freq_minutes}m completed successfully!")
        
        # 5. Notify Inference API to reload models in RAM
        try:
            res = requests.post(f"{INFERENCE_API_URL}/reload-models", timeout=5)
            if res.status_code == 200:
                print(f"[Pipeline] ml-inference successfully reloaded new {freq_minutes}m models!")
        except Exception as ping_err:
            print(f"[Pipeline] Failed to contact ml-inference for model reload: {ping_err}")
            
    except Exception as e:
        print(f"[Pipeline] Critical error during {freq_minutes}m training: {e}")

@app.on_event("startup")
def bootstrap_check():
    """Checks for existing models and boots the queue daemon."""
    
    # 1. Start daemon thread (daemon=True means it dies when FastAPI dies)
    threading.Thread(target=worker_daemon, daemon=True).start()

    # 2. Check for Cold Start
    needs_training = not os.path.exists(BASE_MODEL_DIR) or not [
        d for d in os.listdir(BASE_MODEL_DIR) if os.path.isdir(os.path.join(BASE_MODEL_DIR, d))
    ]

    if needs_training:
        print("\n[Trainer API] ⚠️ No models found at boot (Cold Start).")
        print(f"[Trainer API] Queuing auto-bootstrap training for: {DEFAULT_FREQS}")
        for freq in DEFAULT_FREQS:
            training_queue.put(freq)
    else:
        print("\n[Trainer API] ✅ Existing models detected. System ready for on-demand requests.")


@app.post("/train/standard")
def trigger_standard_training():
    """Queues training for standard default frequencies."""
    for freq in DEFAULT_FREQS:
        training_queue.put(freq)
    
    return {"message": f"Training queued for frequencies: {DEFAULT_FREQS} minutes.", "queue_size": training_queue.qsize()}

@app.post("/train/custom/{freq_minutes}")
def trigger_custom_training(freq_minutes: int):
    """Queues training for a user-customized frequency (e.g., 3m)."""
    if freq_minutes <= 0 or freq_minutes > TARGET_FREQ_MINUTES:
        raise HTTPException(status_code=400, detail=f"Invalid frequency. Must be between 1 and {TARGET_FREQ_MINUTES}.")
    
    # Divisor Check constraint
    if TARGET_FREQ_MINUTES % freq_minutes != 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Operation not allowed. The frequency {freq_minutes}m is not an exact divisor of the {TARGET_FREQ_MINUTES}m target."
        )
        
    training_queue.put(freq_minutes)
    return {"message": f"Custom training ({freq_minutes}m) queued.", "queue_size": training_queue.qsize()}

@app.get("/queue/status")
def get_queue_status():
    """Returns the number of tasks currently in the training queue."""
    return {"tasks_in_queue": training_queue.qsize()}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)