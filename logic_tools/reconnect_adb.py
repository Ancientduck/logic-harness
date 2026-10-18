import concurrent.futures
import platform
import socket
import subprocess
import time
import sys

def get_local_subnet():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            return ".".join(local_ip.split(".")[:3]) + "."
    except Exception:
        return "192.168.0."

def ping_ip(ip):
    is_windows = platform.system().lower() == "windows"
    param = "-n" if is_windows else "-c"
    timeout_param = "-w" if is_windows else "-W"
    timeout_val = "50" if is_windows else "1"
    creationflags = subprocess.CREATE_NO_WINDOW if is_windows else 0
    
    try:
        res = subprocess.run(
            ["ping", param, "1", timeout_param, timeout_val, ip],
            capture_output=True,
            creationflags=creationflags
        )
        return ip if res.returncode == 0 else None
    except Exception:
        return None

def ping_sweep():
    base = get_local_subnet()
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        futures = {executor.submit(ping_ip, f"{base}{i}"): i for i in range(1, 255)}
        live_ips = []
        for future in concurrent.futures.as_completed(futures):
            ip = future.result()
            if ip:
                live_ips.append(ip)
    return live_ips

def try_connect():
    live_ips = ping_sweep()
    for ip in live_ips:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            try:
                s.connect((ip, 5555))
                print(f"[+] Found device at {ip}. Connecting...")
                connect_res = subprocess.run(["adb", "connect", f"{ip}:5555"], capture_output=True, text=True)
                print(connect_res.stdout.strip())
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                pass
    return False

if __name__ == "__main__":
    print("[*] Starting Wireless ADB Auto-Connector (Press Ctrl+C to stop)...")
    
    # Ensure ADB server is running fresh
    subprocess.run(["adb", "kill-server"], capture_output=True)
    subprocess.run(["adb", "start-server"], capture_output=True)

    # Continuous loop until connection is established
    while True:
        if try_connect():
            print("[+] Successfully connected! Exiting script.")
            sys.exit(0)
        
        # Wait 5 seconds before retrying so it doesn't spam CPU
        time.sleep(5)