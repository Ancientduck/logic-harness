
from pathlib import Path
import os
import sys
import subprocess
import psutil
import re
import time
from playwright.sync_api import sync_playwright


# Suppress Brave DevTools/TF noise
_stderr = sys.stderr
sys.stderr = open(os.devnull, "w")


def _find_chrome():
    import shutil as _sh

    for env_name in ('LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)'):
        base = os.environ.get(env_name, '')
        p = os.path.join(base, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe')
        if os.path.exists(p):
            return p

    w = _sh.which('brave.exe') or _sh.which('brave') or _sh.which('chrome')
    if w:
        return w

    return os.path.expandvars(r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe')

USER_DATA_DIR = (
    os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\LogicBrowserProfile")
)
EXECUTABLE_PATH = _find_chrome()
DEBUG_PORT = 9222
HEADLESS = True
MAX_MSGS = 10

DATA_DIR = str(
    Path(__file__).resolve().parent.parent
    / "logic_tools" / "messenger_data"
)
NICKNAMES_FILE = os.path.join(DATA_DIR, "messenger_nicknames.json")
INBOX_URLS_FILE = os.path.join(DATA_DIR, "messenger_inbox_urls.json")


def load_nicknames():
    try:
        if os.path.exists(NICKNAMES_FILE):
            import json
            with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def resolve_name(input_name):
    nick_names = load_nicknames()
    low = input_name.strip().lower()
    aliases = [low]
    search = input_name.strip()

    for nick, real in nick_names.items():
        if low in nick.lower() or nick.lower() in low:
            aliases += [nick.lower(), real.lower()]
            return real, aliases

        if low in real.lower() or real.lower() in low:
            aliases += [nick.lower(), real.lower()]
            return real, aliases

    return search, aliases


def _normalise_path(path):
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _uses_automation_profile(proc, profile_dir):
    """Check whether this Brave process belongs to our profile."""
    try:
        if proc.info.get("name", "").lower() != "brave.exe":
            return False

        cmdline = proc.info.get("cmdline") or []
        expected = _normalise_path(profile_dir)

        for arg in cmdline:
            if arg.lower().startswith("--user-data-dir="):
                actual = arg.split("=", 1)[1].strip('"')
                if _normalise_path(actual) == expected:
                    return True

    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    return False


def is_chrome_running():
    """Only detect Brave using the configured LogicBrowserProfile."""
    for proc in psutil.process_iter(["name", "cmdline"]):
        if _uses_automation_profile(proc, USER_DATA_DIR):
            return True
    return False


def _cleanup_launched_chrome(chrome_process, profile_dir):
    """
    Terminate only Brave processes belonging to the profile this script
    launched. Call this ONLY when launched_chrome and HEADLESS are True.
    """
    targets = {}

    # First, collect the original process tree while it still exists.
    if chrome_process is not None:
        try:
            root = psutil.Process(chrome_process.pid)
            for child in root.children(recursive=True):
                targets[child.pid] = child
            targets[root.pid] = root
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Also find profile-specific Brave processes that may have detached
    # from the original process tree.
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if _uses_automation_profile(proc, profile_dir):
            targets[proc.pid] = proc

    processes = list(targets.values())

    # Request graceful termination first.
    for proc in processes:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # Wait briefly, then force-close only processes still running.
    _, alive = psutil.wait_procs(processes, timeout=3)

    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if alive:
        psutil.wait_procs(alive, timeout=2)


def poll_until(fn, timeout=15, interval=0.3):
    deadline = time.time() + timeout

    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)

    return fn()


def wait_rows_stable(page, timeout=10):
    prev = 0
    stable = 0
    deadline = time.time() + timeout

    while time.time() < deadline:
        cur = len(page.query_selector_all("[role='row']"))

        if cur > 0 and cur == prev:
            stable += 1
            if stable >= 3:
                return cur
        else:
            stable = 0
            prev = cur

        time.sleep(0.3)

    return prev


def get_unread_convos(page):
    rows = page.query_selector_all("[role='row']")
    unread = []

    for row in rows:
        txt = row.inner_text()

        if "Unread message" not in txt:
            continue

        name = None

        for btn in row.query_selector_all("[role='button']"):
            label = btn.get_attribute("aria-label") or ""
            m = re.match(r"^More options for (.+)$", label)

            if m:
                name = m.group(1).strip()
                break

        if not name:
            lines = [
                line.strip()
                for line in txt.split("\n")
                if line.strip()
            ]

            for line in lines:
                if line not in ("Active now", "Unread message:"):
                    name = line
                    break

        if name:
            unread.append(name)

    return unread


def find_and_click(page, target, aliases=None):
    check = [target.lower()]

    if aliases:
        check = aliases

    rows = page.query_selector_all("[role='row']")
    best = None
    best_name = None

    for row in rows:
        for btn in row.query_selector_all("[role='button']"):
            label = btn.get_attribute("aria-label") or ""
            m = re.match(r"^More options for (.+)$", label)

            if not m:
                continue

            row_name = m.group(1).strip()
            row_lower = row_name.lower()

            if row_lower in check:
                best = row
                best_name = row_name
                break

            for alias in check:
                if alias in row_lower or row_lower in alias:
                    best = row
                    best_name = row_name
                    break

            if best:
                break

        if best:
            break

    if best:
        link = best.query_selector("a")

        if link:
            link.click()
        else:
            best.click()

        return best_name

    return None


def wait_conversation_loaded(page, convo_name):
    def has_msg():
        main = page.query_selector("div[role='main']")

        if not main:
            return False

        return len(
            main.query_selector_all("[aria-label*='Message sent']")
        ) >= 1

    poll_until(has_msg, timeout=8, interval=0.15)

    try:
        spinner = page.locator(
            "div[role='main'] [data-visualcompletion='loading-state']"
        )

        if spinner.count() > 0:
            spinner.first.wait_for(state="detached", timeout=5000)
    except Exception:
        pass

    prev = 0

    for _ in range(4):
        main = page.query_selector("div[role='main']")
        cur = (
            len(main.query_selector_all("[aria-label*='Message sent']"))
            if main else 0
        )

        if cur > 0 and cur == prev:
            break

        prev = cur
        time.sleep(0.15)

    return True


def extract_messages(page, include_own=False):
    main = page.query_selector("div[role='main']")

    if not main:
        return []

    msg_els = main.query_selector_all("[aria-label*='Message sent']")

    if not msg_els:
        return []

    pattern = re.compile(
        r"Enter, Message sent .+? by (.+?): (.+)",
        re.DOTALL
    )

    parsed = []

    for el in msg_els:
        aria = el.get_attribute("aria-label") or ""
        m = pattern.match(aria)

        if m:
            sender = m.group(1).strip()
            text = m.group(2).strip()
            parsed.append((sender, text))

    own_names = {"you", "apurbo", "apurbo mostafiz"}
    result = []

    for sender, text in reversed(parsed):
        if not include_own and sender.lower() in own_names:
            break

        result.append((sender, text))

        if len(result) >= MAX_MSGS:
            break

    result.reverse()
    return result


def get_convo_header_name(page):
    """Read the actual conversation name from the main panel header."""

    def _get():
        selectors = [
            "div[role='main'] h2",
            "div[role='main'] [data-testid='conversation-header'] span",
            "div[role='main'] a[role='link'] span",
            "div[role='banner'] h2",
            "div[role='banner'] a span",
        ]

        for sel in selectors:
            el = page.query_selector(sel)

            if el:
                txt = el.inner_text().strip()

                if txt and txt != "Unread message":
                    return txt

        return None

    return poll_until(_get, timeout=5, interval=0.2)


def main(target_name=None):
    launched_chrome = False
    chrome_process = None
    browser = None

    try:
        with sync_playwright() as p:
            # Only launch if our LogicBrowserProfile isn't already running.
            launched_chrome = not is_chrome_running()

            if launched_chrome:
                chrome_args = [
                    EXECUTABLE_PATH,
                    f"--user-data-dir={USER_DATA_DIR}",
                    f"--remote-debugging-port={DEBUG_PORT}",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]

                if HEADLESS:
                    chrome_args.append("--headless=new")

                chrome_args.append("https://www.messenger.com/")

                chrome_process = subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                # Wait for Brave's debugging endpoint to become available.
                deadline = time.time() + 15

                while time.time() < deadline:
                    if chrome_process.poll() is not None:
                        raise RuntimeError(
                            "Brave exited before it became available."
                        )

                    try:
                        browser = p.chromium.connect_over_cdp(
                            f"http://localhost:{DEBUG_PORT}",
                            timeout=1000,
                        )
                        break
                    except Exception:
                        time.sleep(0.25)

                if browser is None:
                    raise RuntimeError(
                        "Could not connect to the Brave debugging port."
                    )
            else:
                # Connect to existing Brave. Never close it.
                browser = p.chromium.connect_over_cdp(
                    f"http://localhost:{DEBUG_PORT}"
                )

            context = (
                browser.contexts[0]
                if browser.contexts
                else browser.new_context()
            )

            # Find existing Messenger tab or open a new one.
            page = None

            for p_tab in context.pages:
                if "messenger.com" in p_tab.url:
                    page = p_tab
                    break

            if not page:
                page = context.new_page()
                page.goto(
                    "https://www.messenger.com/",
                    wait_until="domcontentloaded",
                )

            wait_rows_stable(page)

            results = {}

            if target_name:
                # Direct mode: open a specific conversation.
                _, aliases = resolve_name(target_name)
                matched = find_and_click(page, target_name, aliases)

                if not matched:
                    print(f"Could not find: {target_name}")
                    return

                wait_conversation_loaded(page, matched)
                header = get_convo_header_name(page) or matched
                msgs = extract_messages(page, include_own=True)

                if msgs:
                    results[header] = msgs

            else:
                # Default mode: scan all unread conversations.
                unread = get_unread_convos(page)

                if not unread:
                    print("No unread messages.")
                    return

                print(
                    f"Found {len(unread)} unread: "
                    f"{', '.join(unread)}\n"
                )

                for target in unread:
                    if not find_and_click(page, target):
                        print(f"Could not find: {target}")
                        continue

                    wait_conversation_loaded(page, target)
                    header = get_convo_header_name(page) or target
                    msgs = extract_messages(page)

                    if msgs:
                        results[header] = msgs

            if not results:
                print("No unread messages found.")
            else:
                total = 0

                for name, msgs in results.items():
                    print(f"{name}:")

                    for sender, message in msgs:
                        print(f"  {sender}: {message}")

                    total += len(msgs)

                print(
                    f"\nTotal: {total} message(s) "
                    f"in {len(results)} conversation(s)"
                )

    finally:
        # The ONLY condition that permits Brave cleanup:
        # this invocation launched it AND headless mode is enabled.
        #
        # This finally block runs even when main() returns early
        # or raises an exception.
        if launched_chrome and HEADLESS:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

            try:
                _cleanup_launched_chrome(
                    chrome_process,
                    USER_DATA_DIR,
                )
            except Exception as exc:
                print(
                    f"Warning: Brave cleanup encountered an error: {exc}",
                    file=sys.__stderr__,
                )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    name = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    main(target_name=name)
