import os
import json
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd
import seaborn as sns

from sklearn.inspection import permutation_importance

FIGSIZE_STANDARD = (16, 10)
FIGSIZE_WIDE = (20, 10)
FIGSIZE_GRID_BASE = 11
FIGSIZE_HEATMAP_BASE = 9
FONT_TITLE = 21
FONT_AXIS = 17
FONT_TICK = 14
FONT_LEGEND = 14

global_subtitle = "Red box highlight best MAE performer"

def _clean_feature_name(name: str) -> str:
    name = name.replace('remainder__', '').replace('poly__', '').replace('regressor__', '')
    if len(name) > 30:
        name = name[:27] + "..."
    return name

def _extract_top_features(feat_imp: dict, target_variance: float = 0.75, max_feats: int = 10):
    if not feat_imp:
        return []
    
    sorted_feats = sorted(feat_imp.items(), key=lambda item: abs(item[1]), reverse=True)
    total_imp = sum([abs(v) for k, v in sorted_feats])
    if total_imp == 0:
        return []
    
    cumulative = 0.0
    top_feats = []
    for i, (k, v) in enumerate(sorted_feats):
        clean_k = _clean_feature_name(k)
        top_feats.append((clean_k, v))
        cumulative += abs(v) / total_imp
        if cumulative >= target_variance or i >= (max_feats - 1):
            break
            
    used_keys = set([k for k, v in top_feats])
    other_sum = sum([abs(v) for k, v in sorted_feats if _clean_feature_name(k) not in used_keys])
    
    if other_sum > 0:
        top_feats.append(("Other", other_sum))
        
    return top_feats

def _get_best_model_for_task(models_data: dict) -> str:
    best_model = None
    min_mae = float('inf')
    for m, data in models_data.items():
        mae = data.get("metrics", {}).get("MAE", float('inf'))
        if mae < min_mae:
            min_mae = mae
            best_model = m
    return best_model





def _plot_metrics_comparison(models_data, model_names, task_name, output_dir, best_model):
    file_path = os.path.join(output_dir, "metrics_comparison.png")
    maes = [models_data[m]["metrics"].get("MAE", 0) for m in model_names]
    rmses = [models_data[m]["metrics"].get("RMSE", 0) for m in model_names]
    r2s = [max(0, models_data[m]["metrics"].get("R_squared", 0)) for m in model_names]

    x = np.arange(len(model_names))
    width = 0.25

    fig, ax = plt.subplots(figsize=FIGSIZE_STANDARD)
    ax.bar(x - width, maes, width, label='MAE', color='#ff9999')
    ax.bar(x, rmses, width, label='RMSE', color='#66b3ff')
    ax.bar(x + width, r2s, width, label='R² (Scaled)', color='#99ff99')

    ax.set_ylabel('Metric Value', fontsize=FONT_AXIS)
    ax.set_title(f'[{task_name.upper()}] Metrics Comparison per Model', fontsize=FONT_TITLE)
    ax.set_xticks(x)
    
    xtick_labels = [f"{m} (Best)" if m == best_model else m for m in model_names]
    ax.set_xticklabels(xtick_labels, rotation=45, ha="right", color='black', fontsize=FONT_TICK)
    ax.tick_params(axis='y', labelsize=FONT_TICK)
    
    ax.legend(fontsize=FONT_LEGEND)
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _plot_timing_comparison(models_data, model_names, task_name, output_dir, best_model):
    file_path = os.path.join(output_dir, "timing_comparison.png")
    train_times = [models_data[m]["performance"].get("training_time_seconds", 0) for m in model_names]
    inf_times = [models_data[m]["performance"].get("inference_time_seconds", 0) for m in model_names]

    fig, ax1 = plt.subplots(figsize=FIGSIZE_STANDARD)
    
    color = 'tab:red'
    ax1.set_xlabel('Models', fontsize=FONT_AXIS)
    ax1.set_ylabel('Training Time (s)', color=color, fontsize=FONT_AXIS)
    ax1.bar(model_names, train_times, color=color, alpha=0.6, width=0.4, align='center', label='Training')
    ax1.tick_params(axis='y', labelcolor=color, labelsize=FONT_TICK)
    
    xtick_labels = [f"{m} (Best)" if m == best_model else m for m in model_names]
    # /app/trainer/analytics_plotter.py:110: UserWarning: FixedFormatter should only be used together with FixedLocator
    ax1.set_xticklabels(xtick_labels, rotation=45, ha="right", fontsize=FONT_TICK)

    ax2 = ax1.twinx()
    color = 'tab:blue'
    ax2.set_ylabel('Inference Time (s)', color=color, fontsize=FONT_AXIS)
    x_pos = np.arange(len(model_names))
    ax2.bar(x_pos + 0.2, inf_times, color=color, alpha=0.8, width=0.4, align='edge', label='Inference')
    ax2.tick_params(axis='y', labelcolor=color, labelsize=FONT_TICK)

    plt.title(f'[{task_name.upper()}] Training vs Inference Times', fontsize=FONT_TITLE)
    fig.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _plot_hyperparameters_table_task(models_data, model_names, task_name, output_dir, best_model):
    file_path = os.path.join(output_dir, "hyperparameters_table.png")
    fig, ax = plt.subplots(figsize=(FIGSIZE_STANDARD[0], min(4 + len(model_names)*0.8, 12)))
    ax.axis('off')
    
    cell_text = []
    for m in model_names:
        params = models_data[m].get("best_params", {})
        params_str = ", ".join([f"{k.split('__')[-1]}:{v}" for k, v in params.items()])
        if len(params_str) > 120:
            params_str = params_str[:117] + "..."
        cell_text.append([m, params_str, models_data[m]["metrics"].get("MAE", "")])

    table = ax.table(cellText=cell_text, colLabels=['Model', 'Best Hyperparameters', 'MAE Result'], 
                     cellLoc='left', loc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(FONT_TICK)
    table.scale(1, 2.5)
    
    for r_idx, m in enumerate(model_names):
        if m == best_model:
            for c_idx in range(3):
                table[r_idx + 1, c_idx].set_edgecolor('red')
                table[r_idx + 1, c_idx].set_linewidth(3)
    
    plt.title(f'[{task_name.upper()}] Best Hyperparameters Summary\n{global_subtitle}', pad=20, fontsize=FONT_TITLE)
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _plot_feature_importance_grid(models_data, model_names, task_name, output_dir, best_model):
    file_path = os.path.join(output_dir, "feature_importance_grid.png")
    n_models = len(model_names)
    cols = min(3, n_models)
    rows = (n_models + cols - 1) // cols
    
    fig, axes = plt.subplots(rows, cols, figsize=(FIGSIZE_GRID_BASE * cols, FIGSIZE_GRID_BASE * 0.7 * rows))
    if n_models == 1:
        axes = np.array([axes])
    axes = axes.flatten()

    for idx, model_name in enumerate(model_names):
        ax = axes[idx]
        is_best = (model_name == best_model)
        feat_imp = models_data[model_name].get("feature_importance", {})
        top_feats = _extract_top_features(feat_imp, target_variance=0.75, max_feats=10)
        
        if top_feats:
            labels = [k for k, v in top_feats]
            sizes = [abs(v) for k, v in top_feats]
            colors = plt.cm.Paired(np.linspace(0, 1, len(labels)))
            
            wedges, _, _ = ax.pie(sizes, autopct='%1.0f%%', startangle=140, colors=colors, textprops={'fontsize': FONT_TICK})
            ax.legend(wedges, labels, title="Features", loc="center left", bbox_to_anchor=(1, 0.5), fontsize=FONT_LEGEND)
            ax.set_title(f'{model_name}', color='red' if is_best else 'black', fontweight='bold' if is_best else 'normal', fontsize=FONT_AXIS)
        else:
            ax.axis('off')
            ax.set_title(f'{model_name} (No Data)', fontsize=FONT_AXIS)
            
        if is_best:
            rect = patches.Rectangle((0, 0), 1, 1, linewidth=5, edgecolor='red', facecolor='none', transform=ax.transAxes)
            ax.add_patch(rect)

    for idx in range(n_models, len(axes)):
        axes[idx].axis('off')

    plt.suptitle(f'[{task_name.upper()}] Feature Importance\nShowing top features capturing >= 75% variance (Max 10)', fontsize=FONT_TITLE, y=1.05)
    plt.tight_layout()
    plt.savefig(file_path, bbox_inches='tight')
    plt.close()
    return file_path






def _generate_global_heatmap(all_data, model_names, key, category, out_dir, freq, best_models, cmap="YlGnBu"):
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
    
    fig, ax = plt.subplots(figsize=(max(FIGSIZE_HEATMAP_BASE, len(model_names) * 2.5), max(8, len(df_matrix) * 1.5)))
    sns.heatmap(df_matrix, annot=True, cmap=cmap, fmt=".3f", linewidths=.5, cbar_kws={'label': key.replace("_", " ").title()}, ax=ax, annot_kws={"size": FONT_TICK})
    
    if not df_matrix.empty:
        for row_idx, task in enumerate(df_matrix.index):
            best_model = best_models.get(task)
            if pd.notna(best_model) and best_model in df_matrix.columns:
                col_idx = df_matrix.columns.get_loc(best_model)
                ax.add_patch(patches.Rectangle((col_idx, row_idx), 1, 1, fill=False, edgecolor='red', lw=4))
            
    plt.title(f"{key.replace('_', ' ').upper()} ({freq}m)\n{global_subtitle}", pad=20, fontsize=FONT_TITLE)
    plt.ylabel("Task", fontsize=FONT_AXIS)
    plt.xlabel("Model", fontsize=FONT_AXIS)
    plt.xticks(fontsize=FONT_TICK, rotation=45, ha='right')
    plt.yticks(fontsize=FONT_TICK)
    
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _generate_global_params_grid(all_data, model_names, out_dir, freq, best_models):
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
        
    if not cell_text:
        return None

    fig, ax = plt.subplots(figsize=(max(FIGSIZE_WIDE[0], 4 * len(model_names)), max(FIGSIZE_WIDE[1], 2.5 * len(tasks))))
    ax.axis('off')
    
    table = ax.table(cellText=cell_text, rowLabels=[t.upper() for t in tasks], colLabels=model_names, loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(FONT_TICK)
    table.scale(1, 6)
    
    for r_idx, task in enumerate(tasks):
        best_m = best_models.get(task)
        if best_m in model_names:
            c_idx = model_names.index(best_m)
            cell = table[r_idx + 1, c_idx] 
            cell.set_edgecolor('red')
            cell.set_linewidth(4)
    
    plt.title(f"BEST HYPERPARAMETERS ({freq}m)\n{global_subtitle}", pad=20, fontsize=FONT_TITLE)
    plt.tight_layout()
    plt.savefig(file_path)
    plt.close()
    return file_path

def _generate_global_feature_importance_grid(all_data, model_names, out_dir, freq, best_models):
    file_path = os.path.join(out_dir, "global_feature_importance.png")
    tasks = sorted(list(all_data.keys()))
    
    cols = len(model_names)
    rows = len(tasks)
    
    fig, axes = plt.subplots(rows, cols, figsize=(FIGSIZE_GRID_BASE * cols, FIGSIZE_GRID_BASE * 0.7 * rows))
    if rows == 1 and cols == 1:
        axes = np.array([[axes]])
    elif rows == 1:
        axes = np.array([axes])
    elif cols == 1:
        axes = np.array([[ax] for ax in axes])
        
    for r_idx, task in enumerate(tasks):
        best_m = best_models.get(task)
        for c_idx, model_name in enumerate(model_names):
            ax = axes[r_idx, c_idx]
            is_best = (model_name == best_m)
            
            if model_name in all_data[task]:
                feat_imp = all_data[task][model_name].get("feature_importance", {})
                top_feats = _extract_top_features(feat_imp, target_variance=0.75, max_feats=10)
                
                if top_feats:
                    labels = [k for k, v in top_feats]
                    sizes = [abs(v) for k, v in top_feats]
                    colors = plt.cm.Paired(np.linspace(0, 1, len(labels)))
                    
                    wedges, _, _ = ax.pie(sizes, autopct='%1.0f%%', startangle=140, colors=colors, textprops={'fontsize': FONT_TICK})
                    ax.legend(wedges, labels, title="Features", loc="center left", bbox_to_anchor=(1, 0.5), fontsize=FONT_LEGEND)
                    ax.set_title(f"{task.upper()} - {model_name}", color='red' if is_best else 'black', fontweight='bold' if is_best else 'normal', fontsize=FONT_AXIS)
                else:
                    ax.axis('off')
                    ax.set_title(f"{task.upper()} - {model_name}\n(No Data/Weights)", fontsize=FONT_AXIS)
            else:
                ax.axis('off')
                ax.set_title(f"{task.upper()} - {model_name}\n(N/A)", fontsize=FONT_AXIS)
                
            if is_best:
                rect = patches.Rectangle((0, 0), 1, 1, linewidth=5, edgecolor='red', facecolor='none', transform=ax.transAxes)
                ax.add_patch(rect)

    
    title = f"Feature Importance Matrix ({freq}m), foreach model in a task \n \
        Showing top features capturing >= 75% variance (Max 10) \n \
        {global_subtitle}" 
    plt.suptitle(title, fontsize=FONT_TITLE + 10, y=1.02)
    plt.tight_layout()
    plt.savefig(file_path, bbox_inches='tight')
    plt.close()
    return file_path



def generate_task_plots(task_dir: str, task_name: str) -> list:
    archive_dir = os.path.join(task_dir, "models_archive")
    output_dir = os.path.join(task_dir, "analytics_plots")
    
    if not os.path.exists(archive_dir): 
        return []
    
    json_files = [f for f in os.listdir(archive_dir) if f.endswith("_metrics.json")]
    
    if not json_files:
        return []
        
    os.makedirs(output_dir, exist_ok=True)

    models_data = {}
    for filename in json_files:
        with open(os.path.join(archive_dir, filename), 'r') as f:
            data = json.load(f)
            models_data[data["model_name"]] = data

    model_names = list(models_data.keys())
    best_model = _get_best_model_for_task(models_data)
    
    generated_files = []
    generated_files.append(_plot_metrics_comparison(models_data, model_names, task_name, output_dir, best_model))
    generated_files.append(_plot_timing_comparison(models_data, model_names, task_name, output_dir, best_model))
    generated_files.append(_plot_hyperparameters_table_task(models_data, model_names, task_name, output_dir, best_model))
    generated_files.append(_plot_feature_importance_grid(models_data, model_names, task_name, output_dir, best_model))

    return [f for f in generated_files if f is not None]



def generate_global_plots(base_dir: str, freq_minutes: int) -> list:
    global_dir = os.path.join(base_dir, "global_analytics")
    tasks = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d)) and d.startswith("t")]
    if not tasks:
        return []
        
    all_data = {}
    all_models = set()
    best_models_global = {}
    
    for task in tasks:
        archive_dir = os.path.join(base_dir, task, "models_archive")
        
        if not os.path.exists(archive_dir): 
            continue
            
        json_files = [f for f in os.listdir(archive_dir) if f.endswith("_metrics.json")]
        if not json_files:
            continue
            
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
                    
        if all_data[task]:
            best_models_global[task] = _get_best_model_for_task(all_data[task])

    if not all_data:
        return []
    
    os.makedirs(global_dir, exist_ok=True)
    
    model_names = sorted(list(all_models))
    generated_files = []
    
    generated_files.append(_generate_global_heatmap(all_data, model_names, "MAE", "metrics", global_dir, freq_minutes, best_models_global))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "RMSE", "metrics", global_dir, freq_minutes, best_models_global))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "R_squared", "metrics", global_dir, freq_minutes, best_models_global, cmap="RdYlGn"))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "training_time_seconds", "performance", global_dir, freq_minutes, best_models_global, cmap="mako"))
    generated_files.append(_generate_global_heatmap(all_data, model_names, "inference_time_seconds", "performance", global_dir, freq_minutes, best_models_global, cmap="mako"))
    
    generated_files.append(_generate_global_params_grid(all_data, model_names, global_dir, freq_minutes, best_models_global))
    generated_files.append(_generate_global_feature_importance_grid(all_data, model_names, global_dir, freq_minutes, best_models_global))
    
    return [f for f in generated_files if f is not None]