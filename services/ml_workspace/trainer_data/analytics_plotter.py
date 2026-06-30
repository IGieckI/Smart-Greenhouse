import os
import json
import shutil
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

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
    
    # 1. La fonte di verità: Esistono i JSON?
    if not os.path.exists(archive_dir):
        return False
        
    json_files = [f for f in os.listdir(archive_dir) if f.endswith("_metrics.json")]
    if not json_files:
        return False # Nessun dato da plottare
        
    # 2. Controllo Idempotenza: I plot ci sono già?
    if os.path.exists(os.path.join(output_dir, "metrics_comparison.png")):
        return True 

    os.makedirs(output_dir, exist_ok=True)

    models_data = {}
    for filename in json_files:
        with open(os.path.join(archive_dir, filename), 'r') as f:
            data = json.load(f)
            models_data[data["model_name"]] = data

    model_names = list(models_data.keys())

    # Generazione dei plot specifici del task
    _plot_metrics_comparison(models_data, model_names, task_name, output_dir)
    _plot_timing_comparison(models_data, model_names, task_name, output_dir)
    _plot_hyperparameters_table(models_data, model_names, task_name, output_dir)
    _plot_feature_importance_grid(models_data, model_names, task_name, output_dir)

    print(f"[Analytics] Plot analitici generati in {output_dir}")
    return True

def ensure_global_analytics(base_dir: str, freq_minutes: int) -> bool:
    """
    Scansiona dinamicamente tutti i task presenti, estrae tutti i gruppi di metriche
    e genera una matrice comparativa (Task vs Model) per ciascun gruppo di informazioni.
    """
    global_dir = os.path.join(base_dir, "global_analytics")
    
    # Scansioniamo tutte le cartelle che sembrano task (es. t1, t2...)
    tasks = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d)) and d.startswith("t")]
    if not tasks: return False
        
    all_data = {}
    all_models = set()
    
    # 1. Parsing di tutti i JSON
    for task in tasks:
        archive_dir = os.path.join(base_dir, task, "models_archive")
        if not os.path.exists(archive_dir): continue
            
        json_files = [f for f in os.listdir(archive_dir) if f.endswith("_metrics.json")]
        if not json_files: continue
            
        all_data[task] = {}
        for jf in json_files:
            with open(os.path.join(archive_dir, jf), 'r') as f:
                try:
                    data = json.load(f)
                    m_name = data["model_name"]
                    all_data[task][m_name] = data
                    all_models.add(m_name)
                except Exception:
                    pass

    if not all_data: return False
    
    os.makedirs(global_dir, exist_ok=True)
    model_names = sorted(list(all_models))
    
    # 2. Generazione Idempotente di 1 Immagine per ogni Gruppo di Informazione
    _generate_global_heatmap(all_data, model_names, "MAE", "metrics", global_dir, freq_minutes)
    _generate_global_heatmap(all_data, model_names, "RMSE", "metrics", global_dir, freq_minutes)
    _generate_global_heatmap(all_data, model_names, "R_squared", "metrics", global_dir, freq_minutes, cmap="RdYlGn")
    _generate_global_heatmap(all_data, model_names, "training_time_seconds", "performance", global_dir, freq_minutes, cmap="Reds")
    _generate_global_heatmap(all_data, model_names, "inference_time_seconds", "performance", global_dir, freq_minutes, cmap="Reds")
    
    # Griglia Testuale per gli Iperparametri
    _generate_global_params_grid(all_data, model_names, global_dir, freq_minutes)
    
    return True

def _generate_global_heatmap(all_data, model_names, key, category, out_dir, freq, cmap="YlGnBu"):
    file_path = os.path.join(out_dir, f"global_{key}.png")
    if os.path.exists(file_path): return # Skip se esiste (Idempotenza)

    df_dict = {}
    for task, models in all_data.items():
        df_dict[task] = {}
        for m in model_names:
            if m in models:
                df_dict[task][m] = models[m].get(category, {}).get(key, np.nan)
            else:
                df_dict[task][m] = np.nan
                
    df_matrix = pd.DataFrame(df_dict).T
    df_matrix.sort_index(inplace=True)
    
    plt.figure(figsize=(max(8, len(model_names) * 1.5), max(4, len(df_matrix) * 0.8)))
    sns.heatmap(df_matrix, annot=True, cmap=cmap, fmt=".3f", linewidths=.5, cbar_kws={'label': key.replace("_", " ").title()})
    plt.title(f"Global Matrix: {key.replace('_', ' ').upper()} ({freq}m)", pad=20)
    plt.ylabel("Task")
    plt.xlabel("Model")
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()

def _generate_global_params_grid(all_data, model_names, out_dir, freq):
    file_path = os.path.join(out_dir, "global_best_params.png")
    if os.path.exists(file_path): return # Skip se esiste
    
    tasks = sorted(list(all_data.keys()))
    cell_text = []
    
    # Costruiamo la matrice bidimensionale di testi
    for t in tasks:
        row = []
        for m in model_names:
            if m in all_data[t]:
                params = all_data[t][m].get("best_params", {})
                # Format: mandiamo a capo ogni iperparametro per adattarlo alla cella
                p_str = "\n".join([f"{k.split('__')[-1]}: {v}" for k, v in params.items()])
                row.append(p_str if p_str else "Default")
            else:
                row.append("N/A")
        cell_text.append(row)
        
    if not cell_text: return

    # Calcoliamo la dimensione dell'immagine per far respirare il testo
    fig, ax = plt.subplots(figsize=(max(8, 3 * len(model_names)), max(4, 1.5 * len(tasks))))
    ax.axis('off')
    
    table = ax.table(cellText=cell_text, rowLabels=[t.upper() for t in tasks], colLabels=model_names, loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 4) # Aumentiamo l'altezza delle righe per il testo \n
    
    plt.title(f"Global Matrix: BEST HYPERPARAMETERS ({freq}m)", pad=20, fontsize=14)
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    
# ==========================================
# INTERNAL PLOTTING UTILS
# ==========================================

def _plot_metrics_comparison(models_data, model_names, task_name, output_dir):
    maes = [models_data[m]["metrics"].get("MAE", 0) for m in model_names]
    rmses = [models_data[m]["metrics"].get("RMSE", 0) for m in model_names]
    r2s = [max(0, models_data[m]["metrics"].get("R_squared", 0)) for m in model_names]

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
    
    color = 'tab:red'
    ax1.set_xlabel('Modelli')
    ax1.set_ylabel('Training Time (s)', color=color)
    ax1.bar(model_names, train_times, color=color, alpha=0.6, width=0.4, align='center', label='Training')
    ax1.tick_params(axis='y', labelcolor=color)
    plt.xticks(rotation=45, ha="right")

    ax2 = ax1.twinx()
    color = 'tab:blue'
    ax2.set_ylabel('Inference Time (s)', color=color)
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

def _plot_feature_importance_grid(models_data, model_names, task_name, output_dir):
    top_n = 5
    n_models = len(model_names)
    
    cols = min(3, n_models)
    rows = (n_models + cols - 1) // cols
    
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 5 * rows))
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

    for idx in range(n_models, len(axes)):
        axes[idx].axis('off')

    plt.suptitle(f'[{task_name.upper()}] Feature Importance Matrix', fontsize=16, y=1.05)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "feature_importance_grid.png"), bbox_inches='tight')
    plt.close()