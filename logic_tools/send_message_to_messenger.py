import os

import socket
import sys
import json
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException

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
LOCK_PORT = 9301
CHROMEDRIVER_PATH = r""
BRAVE_PATH = os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe")

SIGNATURE = "-LOGIC, Apurbo's AI companion"


# def _dbg(tag, **kw):
#     print(f"[DBG] {tag} | " + " ".join(f"{k}={v!r}" for k, v in kw.items()), flush=True)


# ============================================================
#  SINGLE-INSTANCE LOCK
# ============================================================
class SendLock:
    def __enter__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.bind(('127.0.0.1', LOCK_PORT))
        except OSError:
            print('[ERROR] Another send is already in progress. Try again in a moment.')
            sys.exit(1)
        return self

    def __exit__(self, *exc):
        self.sock.close()


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


def apply_perf_tuning(driver):
    try:
        driver.execute_cdp_cmd('Network.enable', {})
    except Exception:
        pass


def _common_perf_options(options):
    options.page_load_strategy = 'none'
    options.binary_location = BRAVE_PATH
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-features=Translate,MediaRouter,OptimizationHints,TranslateUI")
    return options


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
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-component-update")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--hide-crash-restore-bubble")
    options = _common_perf_options(options)
    driver = make_driver(options)
    apply_perf_tuning(driver)
    return driver


def attach_to_existing_chrome():
    options = Options()
    options.add_experimental_option("debuggerAddress", f"localhost:{DEBUG_PORT}")
    options = _common_perf_options(options)
    driver = make_driver(options)
    apply_perf_tuning(driver)
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
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if "messenger.com" in driver.current_url:
            return True
    return False


def close_chrome():
    if is_debug_chrome_running():
        try:
            attach_to_existing_chrome().quit()
            print('[OK] Automation Brave closed.')
        except Exception as e:
            print(f'[ERROR] Could not close Brave: {e}')
    else:
        print('[OK] Brave was not running.')


# ============================================================
#  WAITING
# ============================================================
def wait_for_messenger_ready(driver, timeout=30):
    wait = WebDriverWait(driver, timeout, poll_frequency=0.1,
                         ignored_exceptions=(StaleElementReferenceException,))
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "div[role='navigation']")))
    return wait


def wait_for_thread_url(driver, thread_id, timeout=6):
    """Wait until the browser is actually on the target thread."""
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05,
                      ignored_exceptions=(StaleElementReferenceException,)).until(
            lambda d: thread_id in d.current_url)
        return True
    except Exception:
        return False


def wait_for_composer_mounted(driver, timeout=6):
    """Wait until the composer element exists (any state)."""
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05,
                      ignored_exceptions=(StaleElementReferenceException,)).until(
            lambda d: get_composer_state(d)[0] != 'missing')
        return True
    except Exception:
        return False


# ============================================================
#  DATA PATHS
# ============================================================
DATA_DIR = Path(__file__).resolve().parent / "messenger_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
NICKNAMES_FILE = DATA_DIR / "messenger_nicknames.json"
INBOX_URLS_FILE = DATA_DIR / "messenger_inbox_urls.json"


def load_nicknames():
    if NICKNAMES_FILE.exists():
        try:
            with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


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


def save_nickname(nickname, real_name):
    if not nickname or not real_name:
        return
    key = nickname.strip().lower()
    val = real_name.strip()
    if not key or not val or key == val.lower():
        return
    try:
        nicks = load_nicknames()
        if nicks.get(key) != val:
            nicks[key] = val
            with open(NICKNAMES_FILE, "w", encoding="utf-8") as f:
                json.dump(nicks, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[WARN] Failed to save nickname: {e}")


def save_inbox_url(real_name, url):
    if not real_name or not url or "/t/" not in url:
        return
    key = real_name.strip().lower()
    if not key:
        return
    try:
        urls = load_inbox_urls()
        if urls.get(key) != url:
            urls[key] = url
            with open(INBOX_URLS_FILE, "w", encoding="utf-8") as f:
                json.dump(urls, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[WARN] Failed to save inbox URL: {e}")


# ============================================================
#  NAMING / MATCHING
# ============================================================
def resolve_identifiers(input_name):
    """
    Bidirectional nickname resolution.
    nick_map is {nickname_lower: real_name}.
    - If input matches a NICKNAME key -> real_search_name = real value (search by real name).
    - If input matches a REAL value -> real_search_name = nickname key (search/click by nickname).
    - aliases always carry BOTH forms for sidebar/row matching.
    """
    nick_map = load_nicknames()
    inp = input_name.strip()
    input_lower = inp.lower()
    aliases = [input_lower]
    real_search_name = inp
    mode = "none"

    # 1) input IS a nickname -> search using its real name
    if input_lower in nick_map:
        real_val = nick_map[input_lower]
        real_search_name = real_val
        aliases = list(dict.fromkeys([input_lower, real_val.lower()]))
        mode = "nick->real"
        #_dbg("RESOLVE_BIDIR", mode=mode, alias=aliases, search=real_search_name)
        return real_search_name, aliases

    # 2) input IS a real name -> search/click using its nickname key
    for nick, real in nick_map.items():
        if real.strip().lower() == input_lower:
            real_search_name = nick
            aliases = list(dict.fromkeys([input_lower, nick.lower()]))
            mode = "real->nick"
            #_dbg("RESOLVE_BIDIR", mode=mode, alias=aliases, search=real_search_name)
            return real_search_name, aliases

    # 3) fuzzy fallback in both directions
    for nick, real in nick_map.items():
        if input_lower in nick.lower() or nick.lower() in input_lower:
            real_search_name = real
            aliases = list(dict.fromkeys([input_lower, nick.lower(), real.lower()]))
            #_dbg("RESOLVE_FUZZY", mode="nick~", alias=aliases, search=real_search_name)
            return real_search_name, aliases
    for nick, real in nick_map.items():
        if input_lower in real.lower() or real.lower() in input_lower:
            real_search_name = nick
            aliases = list(dict.fromkeys([input_lower, nick.lower(), real.lower()]))
            #_dbg("RESOLVE_FUZZY", mode="real~", alias=aliases, search=real_search_name)
            return real_search_name, aliases

    #_dbg("RESOLVE_NONE", input=inp)
    return real_search_name, aliases


def get_conversation_header_text(driver):
    for sel in ("div[role='main'] h2",
                "div[role='main'] [role='heading']",
                "div[aria-label='Thread header']",
                "h2"):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                t = el.text.strip()
                if t and t.lower() not in ("chats", "active now", "contacts",
                                           "search results", "marketplace"):
                    return t
        except Exception:
            continue
    return ""


def clean_display_name(text):
    if not text:
        return ""
    return re.sub(r"^(conversation with|conversation titled|chat with)\s*",
                  "", text.strip(), flags=re.I).strip()


def get_composer_state(driver):
    """Returns (state, name):
        ('named', 'Real Name')  composer has 'Write to <Name>'
        ('self', '')            composer has bare 'Write to ' (self-chat)
        ('missing', '')         composer element not present yet
    """
    try:
        result = driver.execute_script("""
            let all = document.querySelectorAll('[aria-label]');
            for (let i = 0; i < all.length; i++) {
                let a = all[i].getAttribute('aria-label');
                if (a && a.startsWith('Write to')) return a;
            }
            return null;
        """)
        if result is None:
            return ('missing', '')
        stripped = re.sub(r"^Write to\s*", "", result, flags=re.I).strip()
        if not stripped or stripped.lower() in ("actions", "message", "send"):
            return ('self', '')
        return ('named', stripped)
    except Exception:
        return ('missing', '')


def get_composer_recipient_name(driver):
    state, name = get_composer_state(driver)
    return name if state == 'named' else ""


def get_window_title_name(driver):
    try:
        raw_title = driver.title or ""
        clean_title = re.sub(r"^\(\d+\)\s*", "", raw_title)
        clean_title = re.sub(r"\s*\|\s*Messenger.*$", "", clean_title, flags=re.I).strip()
        if clean_title.lower() not in ("", "messenger", "chats"):
            return clean_title
    except Exception:
        pass
    return ""


def _matches_any(name, aliases, real_search_name):
    if not name:
        return False
    n = name.lower()
    if any(a in n or n in a for a in aliases):
        return True
    if real_search_name and (real_search_name.lower() in n or n in real_search_name.lower()):
        return True
    return False


def _is_distinct_alias(alias, recipient_name):
    if not alias or not recipient_name:
        return False
    a = alias.strip().lower()
    r = recipient_name.strip().lower()
    if not a or not r or a == r:
        return False
    if a in r or r in a:
        return False
    return True


def get_current_recipient(driver, aliases, real_search_name, prev_composer="", known_label="", timeout=6):
    """Full verification flow. Polls for:
      - 'named' composer matching aliases, OR
      - 'named' composer changed from prev_composer, OR
      - 'self' composer (self-chat) with title matching aliases.
    """
    def _poll(d):
        state, name = get_composer_state(d)

        if state == 'named':
            if _matches_any(name, aliases, real_search_name):
                return ('composer', name)
            if name.lower() != (prev_composer or "").lower():
                return ('composer', name)
            return None

        if state == 'self':
            title = get_window_title_name(d)
            if title and _matches_any(title, aliases, real_search_name):
                return ('self', title)
            return None

        return None

    try:
        src, name = WebDriverWait(driver, timeout, poll_frequency=0.05,
                                  ignored_exceptions=(StaleElementReferenceException,)).until(_poll)
        if src == 'self':
            return name, 'title'
        return name, src
    except Exception:
        pass

    title = get_window_title_name(driver)
    if title and _matches_any(title, aliases, real_search_name):
        return title, 'title'

    if known_label:
        return known_label, 'label'
    return '', 'none'


def check_current_thread(driver, aliases, real_search_name):
    state, name = get_composer_state(driver)
    if state == 'named' and _matches_any(name, aliases, real_search_name):
        return name
    title = get_window_title_name(driver)
    if title and _matches_any(title, aliases, real_search_name):
        return title
    return ""


def learn_and_cache(driver, aliases, recipient_name):
    if not recipient_name:
        return

    for alias in aliases:
        if _is_distinct_alias(alias, recipient_name):
            save_nickname(alias, recipient_name)

    header_clean = clean_display_name(get_conversation_header_text(driver))
    if _is_distinct_alias(header_clean, recipient_name):
        save_nickname(header_clean, recipient_name)

    try:
        url = driver.current_url
        if "/t/" in url:
            save_inbox_url(recipient_name, url)
    except Exception:
        pass


def extract_chat_title_from_row(row):
    try:
        labels = []
        aria = row.get_attribute("aria-label")
        if aria:
            labels.append(aria)
        for a in row.find_elements(By.CSS_SELECTOR, "a[role='link'], a[href*='/t/']"):
            lbl = a.get_attribute("aria-label")
            if lbl:
                labels.append(lbl)
        for lbl in labels:
            clean = clean_display_name(lbl)
            clean = re.sub(r",\s*\d+\s*unread.*$", "", clean, flags=re.I).strip()
            if clean:
                return clean.lower()
    except Exception:
        pass
    try:
        raw = row.get_attribute("innerText") or row.text or ""
        for line in (l.strip() for l in raw.split("\n") if l.strip()):
            if line.lower() in ("active now", "chats") or re.match(r"^\d+[\s:]*\w*$", line):
                continue
            return line.lower()
    except Exception:
        pass
    return ""


def try_select_by_target_url(driver, target_url):
    if not target_url or "/t/" not in target_url:
        return False
    thread_id = target_url.rstrip("/").split("/")[-1]
    if not thread_id:
        return False
    if thread_id in driver.current_url:
        return True
    try:
        links = driver.find_elements(By.CSS_SELECTOR, f"a[href*='{thread_id}']")
        for link in links:
            if link.is_displayed():
                driver.execute_script("arguments[0].click();", link)
                return True
        if links:
            driver.execute_script("arguments[0].click();", links[0])
            return True
    except Exception:
        pass
    return False


def try_select_from_sidebar(driver, aliases):
    wait = WebDriverWait(driver, 5, poll_frequency=0.1,
                         ignored_exceptions=(StaleElementReferenceException,))

    def get_rows(d):
        for sel in ("div[role='grid'] div[role='row']",
                    "div[role='navigation'] div[role='row']",
                    "div[aria-label='Chats'] [role='row']",
                    "div[aria-label='Chats'] a[href*='/t/']",
                    "div[role='navigation'] a[href*='/t/']"):
            found = d.find_elements(By.CSS_SELECTOR, sel)
            if found and any(r.text.strip() or r.get_attribute("aria-label") for r in found[:5]):
                return found
        return []

    try:
        rows = wait.until(lambda d: get_rows(d) or False)
    except Exception:
        return ""

    for row in rows:
        try:
            title = extract_chat_title_from_row(row)
            if not title:
                continue
            if any(a in title or title in a for a in aliases):
                target = row
                sub = row.find_elements(
                    By.CSS_SELECTOR, "a[href*='/t/'], a[role='link'], div[role='button']")
                if sub:
                    target = sub[0]
                driver.execute_script("arguments[0].click();", target)
                return title
        except Exception:
            continue
    return ""


# ============================================================
#  TYPING
# ============================================================
def type_message(driver, textbox, text):
    from selenium.webdriver.common.action_chains import ActionChains
    try:
        driver.execute_script("arguments[0].focus();", textbox)
    except Exception:
        pass

    lines = text.split(chr(10))
    actions = ActionChains(driver)
    actions.click(textbox)
    #*here
    for i, line in enumerate(lines):
        if line:
            actions.send_keys(line)
        if i < len(lines) - 1:
            actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT)

    actions.send_keys(Keys.ENTER)
    actions.perform()


# ============================================================
#  CORE SEND
# ============================================================
def send_message(name='', msg='', file_path=''):
    t0 = time.time()
    #_dbg("ENTER", name=name, msg=msg, file=file_path)
    real_search_name, aliases = resolve_identifiers(name)
    #_dbg("RESOLVED", real=real_search_name, aliases=aliases)
    driver = get_driver()
    #_dbg("DRIVER_OK", url=driver.current_url)

    if not find_messenger_tab(driver):
        #_dbg("NO_TAB", handles=len(driver.window_handles), urls=[h for h in driver.window_handles])
        driver.get("https://www.messenger.com")
        #_dbg("NAVIGATED", url=driver.current_url)

    try:
        wait = wait_for_messenger_ready(driver)
    except Exception as e:
        print(e)
        #_dbg("READY_FAIL", err=repr(e)); return
    #_dbg("READY_OK")

    matched = check_current_thread(driver, aliases, real_search_name)

    cached_urls = load_inbox_urls()
    target_url = cached_urls.get(real_search_name.lower())
    if not target_url:
        for alias in aliases:
            if alias in cached_urls:
                target_url = cached_urls[alias]
                break
    #_dbg("CACHE", matched=matched, target_url=target_url)

    # --- attempt 0: cached URL → TRUSTED fast path (no verification) ---
    if not matched and target_url and "/t/" in target_url:
        thread_id = target_url.rstrip("/").split("/")[-1]
        clicked = False
        if thread_id in driver.current_url:
            clicked = True
        elif try_select_by_target_url(driver, target_url):
            clicked = True

        if clicked:
            url_ok = wait_for_thread_url(driver, thread_id, timeout=6)
            composer_ok = wait_for_composer_mounted(driver, timeout=6) if url_ok else False
            #_dbg("ATT0", thread_id=thread_id, clicked=clicked, url_ok=url_ok, composer_ok=composer_ok)
            if url_ok and composer_ok:
                matched = real_search_name

    # --- attempt 1: sidebar text match ---
    if not matched:
        prev_composer = get_composer_recipient_name(driver)
        label = try_select_from_sidebar(driver, aliases)
        #_dbg("ATT1", label=label, prev_composer=prev_composer)
        if label:
            matched, _ = get_current_recipient(
                driver, aliases, real_search_name,
                prev_composer=prev_composer, known_label=label, timeout=6)
            if matched:
                learn_and_cache(driver, aliases, matched)

    # --- attempt 2: cold full reload ---
    if (not matched and target_url and "/t/" in target_url
            and driver.current_url.rstrip("/") != target_url.rstrip("/")):
        #_dbg("ATT2", target_url=target_url)
        driver.get(target_url)
        wait = wait_for_messenger_ready(driver)
        matched, _ = get_current_recipient(driver, aliases, real_search_name, timeout=8)
        #_dbg("ATT2_RESULT", matched=matched)
        if matched:
            learn_and_cache(driver, aliases, matched)

    # --- attempt 3: search bar ---
    if not matched:
        try:
            search = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR,
                 "input[placeholder='Search Messenger'], input[aria-label='Search Messenger']")))
            search.click()
            search.send_keys(Keys.CONTROL, "a")
            search.send_keys(Keys.BACKSPACE)
            search.send_keys(real_search_name)
            #_dbg("ATT3_SEARCH_TYPED", q=real_search_name)

            wait_search = WebDriverWait(driver, 8, poll_frequency=0.05,
                                        ignored_exceptions=(StaleElementReferenceException,))
            contact = wait_search.until(EC.element_to_be_clickable(
                (By.XPATH,
                 "//li[@role='option' and not(contains(@aria-label, 'Search messages'))]")))

            label = contact.get_attribute("aria-label") or contact.text
            #_dbg("ATT3_CONTACT", label=label)
            clean = clean_display_name(label).lower()
            if clean and clean not in aliases:
                aliases.append(clean)

            prev_composer = get_composer_recipient_name(driver)
            try:
                contact.click()
            except Exception:
                driver.execute_script("arguments[0].click();", contact)

            matched, _ = get_current_recipient(
                driver, aliases, real_search_name,
                prev_composer=prev_composer, known_label=clean, timeout=8)
            #_dbg("ATT3_RESULT", matched=matched)
            if matched:
                learn_and_cache(driver, aliases, matched)
        except Exception as e:
            print(f'[ERROR] Search failed: {e}')
            return

    if not matched:
        print('[ERROR] Could not open the intended thread. Message NOT sent.')
        return

    # --- type & send ---
    #_dbg("TEXTBOX_SEARCH", matched=matched)
    textbox = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']")))
    #_dbg("TEXTBOX_OK")

    if file_path:
        files = [file_path] if isinstance(file_path, str) else file_path
        for f in files:
            file_box = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//input[@type='file']")))
            file_box.send_keys(str(Path(f).resolve()))
            try:
                WebDriverWait(driver, 10, poll_frequency=0.1).until(
                    lambda d: d.find_elements(
                        By.CSS_SELECTOR,
                        "[role='textbox'] ~ * [role='button'][aria-label*='Remove'], "
                        "div[aria-label*='attachment' i], div[aria-label*='Remove' i]"))
            except Exception:
                pass

    msg = msg.replace("\\n", "\n")
    if SIGNATURE not in msg:
        msg = f"{msg}\n{SIGNATURE}" if msg else f"files sent by {SIGNATURE}"

    #_dbg("TYPING", final_msg=msg)
    type_message(driver, textbox, msg)
    #_dbg("TYPED", elapsed=round(time.time()-t0, 2))

    print(f'[SUCCESS] Message sent to {matched} in {time.time() - t0:.1f}s')
    print("==========================================\n")


def send_messages(the_list):
    for name, msg, *rest in the_list:
        file_path = rest[0] if rest else ''
        print(f"\n[QUEUE] Target: {name} | Msg: {msg} | File: {file_path or 'none'}")
        send_message(name, msg, file_path)


# ============================================================
#  CLI
# ============================================================
if __name__ == '__main__':
    if '--close' in sys.argv:
        close_chrome()
        sys.exit(0)

    with SendLock():
        if len(sys.argv) > 2:
            send_messages([[sys.argv[1], " ".join(sys.argv[2:])]])
        elif len(sys.argv) > 1:
            try:
                the_list = json.loads(sys.argv[1])
                if not isinstance(the_list[0], list):
                    the_list = [the_list]
                send_messages(the_list)
            except Exception as e:
                print(f'[ERROR] Bad argument: {e}')
        else:
            send_messages([
                ['২ নম্বার খাঁটি মাল (•̀ᴗ•́)', 'ho, fuck you'],
            ])
