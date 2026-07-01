import os
import json
import shutil
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd
import seaborn as sns

def generate_task_plots(task_dir: str, task_name: str) -> list:
    """
    Generates analytical plots for a specific task ON THE FLY.
    Returns a list of absolute paths to the generated PNG files.
    """
    archive_dir = os.path.join(task_dir, "models_archive")
    output_dir = os.path.join(task_dir, "analytics_plots")
    
    if not os.path.exists(archive_dir):
        return []
        
    json_files = [f for f in os.listdir(archive_dir) if f.endswith("_metrics.json")]
    if not json_files:
        return []
        
    # Recreate output directory to clear old artifacts
    if os.path.exists(output_dir): shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    models_data = {}
    for filename in json_files:
        with open(os.path.join(archive_dir, filename), 'r') as f:
            data = json.load(f)
            models_data[data["model_name"]] = data

    model_names = list(models_data.keys())
    generated_files = []

    generated_files.append(_plot_metrics_comparison(models_data, model_names, task_name, output_dir))
    generated_files.append(_plot_timing_comparison(models_data, model_names, task_name, output_dir))
    generated_files.append(_plot_hyperparameters_table(models_data, model_names, task_name, output_dir))
    generated_files.append(_plot_feature_importance_grid(models_data, model_names, task_name, output_dir))

    return [f for f in generated_files if f is not None]


def generate_global_plots(base_dir: str, freq_minutes: int) -> list:
    """
    Scans all tasks, extracts metrics, and generates global comparative matrices ON THE FLY.
    Returns a list of absolute paths to the generated PNG files.
    """
    global_dir = os.path.join(base_dir, "global_analytics")
    tasks = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d)) and d.startswith("t")]
    if not tasks: return []
        
    all_data = {}
    all_models = set()
    
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

    if not all_data: return []
    
    if os.path.exists(global_dir): shutil.rmtree(global_dir)
    os.makedirs(global_dir, exist_ok=True)
    
    model_names = sorted(list(all_models))
    generated_files = []
    
    generated_files.append(_generate_global_heatmap(all_data, model_names, "MAE", "metrics", global_dir, freq_minutes))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "RMSE", "metrics", global_dir, freq_minutes))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "R_squared", "metrics", global_dir, freq_minutes, cmap="RdYlGn", highlight_max=True))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "training_time_seconds", "performance", global_dir, freq_minutes, cmap="Reds"))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "inference_time_seconds", "performance", global_dir, freq_minutes, cmap="Reds"))
    
    generated_files.append(_generate_global_params_grid(all_data, model_names, global_dir, freq_minutes))
    generated_files.append(_generate_global_feature_importance_grid(all_data, model_names, global_dir, freq_minutes))
    
    return [f for f in generated_files if f is not None]


# ==========================================
# INTERNAL PLOTTING UTILS
# ==========================================

def _extract_top_features(feat_imp: dict, target_variance: float = 0.75, max_feats: int = 15):
    """Extracts features capturing up to target_variance or max_feats. Groups the rest into 'Other'."""
    if not feat_imp: return []
    
    sorted_feats = sorted(feat_imp.items(), key=lambda item: abs(item[1]), reverse=True)
    total_imp = sum([abs(v) for k, v in sorted_feats])
    if total_imp == 0: return []
    
    cumulative = 0.0
    top_feats = []
    for i, (k, v) in enumerate(sorted_feats):
        top_feats.append((k, v))
        cumulative += abs(v) / total_imp
        if cumulative >= target_variance or i >= (max_feats - 1):
            break
            
    used_keys = set([k for k, v in top_feats])
    other_sum = sum([abs(v) for k, v in sorted_feats if k not in used_keys])
    
    if other_sum > 0:
        top_feats.append(("Other", other_sum))
        
    return top_feats

def _generate_global_heatmap(all_data, model_names, key, category, out_dir, freq, cmap="YlGnBu", highlight_max=False):
    file_path = os.path.join(out_dir, f"global_{key}.png")

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
    
    fig, ax = plt.subplots(figsize=(max(8, len(model_names) * 2), max(4, len(df_matrix) * 1.2)))
    sns.heatmap(df_matrix, annot=True, cmap=cmap, fmt=".3f", linewidths=.5, cbar_kws={'label': key.replace("_", " ").title()}, ax=ax)
    
    # Highlight best model per task (row)
    if not df_matrix.empty:
        best_cols = df_matrix.idxmax(axis=1) if highlight_max else df_matrix.idxmin(axis=1)
        for row_idx, task in enumerate(df_matrix.index):
            best_model = best_cols[task]
            if pd.isna(best_model): continue
            col_idx = df_matrix.columns.get_loc(best_model)
            # Add a red box around the best performing cell
            ax.add_patch(patches.Rectangle((col_idx, row_idx), 1, 1, fill=False, edgecolor='red', lw=4))
            
    plt.title(f"Global Matrix: {key.replace('_', ' ').upper()} ({freq}m)\nRed box indicates best performance", pad=20)
    plt.ylabel("Task")
    plt.xlabel("Model")
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _generate_global_params_grid(all_data, model_names, out_dir, freq):
    file_path = os.path.join(out_dir, "global_best_params.png")
    tasks = sorted(list(all_data.keys()))
    cell_text = []
    
    for t in tasks:
        row = []
        for m in model_names:
            if m in all_data[t]:
                params = all_data[t][m].get("best_params", {})
                p_str = "\n".join([f"{k.split('__')[-1]}: {v}" for k, v in params.items()])
                row.append(p_str if p_str else "Default")
            else:
                row.append("N/A")
        cell_text.append(row)
        
    if not cell_text: return None

    fig, ax = plt.subplots(figsize=(max(8, 3.5 * len(model_names)), max(4, 2 * len(tasks))))
    ax.axis('off')
    
    table = ax.table(cellText=cell_text, rowLabels=[t.upper() for t in tasks], colLabels=model_names, loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 5)
    
    plt.title(f"Global Matrix: BEST HYPERPARAMETERS ({freq}m)", pad=20, fontsize=16)
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _generate_global_feature_importance_grid(all_data, model_names, out_dir, freq):
    file_path = os.path.join(out_dir, "global_feature_importance.png")
    tasks = sorted(list(all_data.keys()))
    
    cols = len(model_names)
    rows = len(tasks)
    
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 5 * rows))
    if rows == 1 and cols == 1: axes = np.array([[axes]])
    elif rows == 1: axes = np.array([axes])
    elif cols == 1: axes = np.array([[ax] for ax in axes])
        
    for r_idx, task in enumerate(tasks):
        for c_idx, model_name in enumerate(model_names):
            ax = axes[r_idx, c_idx]
            
            if model_name in all_data[task]:
                feat_imp = all_data[task][model_name].get("feature_importance", {})
                top_feats = _extract_top_features(feat_imp, target_variance=0.75, max_feats=15)
                
                if top_feats:
                    labels = [k for k, v in top_feats]
                    sizes = [abs(v) for k, v in top_feats]
                    colors = plt.cm.Paired(np.linspace(0, 1, len(labels)))
                    ax.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=140, textprops={'fontsize': 8})
                    ax.set_title(f"{task.upper()} - {model_name}")
                else:
                    ax.axis('off')
                    ax.set_title(f"{task.upper()} - {model_name}\n(No Data/Weights)")
            else:
                ax.axis('off')
                ax.set_title(f"{task.upper()} - {model_name}\n(N/A)")

    # FIX: Replaced '$\ge$' with '>=' to prevent Matplotlib parse error
    plt.suptitle(f"Global Feature Importance Matrix ({freq}m)\nShowing top features capturing >= 75% variance (Max 15)", fontsize=20, y=1.02)
    plt.tight_layout()
    plt.savefig(file_path, bbox_inches='tight')
    plt.close()
    return file_path

def _plot_metrics_comparison(models_data, model_names, task_name, output_dir):
    file_path = os.path.join(output_dir, "metrics_comparison.png")
    maes = [models_data[m]["metrics"].get("MAE", 0) for m in model_names]
    rmses = [models_data[m]["metrics"].get("RMSE", 0) for m in model_names]
    r2s = [max(0, models_data[m]["metrics"].get("R_squared", 0)) for m in model_names]

    x = np.arange(len(model_names))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.bar(x - width, maes, width, label='MAE', color='#ff9999')
    ax.bar(x, rmses, width, label='RMSE', color='#66b3ff')
    ax.bar(x + width, r2s, width, label='R² (Scaled)', color='#99ff99')

    ax.set_ylabel('Metric Value')
    ax.set_title(f'[{task_name.upper()}] Metrics Comparison per Model')
    ax.set_xticks(x)
    ax.set_xticklabels(model_names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _plot_timing_comparison(models_data, model_names, task_name, output_dir):
    file_path = os.path.join(output_dir, "timing_comparison.png")
    train_times = [models_data[m]["performance"].get("training_time_seconds", 0) for m in model_names]
    inf_times = [models_data[m]["performance"].get("inference_time_seconds", 0) for m in model_names]

    fig, ax1 = plt.subplots(figsize=(10, 5))
    
    color = 'tab:red'
    ax1.set_xlabel('Models')
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

    plt.title(f'[{task_name.upper()}] Training vs Inference Times')
    fig.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _plot_hyperparameters_table(models_data, model_names, task_name, output_dir):
    file_path = os.path.join(output_dir, "hyperparameters_table.png")
    fig, ax = plt.subplots(figsize=(12, min(2 + len(model_names)*0.5, 8)))
    ax.axis('off')
    
    cell_text = []
    for m in model_names:
        params = models_data[m].get("best_params", {})
        params_str = ", ".join([f"{k.split('__')[-1]}:{v}" for k, v in params.items()])
        if len(params_str) > 80:
            params_str = params_str[:77] + "..."
        cell_text.append([m, params_str, models_data[m]["metrics"].get("MAE", "")])

    table = ax.table(cellText=cell_text, colLabels=['Model', 'Best Hyperparameters', 'MAE Result'], 
                     cellLoc='left', loc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2)
    
    plt.title(f'[{task_name.upper()}] Best Hyperparameters Summary', pad=20)
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _plot_feature_importance_grid(models_data, model_names, task_name, output_dir):
    file_path = os.path.join(output_dir, "feature_importance_grid.png")
    n_models = len(model_names)
    cols = min(3, n_models)
    rows = (n_models + cols - 1) // cols
    
    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 6 * rows))
    if n_models == 1: axes = np.array([axes])
    axes = axes.flatten()

    for idx, model_name in enumerate(model_names):
        ax = axes[idx]
        feat_imp = models_data[model_name].get("feature_importance", {})
        top_feats = _extract_top_features(feat_imp, target_variance=0.75, max_feats=15)
        
        if top_feats:
            labels = [k for k, v in top_feats]
            sizes = [abs(v) for k, v in top_feats]
            colors = plt.cm.Paired(np.linspace(0, 1, len(labels)))
            ax.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=140, textprops={'fontsize': 9})
            ax.set_title(f'{model_name}')
        else:
            ax.axis('off')
            ax.set_title(f'{model_name} (No Data / Zero Weights)')

    for idx in range(n_models, len(axes)):
        axes[idx].axis('off')

    # FIX: Replaced '$\ge$' with '>='
    plt.suptitle(f'[{task_name.upper()}] Feature Importance\nShowing top features capturing >= 75% variance (Max 15)', fontsize=16, y=1.05)
    plt.tight_layout()
    plt.savefig(file_path, bbox_inches='tight')
    plt.close()
    return file_path