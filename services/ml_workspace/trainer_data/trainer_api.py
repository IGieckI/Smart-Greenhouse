import sys
import os
import threading
import queue
import time
import requests
import json
from fastapi import FastAPI, BackgroundTasks, HTTPException
import uvicorn
from fastapi.responses import FileResponse
from fastapi import Path

import shutil

from analytics_plotter import ensure_analytics_plots, ensure_global_analytics

from shared_core.data_sync import sync_clean_bucket
from train import fetch_clean_data, train_environmental_prophet, run_pipeline_for_task
from shared_core.config import *
from shared_core.tasks import TASKS

app = FastAPI(title="ML Trainer API Worker")
INFERENCE_API_URL = "http://ml-inference:8000"

# TRAINING QUEUE MANAGEMENT
training_queue = queue.Queue()

def worker_daemon():
    print("[Worker Daemon] Initialized and waiting for tasks...")
    while True:
        freq_minutes = training_queue.get() 
        try:
            print(f"\n[Worker Daemon] Extracted task from queue: Training {freq_minutes}m")
            run_full_pipeline_for_freq(freq_minutes)
        except Exception as e:
            print(f"[Worker Daemon] Unexpected error executing task {freq_minutes}m: {e}")
        finally:
            training_queue.task_done()
            print(f"[Worker Daemon] Task {freq_minutes}m completed. Awaiting new tasks...")

def run_full_pipeline_for_freq(freq_minutes: int):
    try:
        print(f"[Pipeline] Starting process for frequency {freq_minutes}m...")
        
        sync_clean_bucket(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, freq_minutes=freq_minutes)
        
        df_clean = fetch_clean_data(freq_minutes)
        if df_clean.empty:
            print(f"[Pipeline] Error: Insufficient data for {freq_minutes}m.")
            return
            
        all_env_features = TASKS["t1"]["features"]
        env_output_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", "env_forecasters")
        train_environmental_prophet(df_clean, all_env_features, env_output_dir, freq_minutes)
        
        for task_name, config in TASKS.items():
            run_pipeline_for_task(task_name, config, df_clean, freq_minutes)
            
        print(f"[Pipeline] Process for {freq_minutes}m completed successfully!")
        
        # Explicitly delete the previous global matrix
        global_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", "global_analytics")
        if os.path.exists(global_dir):
            shutil.rmtree(global_dir)
            print(f"[Pipeline] Cleaned up previous global analytics for {freq_minutes}m.")

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
    threading.Thread(target=worker_daemon, daemon=True).start()

    needs_training = not os.path.exists(BASE_MODEL_DIR) or not [
        d for d in os.listdir(BASE_MODEL_DIR) if os.path.isdir(os.path.join(BASE_MODEL_DIR, d))
    ]
    
    if needs_training:
        print("\n[Trainer API] No models found at boot (Cold Start).")
        for freq in DEFAULT_FREQS: 
            training_queue.put(freq)
    else:
        print("\n[Trainer API] Existing models detected. System ready for on-demand requests.")

@app.post("/train/standard")
def trigger_standard_training():
    
    tmp = ','.join([f"{i}" for i in DEFAULT_FREQS])
    for freq in DEFAULT_FREQS: 
        training_queue.put(freq)

    return {
        "message": f"Training queued for minute frequencies: {tmp}.", 
        "queue_size": training_queue.qsize()
        }

@app.post("/train/custom/{freq_minutes}")
def trigger_custom_training(freq_minutes: int):
    if freq_minutes <= 0 or freq_minutes > TARGET_FREQ_MINUTES or TARGET_FREQ_MINUTES % freq_minutes != 0:
        raise HTTPException(status_code=400, detail="Invalid frequency or not a divisor of the target.")
    training_queue.put(freq_minutes)
    return {"message": f"Custom training ({freq_minutes}m) queued.", "queue_size": training_queue.qsize()}

@app.get("/queue/status")
def get_queue_status():
    return {"tasks_in_queue": training_queue.qsize()}

# ==========================================
# ANALYTICS & PLOTTING API
# ==========================================

@app.get("/analytics/{freq_minutes}/summary")
def get_global_summary(freq_minutes: int):
    """Dynamically explores the directory and returns all tasks and their trained models."""
    base_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m")
    if not os.path.exists(base_dir):
        raise HTTPException(status_code=404, detail="Frequency not found. Train the system first.")
        
    tasks = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d)) and d != "global_analytics" and d != "env_forecasters"]
    
    summary = {}
    for task in tasks:
        archive_dir = os.path.join(base_dir, task, "models_archive")
        if not os.path.exists(archive_dir): continue
            
        json_files = [f for f in os.listdir(archive_dir) if f.endswith("_metrics.json")]
        models_info = {}
        for jf in json_files:
            with open(os.path.join(archive_dir, jf), 'r') as f:
                data = json.load(f)
                models_info[data["model_name"]] = data["metrics"]
                
        if models_info:
            summary[task] = models_info
            
    return {"freq_minutes": freq_minutes, "tasks_available": list(summary.keys()), "details": summary}

@app.get("/analytics/{freq_minutes}/global-matrix")
def get_global_matrix(freq_minutes: int):
    """Generates and returns the comparative matrix (Heatmap) of all tasks vs models."""
    base_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m")
    
    success = ensure_global_analytics(base_dir, freq_minutes)
    if not success:
         raise HTTPException(status_code=404, detail="Insufficient data to generate the global matrix.")
         
    file_path = os.path.join(base_dir, "global_analytics", "global_mae_matrix.png")
    return FileResponse(path=file_path, media_type="image/png", filename=f"global_matrix_{freq_minutes}m.png")

@app.get("/analytics/{freq_minutes}/{task_name}/plots")
def list_analytics_plots(freq_minutes: int, task_name: str):
    task_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name)
    success = ensure_analytics_plots(task_dir, task_name)
    if not success:
        raise HTTPException(status_code=404, detail="No JSON data found. Train the model first.")

    analytics_dir = os.path.join(task_dir, "analytics_plots")
    plots = [f for f in os.listdir(analytics_dir) if f.endswith(".png")]
    
    return {
        "freq_minutes": freq_minutes, "task": task_name,
        "available_plots": plots,
        "download_url_template": f"/analytics/{freq_minutes}/{task_name}/plot/{{filename}}"
    }

@app.get("/analytics/{freq_minutes}/{task_name}/plot/{filename}")
def get_analytics_plot(freq_minutes: int, task_name: str, filename: str):
    task_dir = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", task_name)
    file_path = os.path.join(task_dir, "analytics_plots", filename)
    
    if not os.path.exists(file_path): 
        ensure_analytics_plots(task_dir, task_name)
    
    if not os.path.exists(file_path): 
        raise HTTPException(status_code=404, detail="Graph not found")
        
    return FileResponse(path=file_path, media_type="image/png", filename=f"{task_name}_{freq_minutes}m_{filename}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)

@app.get("/analytics/global-grids")
def generate_all_global_grids():
    """
    100% Non-Parametric and Self-Sufficient API: 
    Scans all processed frequencies and explores the tasks for each.
    Leverages the cache: generates missing plots transparently.
    Returns an organized JSON with the URLs of all generated grids.
    """
    if not os.path.exists(BASE_MODEL_DIR):
        raise HTTPException(status_code=404, detail="Models directory not found. Start a training.")
        
    freq_dirs = [d for d in os.listdir(BASE_MODEL_DIR) if d.endswith("m") and os.path.isdir(os.path.join(BASE_MODEL_DIR, d))]
    
    result = {}
    for d in freq_dirs:
        freq = int(d.replace("m", ""))
        base_dir = os.path.join(BASE_MODEL_DIR, d)
        
        # Trigger execution (Idempotent) for this specific frequency
        ensure_global_analytics(base_dir, freq)
        
        global_dir = os.path.join(base_dir, "global_analytics")
        if os.path.exists(global_dir):
            plots = [f for f in os.listdir(global_dir) if f.endswith(".png")]
            result[d] = {
                "available_grids": plots,
                "download_urls": [f"/analytics/download-grid/{freq}/{p}" for p in plots]
            }
            
    return {"status": "success", "data": result}

@app.get("/analytics/download-grid/{freq_minutes}/{filename}")
def download_global_grid(freq_minutes: int, filename: str):
    """Physically serves the requested image."""
    file_path = os.path.join(BASE_MODEL_DIR, f"{freq_minutes}m", "global_analytics", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Grid not found. Call /analytics/global-grids first")
    return FileResponse(path=file_path, media_type="image/png", filename=filename)