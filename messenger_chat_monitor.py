import os
import socket
import sys
import json
import re
import time
import queue
import threading
from collections import deque
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

import warnings
import logging
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.getLogger("selenium").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

# ============================================================
#  CONFIG
# ============================================================
DEBUG_PORT = 9222
USER_DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\LogicBrowserProfile")
BRAVE_PATH = os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe")
CHROMEDRIVER_PATH = r""

# ============================================================
#  DATA PATHS & NICKNAMES
# ============================================================
DATA_DIR = Path(__file__).resolve().parent / "logic_tools" / "messenger_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
NICKNAMES_FILE = DATA_DIR / "messenger_nicknames.json"
INBOX_URLS_FILE = DATA_DIR / "messenger_inbox_urls.json"

fallback_nick_names = {
    "২ নম্বার খাঁটি মাল (•̀ᴗ•́)": "Hamim hossine",
    "Niggacetti": "Hasan Yamin Hisham",
    "vaiya": "Shahriyer Oishorjo",
}

def load_nicknames():
    res = dict(fallback_nick_names)
    if NICKNAMES_FILE.exists():
        try:
            with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    res.update(data)
        except Exception:
            pass
    return res

def load_inbox_urls():
    if INBOX_URLS_FILE.exists():
        try:
            with open(INBOX_URLS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}

def resolve_identifiers(input_name):
    nick_map = load_nicknames()
    input_lower = input_name.strip().lower()
    sidebar_aliases = [input_lower]
    real_search_name = input_name.strip()

    for nick, real in nick_map.items():
        if input_lower in nick.lower() or nick.lower() in input_lower:
            sidebar_aliases += [nick.lower(), real.lower()]
            return real, sidebar_aliases

    for nick, real in nick_map.items():
        if input_lower in real.lower() or real.lower() in input_lower:
            sidebar_aliases += [nick.lower(), real.lower()]
            return real, sidebar_aliases

    return real_search_name, sidebar_aliases

# ============================================================
#  CHROME LIFECYCLE
# ============================================================
def is_debug_chrome_running():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.05)
        return s.connect_ex(('localhost', DEBUG_PORT)) == 0

def make_driver(options):
    if CHROMEDRIVER_PATH:
        return webdriver.Chrome(service=Service(CHROMEDRIVER_PATH), options=options)
    return webdriver.Chrome(options=options)

def _common_perf_options(options):
    options.page_load_strategy = 'none'
    options.binary_location = BRAVE_PATH
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-features=Translate,MediaRouter,OptimizationHints,TranslateUI")
    return options

def attach_to_existing_chrome():
    options = Options()
    options.add_experimental_option("debuggerAddress", f"localhost:{DEBUG_PORT}")
    options = _common_perf_options(options)
    return make_driver(options)

def launch_new_chrome():
    options = Options()
    options.add_experimental_option("detach", True)
    options.add_experimental_option("prefs", {
        "session.restore_on_startup": 4,
        "session.startup_urls": ["https://www.messenger.com"],
    })
    options.add_argument(f"user-data-dir={USER_DATA_DIR}")
    options.add_argument(f"--remote-debugging-port={DEBUG_PORT}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options = _common_perf_options(options)
    driver = make_driver(options)
    driver.get("https://www.messenger.com")
    return driver

def get_driver():
    if is_debug_chrome_running():
        try:
            d = attach_to_existing_chrome()
            _ = d.current_url
            return d
        except Exception:
            pass
    return launch_new_chrome()

def find_messenger_tab(driver):
    try:
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if "messenger.com" in driver.current_url:
                return True
    except Exception:
        pass
    return False

# ============================================================
#  INBOX RESOLUTION & NAVIGATION
# ============================================================
def _get_current_name(driver, timeout=3):
    try:
        wait = WebDriverWait(driver, timeout)
        h2 = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h2")))
        return h2.text.lower().replace("conversation with", "").replace("conversation titled", "").strip()
    except Exception:
        return ""

def _resolve_nick(target_name, current_name):
    if not current_name:
        return ""
    nick_map = load_nicknames()
    if any(target_name.lower() in v.lower() for v in nick_map.values()):
        for nickname, main_name in nick_map.items():
            if current_name.lower() in nickname.lower():
                return main_name
    return current_name

def find_inbox(driver, name):
    real_search_name, aliases = resolve_identifiers(name)
    wait = WebDriverWait(driver, 15)
    current_raw = _get_current_name(driver, timeout=3)
    name_head = _resolve_nick(real_search_name, current_raw)
    if any(a in name_head.lower() for a in aliases) or real_search_name.lower() in name_head.lower():
        return True

    old_url = driver.current_url
    try:
        search = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input[placeholder='Search Messenger'], input[aria-label='Search Messenger']")
        ))
        search.click()
        search.send_keys(Keys.CONTROL, "a")
        search.send_keys(Keys.BACKSPACE)
        search.send_keys(real_search_name)

        fast_wait = WebDriverWait(
            driver,
            6,
            poll_frequency=0.1,
            ignored_exceptions=(StaleElementReferenceException,)
        )

        contact = fast_wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//li[@role='option' and not(contains(@aria-label, 'Search messages'))]")
            )
        )
        try:
            contact.click()
        except Exception:
            driver.execute_script("arguments[0].click();", contact)

        WebDriverWait(driver, 10).until(lambda d: d.current_url != old_url)

        name_head = _resolve_nick(real_search_name, _get_current_name(driver, timeout=5))
        if not (any(a in name_head.lower() for a in aliases) or real_search_name.lower() in name_head.lower()):
            print(f"[find_inbox] Target '{real_search_name.lower()}' mismatch with '{name_head.lower()}'.")
            return False

        return True
    except Exception as e:
        print(f"[find_inbox] Error: {e}")
        return False

def open_inbox(name):
    driver = get_driver()
    if not find_messenger_tab(driver):
        driver.get("https://www.messenger.com")

    if find_inbox(driver, name):
        print(f"Inbox for '{name}' is now open.")
        return driver
    else:
        print(f"Failed to open inbox for '{name}'.")
        return None

# ============================================================
#  MESSENGER BOT CLASS
# ============================================================
class MessengerBot:
    def __init__(self):
        self.driver = None
        self.current_inbox_msg = []
        self.seen_msg_ids = deque(maxlen=500)
        self.running = False
        self.thread = None
        self.injection_queue = queue.Queue()
        self.prev_name = None
        self.current_inbox = None
        self.inbox_changed = False

    def start(self, name, interval=3):
        self.driver = self.setup_driver(name)
        if self.driver is None:
            print("[Monitor] Failed to start — driver is None")
            return
        self.running = True
        self.thread = threading.Thread(target=self.run_loop, args=(name, interval), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        # Never call driver.quit() to preserve persistent automation session

    def setup_driver(self, name):
        driver = get_driver()
        if not find_messenger_tab(driver):
            driver.get("https://www.messenger.com")

        if find_inbox(driver, name):
            print(f"Inbox for '{name}' is now open.")
            self.prev_name = name
            return driver
        else:
            print(f"Failed to open inbox for '{name}'.")
            return None

    def run_loop(self, name, interval):
        try:
            elements = self.driver.find_elements(By.CSS_SELECTOR, '[aria-roledescription="message"][data-message-id][aria-label]')
            for e in elements:
                msg_id = e.get_attribute('data-message-id')
                if msg_id:
                    self.seen_msg_ids.append(msg_id)

            while self.running:
                try:
                    self.check_messages(name)
                except WebDriverException as wde:
                    print(f"[Monitor] WebDriver error, attempting reconnect: {wde}")
                    time.sleep(2)
                    self.driver = get_driver()
                except Exception as inner_e:
                    print(f"[Monitor] Check cycle error: {inner_e}")
                time.sleep(interval)
        except Exception as e:
            print(f"[Monitor] Thread terminated: {e}")

    def check_messages(self, name):
        elements = self.driver.find_elements(By.CSS_SELECTOR, '[aria-roledescription="message"][data-message-id][aria-label]')
        if not elements:
            return

        current_name_raw = _get_current_name(self.driver, timeout=1)
        if current_name_raw:
            current_name = _resolve_nick(name, current_name_raw)
            if self.prev_name and self.prev_name.lower() not in current_name.lower():
                self.current_inbox = current_name
                self.prev_name = current_name
                self.inbox_changed = True
                self.current_inbox_msg.clear()

        new_msgs = []
        for e in elements[-5:]:
            msg_id = e.get_attribute('data-message-id')
            if not msg_id or msg_id in self.seen_msg_ids:
                continue
            self.seen_msg_ids.append(msg_id)

            aria = e.get_attribute('aria-label')
            if aria:
                cleaned = aria.replace(' ', ' ').strip()
                new_msgs.append(cleaned)
                self.current_inbox_msg.append(cleaned)

        if new_msgs:
            latest = new_msgs[-1]
            # Match word boundary 'logic' and avoid self-triggering
            if re.search(r'\blogic\b', latest, re.IGNORECASE) and "Apurbo's AI companion" not in latest:
                if not self.inbox_changed:
                    self.injection_queue.put(self.current_inbox_msg.copy())
                else:
                    self.injection_queue.put([f"[INBOX CHANGED TO: {self.current_inbox}]"] + self.current_inbox_msg.copy())
                    self.inbox_changed = False
                self.current_inbox_msg.clear()

    def get_injection(self):
        try:
            return self.injection_queue.get_nowait()
        except queue.Empty:
            return None

if __name__ == "__main__":
    bot = MessengerBot()
    bot.start('Apurbo')

    try:
        while True:
            msg = bot.get_injection()
            if msg:
                print(f"INJECTED: {msg}")
            time.sleep(1)
    except KeyboardInterrupt:
        bot.stop()
        print("Monitor stopped")

def start_monitor(name):
    bot = MessengerBot()
    bot.start(name)
    return bot
