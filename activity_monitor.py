import datetime
import win32process
import win32api
import win32con
import win32gui
import win32com.client
import time
import os
import ctypes
import ctypes.wintypes
from collections import deque
import uiautomation as auto
import urllib.parse
import comtypes
from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

# Short global timeout so UI Automation doesn't hang the monitor
auto.uiautomation.SetGlobalSearchTimeout(0.3)

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwTime", ctypes.wintypes.DWORD)
    ]

class ActivityMonitor:

    def __init__(self, ignored_processes=None, idle_threshold=300):
        self.ignored_processes = ignored_processes or [
            "windowsterminal.exe",
            "powershell.exe",
            "cmd.exe",
            "conhost.exe"
        ]

        self.browsers = {
            "chrome.exe",
            "msedge.exe",
            "brave.exe",
            "firefox.exe"
        }

        self.idle_threshold = idle_threshold
        self.is_idle = False

        self.last_hwnd = None
        self.last_title = ""
        self.last_proc_name = ""
        self.last_path = None
        self.last_url = ""

        self.start_time = time.perf_counter()

        self.history = {}
        self.recent_history = deque(maxlen=10)

        self._tracking = False

    def get_idle_duration(self):
        """Returns time in seconds since last user keyboard/mouse input."""
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis_since_input = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, millis_since_input / 1000.0)
        return 0.0

    def is_audio_playing(self):
        """Ultra-lightweight audio check via default audio device peak meter."""
        try:
            device = AudioUtilities.GetSpeakers()
            if not device:
                return False
            meter = device.Activate(
                IAudioMeterInformation._iid_,
                comtypes.CLSCTX_ALL,
                None
            )
            meter_info = ctypes.cast(meter, ctypes.POINTER(IAudioMeterInformation))
            peak = meter_info.GetPeakValue()
            return peak > 0.001
        except Exception:
            return False

    def check_user_idle(self):
        """
        Determines whether the user is truly idle:
        Inactivity > threshold AND no audio output playing.
        """
        inactivity = self.get_idle_duration()
        if inactivity >= self.idle_threshold:
            if not self.is_audio_playing():
                return True, inactivity
        return False, inactivity

    def _get_process_name(self, hwnd):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid
            )

            name = win32process.GetModuleFileNameEx(handle, 0)

            win32api.CloseHandle(handle)

            return os.path.basename(name).lower()

        except Exception:
            return ""

    def _get_explorer_path(self, hwnd):
        try:
            shell = win32com.client.Dispatch("Shell.Application")

            for window in shell.Windows():
                try:
                    if int(window.HWND) == int(hwnd):
                        return window.Document.Folder.Self.Path
                except Exception:
                    continue

        except Exception:
            pass

        return None

    def _get_browser_url(self, hwnd, proc_name):
        """
        Attempts to retrieve the browser's current URL.

        UI Automation can fail temporarily while the browser is
        changing tabs/pages, so failure simply returns an empty URL.
        """

        try:
            window = auto.WindowControl(
                searchDepth=1,
                HWND=hwnd
            )

            if not window.Exists(0.2):
                return ""

            edit = window.EditControl(
                searchDepth=10
            )

            # Prevent the lookup from waiting indefinitely.
            if not edit.Exists(0.5):
                return ""

            try:
                pattern = edit.GetValuePattern()
            except Exception:
                return ""

            if not pattern:
                return ""

            try:
                url = pattern.Value or ""
            except Exception:
                return ""

            url = url.strip()

            if not url:
                return ""

            # Filter out normal search queries.
            try:
                parsed = urllib.parse.urlparse(url)

                if (
                    "search" in parsed.path.lower()
                    or "q=" in parsed.query.lower()
                ):
                    return ""

            except Exception:
                pass

            return url

        except Exception:
            return ""

    def get_current_focus(self):
        hwnd = win32gui.GetForegroundWindow()

        title = win32gui.GetWindowText(hwnd)

        proc_name = self._get_process_name(hwnd)

        path = ""

        if proc_name == "explorer.exe":
            path = self._get_explorer_path(hwnd)

        return hwnd, title, proc_name, path

    def format_time(self, seconds):
        seconds = int(seconds + 0.5)

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        res = []

        if hours > 0:
            res.append(f"{hours}h")

        if minutes > 0 or hours > 0:
            res.append(f"{minutes}m")

        res.append(f"{secs}s")

        return " ".join(res)

    def monitor(self):
        while True:
            idle_now, inactivity_time = self.check_user_idle()

            if idle_now:
                if not self.is_idle:
                    # User transitioned to IDLE
                    self.is_idle = True
                    if self._tracking and self.last_hwnd is not None:
                        active_elapsed = max(0.0, time.perf_counter() - self.start_time - inactivity_time)
                        if active_elapsed > 0.1:
                            key = self.last_proc_name
                            self.history[key] = self.history.get(key, 0) + active_elapsed

                        yield (
                            self.last_title,
                            self.last_proc_name,
                            self.last_path,
                            self.last_url,
                            active_elapsed,
                            self.history.get(self.last_proc_name, 0),
                            "IDLE",
                            "idle",
                            "",
                            ""
                        )
                time.sleep(1.0)
                continue

            if self.is_idle:
                # User resumed activity from IDLE
                self.is_idle = False
                self.start_time = time.perf_counter()

            hwnd, title, proc_name, path = self.get_current_focus()

            is_valid = not (
                proc_name in self.ignored_processes
                or not title.strip()
            )

            if is_valid:

                if (
                    hwnd != self.last_hwnd
                    or title != self.last_title
                    or path != self.last_path
                ):

                    url = ""

                    if proc_name in self.browsers:
                        url = self._get_browser_url(
                            hwnd,
                            proc_name
                        )

                    if (
                        self._tracking
                        and self.last_hwnd is not None
                    ):
                        elapsed = (
                            time.perf_counter()
                            - self.start_time
                        )

                        if elapsed > 0.1:

                            key = self.last_proc_name

                            self.history[key] = (
                                self.history.get(key, 0)
                                + elapsed
                            )

                            yield (
                                self.last_title,
                                self.last_proc_name,
                                self.last_path,
                                self.last_url,
                                elapsed,
                                self.history[key],
                                title,
                                proc_name,
                                path,
                                url
                            )

                    self.last_hwnd = hwnd
                    self.last_title = title
                    self.last_proc_name = proc_name
                    self.last_path = path
                    self.last_url = url

                    self.start_time = time.perf_counter()

                    self._tracking = True

            else:

                if not self._tracking:
                    self.last_hwnd = ""
                    self.last_title = ""
                    self.last_proc_name = ""
                    self.last_path = "None"
                    self.last_url = ""
                    self.start_time = time.perf_counter()

            time.sleep(0.5)


    def get_live_report(self):
        time_now = datetime.datetime.now().strftime("%I:%M %p")
        if not self._tracking or (not self.last_title and not self.last_proc_name):
            return f"[Activity] {time_now} | No activity data yet |"

        current_elapsed = max(0.0, time.perf_counter() - self.start_time)
        total_time = self.history.get(self.last_proc_name, 0) + current_elapsed
        url_str = f" {self.last_url}" if self.last_url else ""
        path_str = f" {self.last_path}" if (self.last_path and self.last_path != "None") else ""

        return (
            f"[Activity] {time_now} | "
            f"current: {self.last_title} ({self.last_proc_name}){path_str}{url_str} | "
            f"+{self.format_time(current_elapsed)} (total {self.format_time(total_time)}) |"
        )

monitor = ActivityMonitor()

if __name__ == "__main__":

    print("Monitoring started. Press Ctrl+C to stop.")

    try:

        for (
            last_title,
            last_proc,
            last_path,
            last_url,
            elapsed,
            total,
            new_title,
            new_proc,
            new_path,
            new_url
        ) in monitor.monitor():

            url_str = (
                f" | URL: {last_url}"
                if last_url
                else ""
            )

            print(
                f"[Activity] "
                f"was: {last_title} "
                f"({last_proc}) "
                f"{last_path}"
                f"{url_str} | "
                f"+{monitor.format_time(elapsed)} "
                f"(total {monitor.format_time(total)}) | "
                f"now: {new_title} "
                f"({new_proc}) "
                f"{new_path}"
            )

    except KeyboardInterrupt:
        print("Monitoring stopped.")
