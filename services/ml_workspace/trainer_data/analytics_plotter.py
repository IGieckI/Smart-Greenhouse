import os
import json
import shutil
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

def clear_analytics_cache(task_dir: str):
    """Elimina la cartella dei plot per invalidare la cache post-addestramento."""
    output_dir = os.path.join(task_dir, "analytics_plots")
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
        print(f"[Analytics] Cache invalidata per: {task_dir}")

def ensure_analytics_plots(task_dir: str, task_name: str) -> bool:
    """
    Genera i plot analitici in modo idempotente. 
    Ritorna True se i plot sono stati generati o erano già presenti, False in caso di errore.
    """
    archive_dir = os.path.join(task_dir, "models_archive")
    output_dir = os.path.join(task_dir, "analytics_plots")
    
    # --- CONTROLLO IDEMPOTENZA (CACHE HIT) ---
    # Usiamo 'metrics_comparison.png' come file "sentinella"
    if os.path.exists(os.path.join(output_dir, "metrics_comparison.png")):
        return True 

    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(archive_dir):
        print(f"[Analytics] Cartella archivio non trovata per {task_name}.")
        return False

    models_data = {}
    for filename in os.listdir(archive_dir):
        if filename.endswith("_metrics.json"):
            with open(os.path.join(archive_dir, filename), 'r') as f:
                data = json.load(f)
                models_data[data["model_name"]] = data

    if not models_data:
        print(f"[Analytics] Nessun dato JSON trovato per {task_name}.")
        return False

    model_names = list(models_data.keys())

    # Generazione dei plot
    _plot_metrics_comparison(models_data, model_names, task_name, output_dir)
    _plot_timing_comparison(models_data, model_names, task_name, output_dir)
    _plot_hyperparameters_table(models_data, model_names, task_name, output_dir)
    _plot_feature_importance_grid(models_data, model_names, task_name, output_dir)

    print(f"[Analytics] Plot analitici generati e messi in cache in {output_dir}")
    return True

# --- FUNZIONI DI PLOT MANTENUTE (Omesse per brevità, mantieni le tue originali) ---
# def _plot_metrics_comparison(...)
# def _plot_timing_comparison(...)
# def _plot_hyperparameters_table(...)

def _plot_feature_importance_grid(models_data, model_names, task_name, output_dir):
    """
    Genera un'unica figura con una matrice di grafici a torta (es. 2x3) 
    per tutti i modelli addestrati in questo task.
    """
    top_n = 5
    n_models = len(model_names)
    
    # Calcolo layout griglia (es. massimo 3 colonne)
    cols = min(3, n_models)
    rows = (n_models + cols - 1) // cols
    
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 5 * rows))
    
    # Se abbiamo un solo asse (es. 1 modello), forziamolo in un array per uniformità
    if n_models == 1:
        axes = np.array([axes])
    axes = axes.flatten()

    for idx, model_name in enumerate(model_names):
        ax = axes[idx]
        data = models_data[model_name]
        feat_imp = data.get("feature_importance", {})
        
        if not feat_imp:
            ax.axis('off')
            ax.set_title(f'{model_name} (No Data)')
            continue
            
        sorted_feats = sorted(feat_imp.items(), key=lambda item: abs(item[1]), reverse=True)
        
        if len(sorted_feats) > top_n:
            top_feats = sorted_feats[:top_n]
            other_sum = sum([abs(v) for k, v in sorted_feats[top_n:]])
            top_feats.append(("Other", other_sum))
        else:
            top_feats = sorted_feats

        labels = [k for k, v in top_feats]
        sizes = [abs(v) for k, v in top_feats]
        
        if sum(sizes) == 0:
            ax.axis('off')
            ax.set_title(f'{model_name} (Zero Weights)')
            continue

        colors = plt.cm.Paired(np.linspace(0, 1, len(labels)))
        ax.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=140)
        ax.set_title(f'{model_name}')

    # Nascondi eventuali subplot vuoti se n_models non riempie la griglia
    for idx in range(n_models, len(axes)):
        axes[idx].axis('off')

    plt.suptitle(f'[{task_name.upper()}] Feature Importance Matrix', fontsize=16, y=1.05)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "feature_importance_grid.png"), bbox_inches='tight')
    plt.close()