import os
import json
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

def generate_analytics_plots(task_dir: str, task_name: str):
    """
    Legge i file metrics.json dalla cartella models_archive e genera plot analitici.
    I plot vengono salvati in task_dir/analytics_plots.
    """
    archive_dir = os.path.join(task_dir, "models_archive")
    output_dir = os.path.join(task_dir, "analytics_plots")
    os.makedirs(output_dir, exist_ok=True)

    # 1. Raccogli tutti i dati dai JSON
    models_data = {}
    if not os.path.exists(archive_dir):
        print(f"[Analytics] Cartella archivio non trovata per {task_name}.")
        return

    for filename in os.listdir(archive_dir):
        if filename.endswith("_metrics.json"):
            with open(os.path.join(archive_dir, filename), 'r') as f:
                data = json.load(f)
                models_data[data["model_name"]] = data

    if not models_data:
        print(f"[Analytics] Nessun dato JSON trovato per {task_name}.")
        return

    model_names = list(models_data.keys())

    # ==========================================
    # PLOT 1: Metriche di Errore (MAE, RMSE, R^2)
    # ==========================================
    _plot_metrics_comparison(models_data, model_names, task_name, output_dir)

    # ==========================================
    # PLOT 2: Tempi di Esecuzione
    # ==========================================
    _plot_timing_comparison(models_data, model_names, task_name, output_dir)

    # ==========================================
    # PLOT 3: Tabella Iperparametri
    # ==========================================
    _plot_hyperparameters_table(models_data, model_names, task_name, output_dir)

    # ==========================================
    # PLOT 4: Feature Importance (Pie Charts)
    # ==========================================
    _plot_feature_importance(models_data, task_name, output_dir)

    print(f"[Analytics] Plot analitici per {task_name} generati in {output_dir}")


def _plot_metrics_comparison(models_data, model_names, task_name, output_dir):
    maes = [models_data[m]["metrics"].get("MAE", 0) for m in model_names]
    rmses = [models_data[m]["metrics"].get("RMSE", 0) for m in model_names]
    r2s = [max(0, models_data[m]["metrics"].get("R_squared", 0)) for m in model_names] # Tronca R^2 negativi per visibilità

    x = np.arange(len(model_names))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(x - width, maes, width, label='MAE', color='#ff9999')
    ax.bar(x, rmses, width, label='RMSE', color='#66b3ff')
    ax.bar(x + width, r2s, width, label='R² (Scaled)', color='#99ff99')

    ax.set_ylabel('Valore Metrica')
    ax.set_title(f'[{task_name.upper()}] Confronto Metriche per Modello')
    ax.set_xticks(x)
    ax.set_xticklabels(model_names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "metrics_comparison.png"))
    plt.close()

def _plot_timing_comparison(models_data, model_names, task_name, output_dir):
    train_times = [models_data[m]["performance"].get("training_time_seconds", 0) for m in model_names]
    inf_times = [models_data[m]["performance"].get("inference_time_seconds", 0) for m in model_names]

    fig, ax1 = plt.subplots(figsize=(10, 5))
    
    # Usiamo un asse gemello per l'inferenza, poiché i tempi di training sono solitamente molto più alti
    color = 'tab:red'
    ax1.set_xlabel('Modelli')
    ax1.set_ylabel('Training Time (s)', color=color)
    ax1.bar(model_names, train_times, color=color, alpha=0.6, width=0.4, align='center', label='Training')
    ax1.tick_params(axis='y', labelcolor=color)
    plt.xticks(rotation=45, ha="right")

    ax2 = ax1.twinx()
    color = 'tab:blue'
    ax2.set_ylabel('Inference Time (s)', color=color)
    # Sfalsiamo leggermente le barre dell'inferenza
    x_pos = np.arange(len(model_names))
    ax2.bar(x_pos + 0.2, inf_times, color=color, alpha=0.8, width=0.4, align='edge', label='Inference')
    ax2.tick_params(axis='y', labelcolor=color)

    plt.title(f'[{task_name.upper()}] Tempi di Training vs Inferenza')
    fig.tight_layout()
    plt.savefig(os.path.join(output_dir, "timing_comparison.png"))
    plt.close()

def _plot_hyperparameters_table(models_data, model_names, task_name, output_dir):
    fig, ax = plt.subplots(figsize=(12, min(2 + len(model_names)*0.5, 8)))
    ax.axis('off')
    
    cell_text = []
    for m in model_names:
        params = models_data[m].get("best_params", {})
        # Formatta i parametri per renderli leggibili andando a capo se troppo lunghi
        params_str = ", ".join([f"{k.split('__')[-1]}:{v}" for k, v in params.items()])
        if len(params_str) > 80:
            params_str = params_str[:77] + "..."
        cell_text.append([m, params_str, models_data[m]["metrics"].get("MAE", "")])

    table = ax.table(cellText=cell_text, colLabels=['Modello', 'Migliori Iperparametri', 'MAE Result'], 
                     cellLoc='left', loc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2)
    
    plt.title(f'[{task_name.upper()}] Best Hyperparameters Summary', pad=20)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "hyperparameters_table.png"))
    plt.close()

def _plot_feature_importance(models_data, task_name, output_dir):
    top_n = 5
    for model_name, data in models_data.items():
        feat_imp = data.get("feature_importance", {})
        if not feat_imp:
            continue
            
        # Ordina per importanza assoluta
        sorted_feats = sorted(feat_imp.items(), key=lambda item: abs(item[1]), reverse=True)
        
        if len(sorted_feats) > top_n:
            top_feats = sorted_feats[:top_n]
            other_sum = sum([abs(v) for k, v in sorted_feats[top_n:]])
            top_feats.append(("Other Features", other_sum))
        else:
            top_feats = sorted_feats

        labels = [k for k, v in top_feats]
        sizes = [abs(v) for k, v in top_feats]
        
        # Evita errori se tutte le importanze sono zero
        if sum(sizes) == 0:
            continue

        plt.figure(figsize=(8, 8))
        colors = plt.cm.Paired(np.linspace(0, 1, len(labels)))
        plt.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=140)
        plt.title(f'[{task_name.upper()}] Feature Importance - {model_name}')
        
        safe_model_name = model_name.replace(" ", "_").lower()
        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, f"feature_importance_{safe_model_name}.png"))
        plt.close()