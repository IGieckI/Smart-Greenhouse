import io
import pandas as pd
import matplotlib
matplotlib.use('Agg')  
import matplotlib.pyplot as plt
from config import TZ_ROME

def _finalize_and_save_plot(title: str, xlabel: str = 'Time (Local)', ylabel: str = 'Value') -> io.BytesIO:
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close()
    return buf

def _is_humidity_series(label: str) -> bool:
    return "Humidity" in label or "(%)" in label

def create_series_plot(df_hist: pd.DataFrame, series_dict: dict, title: str, hide_real_history: bool = False) -> io.BytesIO:
    fig, ax_temp = plt.subplots(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    last_val = None

    has_humidity = any(_is_humidity_series(label) for label, data in series_dict.items() if data)
    ax_hum = ax_temp.twinx() if has_humidity else None

    def _axis_for(label: str):
        return ax_hum if (ax_hum is not None and _is_humidity_series(label)) else ax_temp

    if not df_hist.empty and 'leaf_temp' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['leaf_temp'])
        if not df_plot.empty:
            last_time = df_plot.index[-1]
            last_val = df_plot['leaf_temp'].iloc[-1]
            if not hide_real_history:
                ax_temp.plot(df_plot.index, df_plot['leaf_temp'], label='Real History', color='black', alpha=0.4, linewidth=2)

    styles = {
        "Blended (Final)": {"color": "blue", "linewidth": 2.5, "marker": "o", "markersize": 6, "alpha": 1.0, "zorder": 5},
        "Environment (Env)": {"color": "orange", "linewidth": 1.5, "linestyle": "--", "marker": "x", "markersize": 6, "alpha": 0.8},
        "Autoregressive (Auto)": {"color": "green", "linewidth": 1.5, "linestyle": "--", "marker": "s", "markersize": 5, "alpha": 0.8},
        "T1/T4 Est. History (Soft Sensor)": {"color": "purple", "linewidth": 2.5, "linestyle": "-", "alpha": 0.8},
        "Standard Prediction": {"color": "red", "linewidth": 2.0, "linestyle": "--", "marker": "o", "markersize": 5},
        "What-If Projection": {"color": "orange", "linewidth": 2.0, "linestyle": "dashed", "marker": "o", "markersize": 5},
        "Air Temp History (°C)": {"color": "red", "linewidth": 1.5, "linestyle": "-", "alpha": 0.6},
        "Air Temp Forecast (°C)": {"color": "red", "linewidth": 1.5, "linestyle": "--", "marker": "."},
        "Humidity History (%)": {"color": "cyan", "linewidth": 1.5, "linestyle": "-", "alpha": 0.6},
        "Humidity Forecast (%)": {"color": "cyan", "linewidth": 1.5, "linestyle": "--", "marker": "."},
        "Leaf Temp History (°C)": {"color": "green", "linewidth": 1.5, "linestyle": "-", "alpha": 0.8},
        "Leaf Temp Forecast (°C)": {"color": "green", "linewidth": 2.0, "linestyle": "--", "marker": "*"}
    }

    for label, data in series_dict.items():
        if not data: continue
        times = [pd.to_datetime(d['timestamp']).astimezone(TZ_ROME) for d in data]
        vals = [d['value'] for d in data]

        if "History" not in label and "Forecast" not in label and last_val is not None:
            times = [last_time] + times
            vals = [last_val] + vals

        style = styles.get(label, {"marker": "o", "markersize": 4, "linestyle": "--"})
        _axis_for(label).plot(times, vals, label=label, **style)

    ax_temp.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Now')

    ax_temp.set_title(title)
    ax_temp.set_xlabel('Time (Local)')
    ax_temp.set_ylabel('Temperature (°C)' if ax_hum is not None else 'Value')
    ax_temp.grid(True, alpha=0.3)
    for label in ax_temp.get_xticklabels():
        label.set_rotation(45)

    if ax_hum is not None:
        ax_hum.set_ylabel('Humidity (%)')
        handles, labels = ax_temp.get_legend_handles_labels()
        h2, l2 = ax_hum.get_legend_handles_labels()
        ax_temp.legend(handles + h2, labels + l2, loc='best')
    else:
        ax_temp.legend(loc='best')

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    plt.close(fig)
    return buf

# Original functions for ml inference

def create_vpd_plot(df_hist: pd.DataFrame, future_vpd: list = None, historical_vpd: list = None) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    last_val = None
    has_data = False
    
    if historical_vpd:
        times = [pd.to_datetime(d['timestamp']).astimezone(TZ_ROME) for d in historical_vpd]
        vals = [d['value'] for d in historical_vpd]
        plt.plot(times, vals, label='Historical VPD (API)', color='magenta', linewidth=2)
        if times:
            last_time = times[-1]
            last_val = vals[-1]
        has_data = True
    elif not df_hist.empty and 'vpd' in df_hist.columns:
        df_plot = df_hist.dropna(subset=['vpd'])
        if not df_plot.empty:
            plt.plot(df_plot.index, df_plot['vpd'], label='Historical VPD (Sensor)', color='magenta', linewidth=2)
            last_time = df_plot.index[-1]
            last_val = df_plot['vpd'].iloc[-1]
            has_data = True

    if future_vpd:
        times = [pd.to_datetime(d['timestamp']).astimezone(TZ_ROME) for d in future_vpd]
        vals = [d['value'] for d in future_vpd]
        if last_val is not None:
            times = [last_time] + times
            vals = [last_val] + vals
        plt.plot(times, vals, label='Future VPD Projection', color='purple', linestyle='--', marker='o', markersize=4)
        has_data = True

    if not has_data:
        plt.text(0.5, 0.5, 'VPD Data Unavailable', horizontalalignment='center', verticalalignment='center', transform=plt.gca().transAxes)

    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Now')
    return _finalize_and_save_plot("Vapor Pressure Deficit (VPD)", ylabel="VPD (kPa)")

def create_semantic_category_plots(df_hist: pd.DataFrame) -> list[io.BytesIO]:
    plots = []
    categories = {
        "Temperatures (°C)": (['air_temp', 'leaf_temp', 'water_temp'], ['red', 'green', 'blue']),
        "Luminosity (Lux)": (['light_lux'], ['orange']),
        "Pressure (hPa)": (['pressure'], ['purple']),
        "Humidity & Soil Moisture (%)": (['humidity', 'soil_moisture'], ['cyan', 'brown']),
        "Water Quality (TDS - ppm)": (['tds'], ['olive'])
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
        plots.append(_finalize_and_save_plot(title))
        
    plots.append(create_vpd_plot(df_hist))
    return plots

# Functions for history menu

def _forecast_xy(series: list, anchor_time=None, anchor_val=None):
    """ Convert an API series [{timestamp, value}] to (times, values), optionally
        prepended with an anchor point so the forecast line connects to the actuals. """
    times = [pd.to_datetime(d['timestamp']).astimezone(TZ_ROME) for d in series]
    vals = [d['value'] for d in series]
    if anchor_time is not None and anchor_val is not None:
        times = [anchor_time] + times
        vals = [anchor_val] + vals
    return times, vals

def create_history_vpd_plot(df_hist: pd.DataFrame, vpd_forecast: dict = None) -> io.BytesIO:
    plt.figure(figsize=(10, 5))
    last_time = pd.Timestamp.now(tz=TZ_ROME)
    has_data = False
    last_air_time = last_air_val = None
    last_leaf_time = last_leaf_val = None

    if not df_hist.empty:
        if 'vpd_air' in df_hist.columns and not df_hist['vpd_air'].dropna().empty:
            df_plot = df_hist.dropna(subset=['vpd_air'])
            plt.plot(df_plot.index, df_plot['vpd_air'], label='Actual VPD (Air)', color='blue', linewidth=1.5, linestyle='-.', alpha=0.6)
            last_time = df_plot.index[-1]
            last_air_time, last_air_val = df_plot.index[-1], df_plot['vpd_air'].iloc[-1]
            has_data = True

        if 'vpd_leaf' in df_hist.columns and not df_hist['vpd_leaf'].dropna().empty:
            df_plot = df_hist.dropna(subset=['vpd_leaf'])
            plt.plot(df_plot.index, df_plot['vpd_leaf'], label='Actual VPD (Leaf)', color='magenta', linewidth=2)
            last_time = df_plot.index[-1]
            last_leaf_time, last_leaf_val = df_plot.index[-1], df_plot['vpd_leaf'].iloc[-1]
            has_data = True

        if 'vpd_air_pred' in df_hist.columns and not df_hist['vpd_air_pred'].dropna().empty:
            df_plot = df_hist.dropna(subset=['vpd_air_pred'])
            plt.plot(df_plot.index, df_plot['vpd_air_pred'], label='Predicted VPD (Air)', color='cyan', linewidth=1.5, linestyle='--')
            has_data = True

        if 'vpd_leaf_pred' in df_hist.columns and not df_hist['vpd_leaf_pred'].dropna().empty:
            df_plot = df_hist.dropna(subset=['vpd_leaf_pred'])
            plt.plot(df_plot.index, df_plot['vpd_leaf_pred'], label='Predicted VPD (Leaf)', color='orange', linewidth=1.5, linestyle='--')
            has_data = True

    if vpd_forecast:
        if air_fc := vpd_forecast.get('air'):
            times, vals = _forecast_xy(air_fc, last_air_time, last_air_val)
            plt.plot(times, vals, label='Forecast VPD (Air)', color='dodgerblue', linewidth=2.0, linestyle='--', marker='.', markersize=6)
            has_data = True
        if leaf_fc := vpd_forecast.get('leaf'):
            times, vals = _forecast_xy(leaf_fc, last_leaf_time, last_leaf_val)
            plt.plot(times, vals, label='Forecast VPD (Leaf)', color='red', linewidth=2.0, linestyle='--', marker='*', markersize=7)
            has_data = True

    if not has_data:
        plt.text(0.5, 0.5, 'VPD Data Unavailable', horizontalalignment='center', verticalalignment='center', transform=plt.gca().transAxes)

    plt.axvline(x=last_time, color='red', linestyle=':', alpha=0.6, label='Now')
    return _finalize_and_save_plot("Vapor Pressure Deficit (VPD) [History]", ylabel="VPD (kPa)")

def create_history_plots(df_hist: pd.DataFrame, vpd_forecast: dict = None) -> list[io.BytesIO]:
    plots = []
    categories = {
        "Temperatures (°C)": {
            'actuals': (['air_temp', 'leaf_temp', 'water_temp'], ['red', 'green', 'blue']),
            'preds': (['air_temp_pred', 'leaf_temp_pred'], ['cyan', 'orange'])
        },
        "Humidity & Soil Moisture (%)": {
            'actuals': (['humidity', 'soil_moisture'], ['cyan', 'brown']),
            'preds': (['humidity_pred'], ['purple'])
        },
        "Luminosity (Lux)": {
            'actuals': (['light_lux'], ['orange']),
            'preds': ([], [])
        },
        "Pressure (hPa)": {
            'actuals': (['pressure'], ['purple']),
            'preds': ([], [])
        },
        "Water Quality (TDS - ppm)": {
            'actuals': (['tds'], ['olive']),
            'preds': ([], [])
        }
    }
    
    for title, config in categories.items():
        actual_cols, actual_colors = config['actuals']
        pred_cols, pred_colors = config['preds']
        
        avail_actuals = [c for c in actual_cols if c in df_hist.columns]
        avail_preds = [c for c in pred_cols if c in df_hist.columns]
        
        if not avail_actuals and not avail_preds: 
            continue
        
        plt.figure(figsize=(10, 4))
        
        for idx, col in enumerate(avail_actuals):
            df_plot = df_hist.dropna(subset=[col])
            if not df_plot.empty:
                plt.plot(df_plot.index, df_plot[col], label=f"Actual {col.replace('_', ' ').title()}", color=actual_colors[idx % len(actual_colors)], linewidth=2)
                
        for idx, col in enumerate(avail_preds):
            df_plot = df_hist.dropna(subset=[col])
            if not df_plot.empty:
                plt.plot(df_plot.index, df_plot[col], label=f"Predicted {col.replace('_pred', '').replace('_', ' ').title()}", color=pred_colors[idx % len(pred_colors)], linewidth=1.5, linestyle='--')
        
        plots.append(_finalize_and_save_plot(title))
        
    plots.append(create_history_vpd_plot(df_hist, vpd_forecast))
    return plots