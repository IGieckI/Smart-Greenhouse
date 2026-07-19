import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import asyncio
import struct
import threading
import csv
import time
from aiocoap import Context, Message, GET, POST

# CoAP URI of the Star node
DEFAULT_ESP32_URL = "coap://192.168.4.1"

# Binary layout of the /dump payload (telemetry_packet_t)
_STRUCT_FMT = '<IIffffffff'
_STRUCT_SIZE = struct.calcsize(_STRUCT_FMT)
_DUMP_FIELDS = ['timestamp', 'node_id', 'water_temp', 'tds_value', 'soil_moisture',
                'light_lux', 'air_temp', 'humidity', 'pressure', 'leaf_temp']

async def _coap_request(uri, code=GET, payload=b'', timeout=10):
    """
    Issue a single CoAP request from a fresh client context and return the response.
    """

    context = await Context.create_client_context()
    try:
        request = Message(code=code, uri=uri, payload=payload)
        return await asyncio.wait_for(context.request(request).response, timeout=timeout)
    finally:
        await context.shutdown()

class GreenhouseDashboard(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Greenhouse Operator Dashboard")
        self.geometry("900x650")
        self.configure(padx=20, pady=20)
        
        self.downloaded_data = []
        self.is_connected = False

        self.create_widgets()
        
        threading.Thread(target=self.connection_watchdog, daemon=True).start()

    def create_widgets(self):
        """
        Create the GUI layout with controls, table, and log window.
        """
        control_frame = ttk.Frame(self)
        control_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(control_frame, text="ESP32 Endpoint:", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        
        self.url_entry = ttk.Entry(control_frame, width=30)
        self.url_entry.insert(0, DEFAULT_ESP32_URL)
        self.url_entry.pack(side=tk.LEFT, padx=(0, 20))

        self.btn_download = ttk.Button(control_frame, text="📥 Sync & Download Data", command=self.start_download_thread, state=tk.DISABLED)
        self.btn_download.pack(side=tk.LEFT, padx=5)

        self.btn_sync_time = ttk.Button(control_frame, text="⏱ Sync Device Time", command=self.start_sync_time_thread, state=tk.DISABLED)
        self.btn_sync_time.pack(side=tk.LEFT, padx=5)

        self.btn_export = ttk.Button(control_frame, text="💾 Export to CSV", command=self.export_csv, state=tk.DISABLED)
        self.btn_export.pack(side=tk.LEFT, padx=5)

        table_frame = ttk.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        scroll_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        self.tree = ttk.Treeview(table_frame, yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set, show="headings")
        self.tree.pack(fill=tk.BOTH, expand=True)

        scroll_y.config(command=self.tree.yview)
        scroll_x.config(command=self.tree.xview)

        action_frame = ttk.Frame(self)
        action_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.btn_clear = ttk.Button(action_frame, text="🗑️ Clear Table", command=self.clear_table)
        self.btn_clear.pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(self, text="System Logs")
        log_frame.pack(fill=tk.X)

        self.log_text = tk.Text(log_frame, height=6, state=tk.DISABLED, bg="#f4f4f4", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, padx=5, pady=5)

        self.log_message("System initialized. Searching for GREENHOUSE_STAR...")
    
    def log_message(self, message):
        """
        Append a timestamped message to the log window.
        """
        self.log_text.config(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        
    def connection_watchdog(self):
        """
        Continuously checks if the Star node is reachable and updates the GUI accordingly.
        """
        while True:
            base = self.url_entry.get().strip().rstrip('/')

            try:
                asyncio.run(_coap_request(f"{base}/info", GET, timeout=2))
                reachable = True
            except Exception:
                reachable = False

            if reachable:
                if not self.is_connected:
                    self.is_connected = True
                    self.after(0, lambda: self.btn_download.config(state=tk.NORMAL))
                    self.after(0, lambda: self.btn_sync_time.config(state=tk.NORMAL))
                    self.after(0, self.log_message, "OK | Connected to Star Node. Commands enabled.")
            else:
                if (self.is_connected) or (not hasattr(self, 'first_check_done')):
                    self.is_connected = False
                    self.first_check_done = True
                    self.after(0, lambda: self.btn_download.config(state=tk.DISABLED))
                    self.after(0, lambda: self.btn_sync_time.config(state=tk.DISABLED))
                    self.after(0, self.log_message, "ERROR | Star Node unreachable. Commands disabled.")

            time.sleep(3)

    def clear_table(self):
        """
        Clear the Treeview table and the in-memory list of downloaded data.
        """
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.downloaded_data.clear()
        self.btn_export.config(state=tk.DISABLED)
        self.log_message("Table and memory cleared.")

    def start_download_thread(self):
        """
        Start a background thread to fetch data from the Star node.
        """
        self.btn_download.config(state=tk.DISABLED)
        self.log_message("Attempting to pull latest data from Central Node...")
        
        thread = threading.Thread(target=self.fetch_data)
        thread.daemon = True
        thread.start()

    def fetch_data(self):
        """
        Fetch the /dump data from the Star node and process it.
        """
        base = self.url_entry.get().strip().rstrip('/')
        uri = f"{base}/dump"
        try:
            response = asyncio.run(_coap_request(uri, GET))
            data = self._decode_dump(response.payload)
            self.after(0, self.process_downloaded_data, data)

        except Exception as e:
            self.after(0, self.log_message, f"ERROR: Invalid data received or timeout. {e}")
            self.after(0, lambda: self.btn_download.config(state=tk.NORMAL))

    @staticmethod
    def _decode_dump(payload):
        """
        Decode the binary payload from the /dump endpoint into a list of dictionaries.
        """
        records = []
        if not payload:
            return records
        for offset in range(0, len(payload) - _STRUCT_SIZE + 1, _STRUCT_SIZE):
            values = struct.unpack_from(_STRUCT_FMT, payload, offset)
            row = dict(zip(_DUMP_FIELDS, values))
            for key in _DUMP_FIELDS[2:]:
                row[key] = round(row[key], 2)
            records.append(row)
        return records

    def process_downloaded_data(self, data):
        """
        Process the downloaded telemetry data, update the GUI table, and log the results.
        """
        self.btn_download.config(state=tk.NORMAL)
        
        if not data:
            self.log_message("Connected, but the ESP32 ring buffer is currently empty.")
            return

        for row in data:
            if ('timestamp' in row) and (row['timestamp'] > 10000):
                row['timestamp'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['timestamp']))

        self.downloaded_data.extend(data)
        self.log_message(f"Successfully appended {len(data)} new telemetry records.")

        headers = list(data[0].keys())
        self.tree["columns"] = headers
        
        for col in headers:
            self.tree.heading(col, text=col.replace("_", " ").title())
            self.tree.column(col, anchor=tk.CENTER, width=100)

        for row in data:
            values = [row.get(col, "") for col in headers]
            self.tree.insert("", tk.END, values=values)
            
        self.btn_export.config(state=tk.NORMAL)

    def start_sync_time_thread(self):
        """
        Start a background thread to sync the Star node's RTC with the current system time.
        """
        self.btn_sync_time.config(state=tk.DISABLED)
        self.log_message("Pushing current system time to Star Node...")
        thread = threading.Thread(target=self.sync_time)
        thread.daemon = True
        thread.start()

    def sync_time(self):
        """
        Sync the Star node's RTC with the current system time by sending a POST request to /set_time.
        """
        base = self.url_entry.get().strip().rstrip('/')
        uri = f"{base}/set_time"
        current_epoch = str(int(time.time())).encode()

        try:
            response = asyncio.run(_coap_request(uri, POST, current_epoch, timeout=5))
            if response.code.is_successful():
                self.after(0, self.log_message, "SUCCESS: Star Node RTC synced.")
            else:
                self.after(0, self.log_message, f"ERROR syncing time: CoAP {response.code}")
        except Exception as e:
            self.after(0, self.log_message, f"ERROR syncing time: {e}")
        finally:
            self.after(0, lambda: self.btn_sync_time.config(state=tk.NORMAL))

    def export_csv(self):
        """
        Export the downloaded telemetry data to a CSV file using a file dialog.
        """
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

if __name__ == "__main__":
    app = GreenhouseDashboard()
    app.mainloop()