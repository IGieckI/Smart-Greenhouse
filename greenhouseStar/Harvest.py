import serial
import time

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 115200
OUTPUT_FILE = 'sensor_data.jsonl'

def listen_for_data():
    print(f"📡 Passively listening on {SERIAL_PORT}...")
    print("Waiting for ESP32 to push data (occurs after 5 seconds of sensor silence).")
    print("Press Ctrl+C to stop.\n")
    
    try:
        # This open the port without triggering a board reset
        ser = serial.Serial()
        ser.port = SERIAL_PORT
        ser.baudrate = BAUD_RATE
        ser.timeout = 1
        ser.setDTR(False)
        ser.setRTS(False)
        ser.open()

        collecting = False
        captured_lines = []

        while True:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            
            if not line:
                continue
                
            if "---START_DUMP---" in line:
                print("\n[+] Dump started, capturing data...")
                collecting = True
                captured_lines = []
                continue
            
            if "---END_DUMP---" in line:
                collecting = False
                if captured_lines:
                    # Append captured data to the file
                    with open(OUTPUT_FILE, "a") as f:
                        for item in captured_lines:
                            f.write(item + "\n")
                    print(f"Successfully appended {len(captured_lines)} records to {OUTPUT_FILE}.")
                else:
                    print("Dump finished, but no data was found.")
                continue
            
            # If we are between START and END markers, capture the JSON
            if collecting and line.startswith("{") and line.endswith("}"):
                captured_lines.append(line)
            # If we aren't collecting, print the ESP32's live logs so you know it's alive
            elif not collecting:
                print(f"Logs: {line}")

    except serial.SerialException as e:
        print(f"\nSerial Error: {e}")
        print("Make sure the port is correct and not open in another program (like idf.py monitor).")
    except KeyboardInterrupt:
        print("\nExiting script. Data saved safely.")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    listen_for_data()