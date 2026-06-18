import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import threading
import csv
import time

# --- CONFIGURATION ---
DEFAULT_ESP32_URL = "http://192.168.4.1/dump"

class GreenhouseDashboard(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Greenhouse Operator Dashboard")
        self.geometry("900x600")
        self.configure(padx=20, pady=20)
        
        self.downloaded_data = []

        self.create_widgets()

    def create_widgets(self):
        # --- Top Frame: Controls ---
        control_frame = ttk.Frame(self)
        control_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(control_frame, text="ESP32 Endpoint:", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        
        self.url_entry = ttk.Entry(control_frame, width=30)
        self.url_entry.insert(0, DEFAULT_ESP32_URL)
        self.url_entry.pack(side=tk.LEFT, padx=(0, 20))

        self.btn_download = ttk.Button(control_frame, text="📥 Sync & Download Data", command=self.start_download_thread)
        self.btn_download.pack(side=tk.LEFT, padx=5)

        self.btn_sync_time = ttk.Button(control_frame, text="⏱ Sync Device Time", command=self.start_sync_time_thread)
        self.btn_sync_time.pack(side=tk.LEFT, padx=5)

        self.btn_export = ttk.Button(control_frame, text="💾 Export to CSV", command=self.export_csv, state=tk.DISABLED)
        self.btn_export.pack(side=tk.LEFT, padx=5)

        # --- Middle Frame: Data Table ---
        table_frame = ttk.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        # Scrollbars for the table
        scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        self.tree = ttk.Treeview(table_frame, yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set, show="headings")
        self.tree.pack(fill=tk.BOTH, expand=True)

        scroll_y.config(command=self.tree.yview)
        scroll_x.config(command=self.tree.xview)

        # --- Bottom Frame: Logs ---
        log_frame = ttk.LabelFrame(self, text="System Logs")
        log_frame.pack(fill=tk.X)

        self.log_text = tk.Text(log_frame, height=6, state=tk.DISABLED, bg="#f4f4f4", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, padx=5, pady=5)

        self.log_message("System initialized. Ready to connect to GREENHOUSE_STAR Wi-Fi.")

    # --- Core Logic ---
    
    def log_message(self, message):
        """Safely append a message to the log window."""
        self.log_text.config(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def start_download_thread(self):
        """Runs the HTTP request in a separate thread to prevent GUI freezing."""
        self.btn_download.config(state=tk.DISABLED)
        self.log_message("Attempting to connect to Central Node...")
        
        # Clear existing table
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        thread = threading.Thread(target=self.fetch_data)
        thread.daemon = True
        thread.start()

    def fetch_data(self):
        url = self.url_entry.get().strip()
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Use root.after to safely update the GUI from a background thread
            self.after(0, self.process_downloaded_data, data)
            
        except requests.exceptions.ConnectionError:
            self.after(0, self.log_message, "ERROR: Could not connect. Are you on the 'GREENHOUSE_STAR' Wi-Fi?")
            self.after(0, lambda: self.btn_download.config(state=tk.NORMAL))
        except requests.exceptions.Timeout:
            self.after(0, self.log_message, "ERROR: Connection timed out.")
            self.after(0, lambda: self.btn_download.config(state=tk.NORMAL))
        except Exception as e:
            self.after(0, self.log_message, f"ERROR: Invalid data received. {e}")
            self.after(0, lambda: self.btn_download.config(state=tk.NORMAL))

    def process_downloaded_data(self, data):
        self.btn_download.config(state=tk.NORMAL)
        
        if not data:
            self.log_message("Connected, but the ESP32 ring buffer is currently empty.")
            return
        
        for row in data:
            if 'timestamp' in row:
                row['timestamp'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['timestamp']))

        self.downloaded_data = data
        self.log_message(f"Successfully downloaded {len(data)} telemetry records.")

        # Dynamically setup Treeview columns based on JSON keys
        headers = list(data[0].keys())
        self.tree["columns"] = headers
        
        for col in headers:
            self.tree.heading(col, text=col.replace("_", " ").title())
            self.tree.column(col, anchor=tk.CENTER, width=100)

        # Insert data into the table
        for row in data:
            values = [row.get(col, "") for col in headers]
            self.tree.insert("", tk.END, values=values)
            
        # Enable the export button
        self.btn_export.config(state=tk.NORMAL)

    def export_csv(self):
        if not self.downloaded_data:
            return
            
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"greenhouse_dump_{int(time.time())}.csv",
            title="Save Telemetry Data"
        )
        
        if file_path:
            try:
                headers = self.downloaded_data[0].keys()
                with open(file_path, 'w', newline='') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(self.downloaded_data)
                
                self.log_message(f"Data successfully saved to {file_path}")
                messagebox.showinfo("Export Successful", f"Saved {len(self.downloaded_data)} records to CSV.")
            except Exception as e:
                self.log_message(f"ERROR saving CSV: {e}")
                messagebox.showerror("Export Failed", str(e))

    def start_sync_time_thread(self):
        self.btn_sync_time.config(state=tk.DISABLED)
        self.log_message("Pushing current system time to Star Node...")
        thread = threading.Thread(target=self.sync_time)
        thread.daemon = True
        thread.start()

    def sync_time(self):
        # Swap '/dump' for '/set_time'
        base_url = self.url_entry.get().strip().rsplit('/', 1)[0]
        url = f"{base_url}/set_time"
        
        current_epoch = str(int(time.time()))
        
        try:
            response = requests.post(url, data=current_epoch, timeout=5)
            response.raise_for_status()
            self.after(0, self.log_message, "SUCCESS: Star Node RTC synced.")
        except Exception as e:
            self.after(0, self.log_message, f"ERROR syncing time: {e}")
        finally:
            self.after(0, lambda: self.btn_sync_time.config(state=tk.NORMAL))

if __name__ == "__main__":
    app = GreenhouseDashboard()
    app.mainloop()