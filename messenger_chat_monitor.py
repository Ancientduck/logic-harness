import datetime
import os
import sys
import time
import socket
import logging
import threading
from collections import deque
import json
import re
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MessengerMonitor")

POLL_INTERVAL = 5.0  # Polling interval in seconds
BRAVE_PROFILE_DIR = r"C:\Users\USER\AppData\Local\BraveSoftware\Brave-Browser\LogicBrowserProfile"
DEFAULT_BRAVE_PATH = r"C:\Users\USER\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"

DATA_DIR = Path(__file__).resolve().parent / "messenger_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
NICKNAMES_FILE = DATA_DIR / "messenger_nicknames.json"
INBOX_URLS_FILE = DATA_DIR / "messenger_inbox_urls.json"

last_known_urls = []
injection_queue = deque()
_active_bot = None

def get_last_url():
    if last_known_urls:
        return last_known_urls[-1]
    return "https://www.messenger.com"

def save_last_url(url):
    if url and "/t/" in url:
        if not last_known_urls or last_known_urls[-1] != url:
            last_known_urls.append(url)

def load_nicknames():
    if NICKNAMES_FILE.exists():
        try:
            return json.loads(NICKNAMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def load_inbox_urls():
    if INBOX_URLS_FILE.exists():
        try:
            return json.loads(INBOX_URLS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_inbox_url(real_name, url):
    if not real_name or not url or "/t/" not in url:
        return
    cache = load_inbox_urls()
    cache[real_name.strip().lower()] = url.strip()
    try:
        INBOX_URLS_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def resolve_identifiers(input_name):
    nicknames = load_nicknames()
    name_clean = input_name.strip()
    name_lower = name_clean.lower()
    real_search_name = nicknames.get(name_lower, name_clean)
    aliases = [name_lower]
    real_lower = real_search_name.lower()
    if real_lower not in aliases:
        aliases.append(real_lower)
    first_part = real_search_name.split()[0].lower() if real_search_name else ""
    if first_part and first_part not in aliases:
        aliases.append(first_part)
    return real_search_name, aliases

def is_port_open(host="127.0.0.1", port=9222, timeout=0.2):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def start_driver():
    brave_path = DEFAULT_BRAVE_PATH
    if not os.path.exists(brave_path):
        logger.error(f"Brave not found at {brave_path}")
        return None

    if not is_port_open(port=9222):
        target_url = get_last_url()
        import subprocess
        cmd = [
            brave_path,
            "--remote-debugging-port=9222",
            f"--user-data-dir={BRAVE_PROFILE_DIR}",
            target_url
        ]
        logger.info(f"Port 9222 not running. Spawning Brave with {target_url}...")
        subprocess.Popen(cmd)
        
        for _ in range(30):
            if is_port_open(port=9222):
                break
            time.sleep(0.2)

    try:
        options = Options()
        options.debugger_address = "127.0.0.1:9222"
        options.add_argument("--disable-gpu")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        driver_155 = r"C:\Users\USER\.cache\selenium\chromedriver\win64\155.0.8059.84\chromedriver.exe"
        if os.path.exists(driver_155):
            service = ChromeService(executable_path=driver_155)
            driver = webdriver.Chrome(service=service, options=options)
        else:
            driver = webdriver.Chrome(options=options)
        return driver
    except Exception as e:
        logger.error(f"Failed to connect to Brave on 9222: {e}")
        return None

def wait_for_messenger_ready(driver, timeout=20):
    wait = WebDriverWait(driver, timeout, poll_frequency=0.1,
                         ignored_exceptions=(StaleElementReferenceException,))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='navigation']")))
    return wait

def wait_for_thread_url(driver, thread_id, timeout=6):
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05,
                      ignored_exceptions=(StaleElementReferenceException,)).until(
            lambda d: thread_id in d.current_url)
        return True
    except Exception:
        return False

def wait_for_composer_mounted(driver, timeout=6):
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05,
                      ignored_exceptions=(StaleElementReferenceException,)).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, "div[role='textbox'], div[aria-label='Message']"))
        return True
    except Exception:
        return False

def focus_messenger_tab(driver):
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if "messenger.com" in driver.current_url:
            return True
    driver.get(get_last_url())
    return True

def get_conversation_header_text(driver):
    selectors = [
        "h2",
        "div[role='main'] div[dir='auto'] span",
        "div[aria-label='Conversation Information'] span"
    ]
    for sel in selectors:
        for elem in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                t = elem.text.strip()
                if t and len(t) < 80 and not any(k in t.lower() for k in ["chats", "marketplace", "requests", "search"]):
                    return t
            except Exception:
                continue
    return ""

def clean_display_name(text):
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines else text.strip()

def check_current_thread(driver, aliases, real_search_name):
    header = get_conversation_header_text(driver)
    if header:
        h_lower = header.lower()
        for alias in aliases:
            if alias in h_lower or h_lower in alias:
                return real_search_name
    return None

def try_select_by_target_url(driver, target_url):
    try:
        thread_id = target_url.rstrip("/").split("/")[-1]
        links = driver.find_elements(By.XPATH, f"//a[contains(@href, '{thread_id}')]")
        for link in links:
            if link.is_displayed():
                try:
                    link.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", link)
                return True
    except Exception:
        pass
    return False

def try_select_from_sidebar(driver, aliases):
    try:
        rows = driver.find_elements(By.XPATH, "//div[@role='row' or @role='gridcell']//a")
        for row in rows:
            text = (row.get_attribute("aria-label") or row.text).lower()
            for alias in aliases:
                if alias in text:
                    try:
                        row.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", row)
                    return clean_display_name(text)
    except Exception:
        pass
    return None

def navigate_to_chat(driver, target_name):
    real_search_name, aliases = resolve_identifiers(target_name)
    try:
        wait = wait_for_messenger_ready(driver, timeout=15)
    except Exception:
        wait = WebDriverWait(driver, 10, poll_frequency=0.1)

    matched = check_current_thread(driver, aliases, real_search_name)
    if matched:
        cur_url = driver.current_url
        save_inbox_url(real_search_name, cur_url)
        save_last_url(cur_url)
        return True

    cached_urls = load_inbox_urls()
    target_url = cached_urls.get(real_search_name.lower())
    if not target_url:
        for alias in aliases:
            if alias in cached_urls:
                target_url = cached_urls[alias]
                break

    # Attempt 0: cached /t/ URL direct match or sidebar link click
    if target_url and "/t/" in target_url:
        thread_id = target_url.rstrip("/").split("/")[-1]
        clicked = False
        if thread_id in driver.current_url:
            clicked = True
        elif try_select_by_target_url(driver, target_url):
            clicked = True

        if clicked:
            url_ok = wait_for_thread_url(driver, thread_id, timeout=4)
            composer_ok = wait_for_composer_mounted(driver, timeout=4) if url_ok else False
            if url_ok and composer_ok:
                save_last_url(driver.current_url)
                return True

    # Attempt 1: sidebar text match
    label = try_select_from_sidebar(driver, aliases)
    if label:
        try:
            wait_for_composer_mounted(driver, timeout=4)
            if check_current_thread(driver, aliases, real_search_name):
                cur_url = driver.current_url
                save_inbox_url(real_search_name, cur_url)
                save_last_url(cur_url)
                return True
        except Exception:
            pass

    # Attempt 2: direct load of cached URL
    if target_url and "/t/" in target_url and driver.current_url.rstrip("/") != target_url.rstrip("/"):
        driver.get(target_url)
        try:
            wait_for_messenger_ready(driver, timeout=10)
            wait_for_composer_mounted(driver, timeout=5)
            if check_current_thread(driver, aliases, real_search_name):
                save_last_url(driver.current_url)
                return True
        except Exception:
            pass

    # Attempt 3: Search bar with explicit waits (no sleep)
    try:
        search = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input[placeholder='Search Messenger'], input[aria-label='Search Messenger']")
        ))
        search.click()
        search.send_keys(Keys.CONTROL, "a")
        search.send_keys(Keys.BACKSPACE)
        search.send_keys(real_search_name)

        wait_search = WebDriverWait(driver, 6, poll_frequency=0.05,
                                    ignored_exceptions=(StaleElementReferenceException,))
        contact = wait_search.until(EC.element_to_be_clickable(
            (By.XPATH, "//li[@role='option' and not(contains(@aria-label, 'Search messages'))]")
        ))

        try:
            contact.click()
        except Exception:
            driver.execute_script("arguments[0].click();", contact)

        wait_for_composer_mounted(driver, timeout=6)
        cur_url = driver.current_url
        if "/t/" in cur_url:
            save_inbox_url(real_search_name, cur_url)
            save_last_url(cur_url)
        return True
    except Exception as e:
        logger.error(f"Search navigation failed: {e}")
        return False

class MessengerBot:
    def __init__(self, target_chat=None):
        self.driver = None
        self.target_chat = target_chat
        self.target_url = None
        self.processed_ids = deque(maxlen=500)
        self.pending_buffer = []
        self.msg_queue = []
        self.stop_event = threading.Event()
        self.thread = None
        self.current_inbox = None
        self.drift_attempts = 0

    def connect_inbox(self, target_name):
        self.driver = start_driver()
        if not self.driver:
            return False
        focus_messenger_tab(self.driver)
        self.target_chat = target_name
        success = navigate_to_chat(self.driver, target_name)
        self.current_inbox = get_conversation_header_text(self.driver) or target_name
        cur_url = self.driver.current_url
        if "/t/" in cur_url:
            self.target_url = cur_url
            save_last_url(cur_url)
        # Pre-seed processed IDs so historical messages already on screen aren't treated as new
        try:
            existing = self.driver.find_elements(By.XPATH, "//div[@aria-roledescription='message'][@data-message-id]")
            for node in existing:
                mid = node.get_attribute("data-message-id")
                if mid and mid not in self.processed_ids:
                    self.processed_ids.append(mid)
        except Exception:
            pass
        return success

    def switch_target(self, new_target):
        logger.info(f"Switching primary monitoring target to: {new_target}")
        self.target_chat = new_target
        if self.driver:
            navigate_to_chat(self.driver, new_target)
            self.current_inbox = get_conversation_header_text(self.driver) or new_target
            cur_url = self.driver.current_url
            if "/t/" in cur_url:
                self.target_url = cur_url
                save_last_url(cur_url)
            try:
                existing = self.driver.find_elements(By.XPATH, "//div[@aria-roledescription='message'][@data-message-id]")
                for node in existing:
                    mid = node.get_attribute("data-message-id")
                    if mid and mid not in self.processed_ids:
                        self.processed_ids.append(mid)
            except Exception:
                pass
        return True

    def start(self, target_chat=None, *args, **kwargs):
        if target_chat:
            self.connect_inbox(target_chat)
        return self.start_monitoring()

    def start_monitoring(self):
        if not self.driver:
            logger.error("Driver not connected.")
            return False
        if self.thread and self.thread.is_alive():
            logger.info("Monitor loop already running.")
            return True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info(f"Monitoring started for '{self.target_chat}'.")
        return True

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        logger.info("Monitoring stopped.")


    def get_injection(self):
        if not hasattr(self, 'msg_queue') or not self.msg_queue:
            return None
        items = []
        while self.msg_queue:
            m = self.msg_queue.pop(0)
            items.append(f"[{m.get('timestamp', '')}] {m.get('sender', 'Sender')}: {m.get('content', '')}")
        return "\n".join(items) if items else None

    def poll_new_messages(self, timeout=None):
        wait_time = POLL_INTERVAL if timeout is None else timeout
        time.sleep(wait_time)
        batch = []
        while hasattr(self, 'msg_queue') and self.msg_queue:
            batch.append(self.msg_queue.pop(0))
        return [f"[{m['timestamp']}] {m['sender']}: {m['content']}" for m in batch]

    def _monitor_loop(self):
        while not self.stop_event.is_set():
            try:
                if self.target_chat:
                    real_search_name, aliases = resolve_identifiers(self.target_chat)
                    matched = check_current_thread(self.driver, aliases, real_search_name)
                    if not matched:
                        self.drift_attempts += 1
                        is_empty = True
                        try:
                            # Messenger uses Lexical editor: span[data-lexical-text='true'] contains typed text
                            lexical_spans = self.driver.find_elements(By.XPATH, "//span[@data-lexical-text='true']")
                            for s in lexical_spans:
                                if s.text and s.text.strip():
                                    is_empty = False
                                    break
                            
                            if is_empty:
                                composers = self.driver.find_elements(By.XPATH, "//div[@role='textbox']")
                                for c in composers:
                                    if c.text and c.text.strip():
                                        is_empty = False
                                        break
                        except Exception:
                            pass

                        if is_empty or self.drift_attempts >= 3:
                            logger.info(f"Returning to '{self.target_chat}' (drift attempt {self.drift_attempts}, empty={is_empty})...")
                            navigate_to_chat(self.driver, self.target_chat)
                            self.drift_attempts = 0
                        else:
                            logger.info(f"Drift detected but composer active. Attempt {self.drift_attempts}/3, waiting 5s...")
                        
                        self.stop_event.wait(5)
                        continue
                    else:
                        self.drift_attempts = 0
                        cur_url = self.driver.current_url
                        if "/t/" in cur_url:
                            self.target_url = cur_url
                            save_last_url(cur_url)

                msg_nodes = self.driver.find_elements(
                    By.XPATH, 
                    "//div[@aria-roledescription='message'][@data-message-id][@aria-label]"
                )
                
                if not msg_nodes:
                    self.stop_event.wait(5)
                    continue

                new_batch = []
                for node in msg_nodes[-10:]:
                    try:
                        msg_id = node.get_attribute("data-message-id")
                        if not msg_id or msg_id in self.processed_ids:
                            continue

                        self.processed_ids.append(msg_id)
                        aria_label = node.get_attribute("aria-label") or ""
                        
                        # Clean unicode spaces / artifacts
                        aria_label = aria_label.replace('\u202f', ' ').replace('\xa0', ' ').replace('\u200e', '').strip()
                        raw_text = node.text.replace('\u202f', ' ').replace('\xa0', ' ').replace('\u200e', '').strip()

                        # Strip status prefixes/suffixes ("You sent", "Sent", "Delivered", "Seen") that Messenger attaches to node.text
                        clean_lines = []
                        for line in raw_text.splitlines():
                            l = line.strip()
                            if not l:
                                continue
                            low = l.lower()
                            if low in ['sent', 'delivered', 'seen', 'you sent']:
                                continue
                            if low.startswith('you sent'):
                                l = l[8:].strip()
                                if not l:
                                    continue
                            clean_lines.append(l)
                        text_content = "\n".join(clean_lines).strip()

                        if not text_content:
                            continue

                        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        # Resolve actual sender from aria-label or fallback
                        # Typical Facebook formats:
                        # 1. "Name: message"
                        # 2. "At 1:58 PM, Name said: message"
                        # 3. "At 1:58 PM, you sent..."
                        # 4. "Name sent a message..."
                        sender = "Unknown"
                        if aria_label:
                            # Messenger aria-labels typically look like:
                            # "At 2:00 PM, You: message"
                            # "At 1:58 PM, John Doe said: message"
                            # "You: message"
                            # "John Doe: message"
                            # Strip any leading timestamp prefix: "At HH:MM [AM/PM],"
                            label_clean = re.sub(r"^At\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?,?\s*", "", aria_label, flags=re.IGNORECASE).strip()
                            
                            m_said = re.search(r"^(.*?)\s+said", label_clean, re.IGNORECASE)
                            m_sent = re.search(r"^(.*?)\s+sent", label_clean, re.IGNORECASE)
                            
                            if m_said:
                                sender = m_said.group(1).strip()
                            elif m_sent:
                                sender = m_sent.group(1).strip()
                            elif ":" in label_clean:
                                sender = label_clean.split(":", 1)[0].strip()
                            else:
                                sender = label_clean.strip()

                        if not sender or sender.lower() == "unknown" or re.match(r"^At\s+\d{1,2}:", sender, re.IGNORECASE):
                            sender = self.current_inbox or self.target_chat or "Sender"

                        # Ignore any message signed by LOGIC
                        lower_text = text_content.lower()
                        if "apurbo's ai companion" in lower_text or "-logic" in lower_text or "- logic" in lower_text:
                            continue

                        msg_item = {
                            "timestamp": now_str,
                            "sender": sender,
                            "content": text_content,
                            "message_id": msg_id
                        }
                        self.pending_buffer.append(msg_item)

                        # Check if message contains LOGIC trigger
                        has_trigger = bool(re.search(r"\blogic\b", text_content, re.IGNORECASE) or re.search(r"\blogic\b", aria_label, re.IGNORECASE))
                        if not has_trigger:
                            continue

                        # When LOGIC is triggered, dump all pending buffered messages (including the trigger message) into msg_queue
                        self.msg_queue.extend(self.pending_buffer)
                        new_batch.extend(self.pending_buffer)
                        self.pending_buffer.clear()

                        logger.info(f"Trigger match found in message [{msg_id}]: {text_content}. Released {len(new_batch)} message(s).")
                        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        recent_context = []
                        for prev in msg_nodes[-8:]:
                            try:
                                t = prev.text.strip()
                                l = prev.get_attribute("aria-label") or ""
                                if t:
                                    recent_context.append(f"[{now_str}] {l}: {t}" if l else f"[{now_str}] {t}")
                            except Exception:
                                pass
                        
                        payload = {
                            "inbox": self.current_inbox or self.target_chat,
                            "message_id": msg_id,
                            "sender_label": aria_label,
                            "content": text_content,
                            "recent_chat": recent_context,
                            "timestamp": now_str
                        }
                        injection_queue.append(payload)
                    except Exception as e:
                        logger.error(f"Error parsing node: {e}")


                        
            except Exception as e:
                err_str = str(e).lower()
                is_disconnect = any(sig in err_str for sig in ("invalid session id", "disconnected", "chrome not reachable", "target window already closed", "session deleted"))
                if not is_disconnect:
                    logger.error(f"Error in monitor loop: {e}")
                else:
                    logger.warning("Brave browser appears closed or disconnected. Attempting to relaunch and recover session...")
                    try:
                        try:
                            self.driver.quit()
                        except Exception:
                            pass
                        time.sleep(2)
                        new_driver = start_driver()
                        if new_driver:
                            self.driver = new_driver
                            logger.info("Successfully reconnected to Brave. Re-navigating to target inbox...")
                            if self.target_chat:
                                navigate_to_chat(self.driver, self.target_chat)
                            self.drift_attempts = 0
                        else:
                            logger.error("Failed to re-establish driver connection after browser termination.")
                    except Exception as recovery_err:
                        logger.error(f"Error during browser relaunch recovery: {recovery_err}")

            self.stop_event.wait(5)

def start_monitor_service(inbox_name):
    global _active_bot
    if _active_bot and _active_bot.driver:
        logger.info(f"Active monitor found. Switching target to '{inbox_name}'...")
        _active_bot.connect_inbox(inbox_name)
        return _active_bot

    bot = MessengerBot(target_chat=inbox_name)
    if bot.connect_inbox(inbox_name):
        bot.start_monitoring()
        _active_bot = bot
        return bot
    return None

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "Apurbo Mostafiz"
    bot = start_monitor_service(target)
    if bot:
        try:
            while True:
                batch = bot.poll_new_messages(timeout=5)
                if batch:
                    print(batch, flush=True)
        except KeyboardInterrupt:
            bot.stop()
