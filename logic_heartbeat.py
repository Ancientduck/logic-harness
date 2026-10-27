"""
Logic Heartbeat & Presence Monitor
Author: LOGIC / Apurbo
"""

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

LOGIC_TOOLS_DIR = r"D:\Ai\logic\logic_tools"

QUIET_SUBSTRINGS = [
    "no unread messages",
    "no unread emails",
    "no new notifications",
    "no notifications found",
    "device not found",
    "adb server",
    "daemon not running",
    "error running adb command",
    "no events found",
]

SCHEDULE_WINDOW_MINUTES = 30
MAX_SEEN = 500


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


def get_idle_duration_seconds() -> float:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return max(0.0, millis / 1000.0)


class Heart:
    def __init__(self, afk_threshold_seconds: int = 600):
        self.afk_threshold = afk_threshold_seconds
        self.is_afk = False
        self.afk_start_time = None
        self._pending_presence_alert = None
        self._lock = threading.Lock()
        self._seen = OrderedDict()

        self._monitor_thread = threading.Thread(target=self._afk_loop, daemon=True)
        self._monitor_thread.start()

    def _afk_loop(self):
        while True:
            time.sleep(1.0)
            idle = get_idle_duration_seconds()

            with self._lock:
                if not self.is_afk:
                    if idle >= self.afk_threshold:
                        self.is_afk = True
                        self.afk_start_time = datetime.now() - timedelta(seconds=idle)
                else:
                    if idle < 3.0:
                        away = datetime.now() - (self.afk_start_time or datetime.now())
                        secs = int(away.total_seconds())
                        mins = max(1, secs // 60)
                        self._pending_presence_alert = (
                            f"User is back, system was idle for ~{secs} second(s) (~{mins} min)."
                        )
                        self.is_afk = False
                        self.afk_start_time = None

    def pop_presence_alert(self):
        with self._lock:
            alert = self._pending_presence_alert
            self._pending_presence_alert = None
            return alert

    @staticmethod
    def _run_script(script_name: str, args: list = None, timeout: int = 25) -> str:
        script_path = os.path.join(LOGIC_TOOLS_DIR, script_name)
        if not os.path.isfile(script_path):
            return ""

        cmd = [sys.executable, script_path] + (args or [])
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONLEGACYWINDOWSSTDIO="0")
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, env=env)
            raw = res.stdout.strip()
            clean_lines = [
                line for line in raw.splitlines()
                if not line.strip().startswith("DevTools listening on ws://")
            ]
            out = "\n".join(clean_lines).strip()
            if not out and res.returncode != 0:
                err = (res.stderr or "").strip().splitlines()
                out = "Error executing {}: {}".format(
                    script_name, err[-1] if err else "exit {}".format(res.returncode)
                )
            return out
        except subprocess.TimeoutExpired:
            return ""
        except Exception as e:
            return f"Error executing {script_name}: {e}"

    @staticmethod
    def _is_quiet(text: str) -> bool:
        if not text:
            return True
        low = text.lower().strip()
        return any(q in low for q in QUIET_SUBSTRINGS)

    @staticmethod
    def _is_error(text: str) -> bool:
        if not text:
            return False
        low = text.lower()
        return (
            low.startswith("error executing")
            or "traceback (most recent call last)" in low
            or "syntaxerror" in low
        )

    def check_schedule(self) -> str:
        out = self._run_script("get_schedule.py")
        if self._is_quiet(out):
            return ""

        now = datetime.now()
        upcoming_lines = []
        in_upcoming = False

        for line in out.splitlines():
            s = line.strip()
            if "Upcoming:" in s:
                in_upcoming = True
                continue
            if in_upcoming:
                if not s or s.startswith("---") or s.startswith("Past:"):
                    break
                upcoming_lines.append(s)

        time_pattern = re.compile(r"(\d{1,2}:\d{2}\s*(?:AM|PM))", re.IGNORECASE)
        imminent = []

        for event in upcoming_lines:
            m = time_pattern.findall(event)
            if not m:
                continue
            try:
                event_dt = datetime.strptime(m[0].upper(), "%I:%M %p").replace(
                    year=now.year, month=now.month, day=now.day
                )
            except Exception:
                continue

            diff = (event_dt - now).total_seconds() / 60.0
            if diff < 0:
                continue
            if diff <= SCHEDULE_WINDOW_MINUTES:
                imminent.append(f"{event} (in {int(diff)}m)")

        return "\n".join(imminent)

    def check_gmail(self) -> str:
        return self._run_script("check_gmail.py")

    def check_messenger(self) -> str:
        return self._run_script("read_messenger_unread_messages.py", timeout=30)

    def check_phone(self) -> str:
        return self._run_script("get_phone_notifications.py", timeout=15)

    def _collect(self, section: str, out: str, results: dict):
        if not out:
            return
        if self._is_error(out):
            results.setdefault("Diagnostics", []).append(f"[{section}] {out}")
            return
        if self._is_quiet(out):
            return
        results.setdefault(section, []).append(out)

    def beat(self, checks: dict) -> dict:
        """Run only the supplied checks. Key = section name, value = callable."""
        if not checks:
            return {}
        results = {}
        with ThreadPoolExecutor(max_workers=len(checks)) as pool:
            futures = {pool.submit(fn): name for name, fn in checks.items()}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    out = fut.result()
                except Exception as e:
                    out = f"Error executing {name}: {e}"
                self._collect(name, out, results)

        fresh = {}
        with self._lock:
            for section, chunks in results.items():
                keep = []
                for chunk in chunks:
                    key = (section, chunk.strip())
                    if key in self._seen:
                        self._seen.move_to_end(key)
                        continue
                    self._seen[key] = True
                    keep.append(chunk)
                if keep:
                    fresh[section] = "\n".join(keep)
            while len(self._seen) > MAX_SEEN:
                self._seen.popitem(last=False)

        return fresh


# ---------------------------------------------------------------- audit log
PULSE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logic_heartbeat.log")
LOG_MAX_BYTES = 512_000

def log_pulse(report: dict):
    """Append every pulse to a rolling audit log so silent failures stay visible."""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not report:
            entry = "[%s] quiet\n" % stamp
        else:
            lines = ["[%s] ALERT" % stamp]
            for section, content in report.items():
                for ln in str(content).splitlines():
                    lines.append("    <%s> %s" % (section, ln))
            entry = "\n".join(lines) + "\n"
        with open(PULSE_LOG, "a", encoding="utf-8") as f:
            f.write(entry)
        if os.path.getsize(PULSE_LOG) > LOG_MAX_BYTES:
            with open(PULSE_LOG, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-2000:]
            with open(PULSE_LOG, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except Exception:
        pass


# ---------------------------------------------------------------- scheduler
TICK_SECONDS = 5
CLOCK_SLOTS = (0, 30)            # heartbeat fires at :00 and :30
CLOCK_CATCHUP_MINUTES = 5        # still fire if we start slightly late
MESSAGE_SLOT_MINUTES = 10        # message sweep on :00 :10 :20 :30 :40 :50
MESSAGE_CATCHUP_MINUTES = 2      # tolerance for a late tick


def last_clock_slot(now, slots=CLOCK_SLOTS, catchup_minutes=CLOCK_CATCHUP_MINUTES):
    """Most recent aligned slot, or None if it is already too stale to be useful."""
    candidates = []
    for m in slots:
        slot = now.replace(minute=m, second=0, microsecond=0)
        if slot > now:
            slot -= timedelta(hours=1)
        candidates.append(slot)
    slot = max(candidates)
    if (now - slot).total_seconds() <= catchup_minutes * 60:
        return slot
    return None


def last_message_slot(now, interval_minutes=MESSAGE_SLOT_MINUTES,
                      catchup_minutes=MESSAGE_CATCHUP_MINUTES):
    """Most recent wall-clock message slot, or None if it is too stale."""
    slot = now.replace(minute=(now.minute // interval_minutes) * interval_minutes,
                       second=0, microsecond=0)
    if (now - slot).total_seconds() <= catchup_minutes * 60:
        return slot
    return None


def run_job(heart, label, checks):
    print("\n[%s: %s]" % (label, datetime.now().strftime("%I:%M:%S %p")))
    try:
        report = heart.beat(checks)
    except Exception as e:
        report = {"Diagnostics": "[%s] beat() failed: %s" % (label, e)}
    log_pulse(report)

    if report:
        print("=== %s Alert (%s) ===" % (label, datetime.now().strftime("%I:%M %p | %B %d, %Y")))
        for section, content in report.items():
            print("--- [%s] ---" % section)
            print(content)
            print()
    else:
        print("Status: All quiet.")
    return report


if __name__ == "__main__":
    AFK_THRESHOLD_SECONDS = 600

    heart = Heart(afk_threshold_seconds=AFK_THRESHOLD_SECONDS)
    print("Heart initialized.")
    print("  schedule : :00 / :30  (30 min lookahead)")
    print("  messages : every %d min, on the clock" % MESSAGE_SLOT_MINUTES)
    print("  tick     : %ds" % TICK_SECONDS)

    # Seed with the slots already in progress so a restart never fires a job
    # immediately. Jobs run only when a *new* slot begins.
    last_slot = last_clock_slot(datetime.now())
    last_message_slot_seen = last_message_slot(datetime.now())

    while True:
        try:
            now = datetime.now()

            presence = heart.pop_presence_alert()
            if presence:
                print("\n--- %s ---" % presence)

            # clock job - only on the half hour
            slot = last_clock_slot(now)
            if slot is not None and slot != last_slot:
                last_slot = slot
                run_job(heart, "Schedule", {"Schedule": heart.check_schedule})

            # interval job - messages every 10 minutes, aligned to the clock
            msg_slot = last_message_slot(now)
            if msg_slot is not None and msg_slot != last_message_slot_seen:
                last_message_slot_seen = msg_slot
                run_job(heart, "Messages", {
                    "Gmail": heart.check_gmail,
                    "Messenger": heart.check_messenger,
                    "Phone Notifications": heart.check_phone,
                })

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print("[heartbeat] loop error: %s" % e)

        time.sleep(TICK_SECONDS)
