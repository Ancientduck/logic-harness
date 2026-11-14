
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
    """Detect Brave using LogicBrowserProfile AND listening on debugging port."""
    import socket
    found_proc = False
    for proc in psutil.process_iter(["name", "cmdline"]):
        if _uses_automation_profile(proc, USER_DATA_DIR):
            found_proc = True
            break
    if not found_proc:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", DEBUG_PORT)) == 0


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
    # Single-pass in-browser extraction to eliminate Playwright roundtrips and DOM traversal overhead.
    # Returns messages with their aria timestamp, sender, and content.
    # For unread checks (include_own=False), it strictly isolates new messages and filters out old chats.
    extracted_raw = page.evaluate('''() => {
        const main = document.querySelector("div[role='main']");
        if (!main) return { dividerIndex: -1, items: [] };

        // Messenger puts an 'Unread messages' or 'New messages' divider in the thread
        const dividerEls = Array.from(main.querySelectorAll("span, div")).filter(el => {
            const txt = (el.textContent || "").trim().toLowerCase();
            return txt === "unread messages" || txt === "new messages";
        });
        const divider = dividerEls.length > 0 ? dividerEls[0] : null;

        const msgEls = main.querySelectorAll("[aria-label*='Message sent']");
        const items = [];
        let dividerPassed = !divider;

        for (const el of msgEls) {
            // Check if this message comes after the unread divider
            let isAfterDivider = true;
            if (divider) {
                const comp = divider.compareDocumentPosition(el);
                // comp & Node.DOCUMENT_POSITION_FOLLOWING (4) means el is after divider
                isAfterDivider = Boolean(comp & 4);
            }

            const aria = el.getAttribute("aria-label") || "";
            const container = el.closest("[role='article']") || el.closest("div[role='row']") || el.parentElement || el;

            const reelAnchors = Array.from(container.querySelectorAll("a[href]")).filter(a => {
                const href = a.getAttribute("href") || "";
                return href.includes("/share/r/") || href.includes("/share/v/") || href.includes("/reel/") || href.includes("/watch/") || href.includes("instagram.com/reel");
            });

            const reels = [];
            for (const a of reelAnchors) {
                let href = a.getAttribute("href") || "";
                try {
                    const u = new URL(href, window.location.origin);
                    href = u.origin + u.pathname;
                } catch(e) {}
                if (!reels.includes(href)) reels.push(href);
            }

            const images = container.querySelectorAll("img");
            let imgCount = 0;
            for (const img of images) {
                if (img.closest("a[href*='/share/r/'], a[href*='/share/v/'], a[href*='/reel/'], a[href*='/watch/'], a[href*='instagram.com/reel']")) continue;
                const src = img.getAttribute("src") || "";
                const alt = img.getAttribute("alt") || "";
                if (src.includes("/images/emoji.php") || alt.startsWith("Seen by")) continue;
                if (alt === "Open photo" || src.startsWith("data:image/") || (src.includes("scontent") && img.clientWidth > 50)) {
                    imgCount++;
                }
            }

            items.push({ aria, reels, imgCount, isAfterDivider });
        }
        return { hasDivider: Boolean(divider), items };
    }''')

    if not extracted_raw or not extracted_raw.get("items"):
        return []

    has_divider = extracted_raw.get("hasDivider", False)
    items = extracted_raw["items"]

    # Regex captures timestamp: "Enter, Message sent <time_str> by <sender>: <text>"
    pattern_with_text = re.compile(r"Enter, Message sent (.+?) by (.+?): (.+)", re.DOTALL)
    pattern_no_text = re.compile(r"Enter, Message sent (.+?) by (.+)", re.DOTALL)

    parsed = []
    for item in items:
        aria = item.get("aria", "")
        m = pattern_with_text.match(aria)
        time_str = ""
        if m:
            time_str = m.group(1).strip()
            sender = m.group(2).strip()
            text = m.group(3).strip()
        else:
            m_no = pattern_no_text.match(aria)
            if m_no:
                time_str = m_no.group(1).strip()
                sender = m_no.group(2).strip()
                text = ""
            else:
                continue

        media_tags = []
        for r_url in item.get("reels", []):
            media_tags.append(f"[REEL:{r_url}]")

        img_count = item.get("imgCount", 0)
        if img_count == 1:
            media_tags.append("[Image]")
        elif img_count > 1:
            media_tags.append(f"[{img_count} Images]")

        parts = media_tags + ([text] if text else [])
        body = " ".join(parts)

        if body:
            parsed.append({
                "sender": sender,
                "body": body,
                "time": time_str,
                "after_divider": item.get("isAfterDivider", True)
            })

    own_names = {"you"}

    # --- FILTERING LOGIC ---
    # When include_own=True (targeted inspect mode): keep recent batch up to MAX_MSGS.
    if include_own:
        result = [(p["sender"], p["body"]) for p in parsed[-MAX_MSGS:]]
        return result

    # When include_own=False (unread monitoring mode):
    # Rule 1: If Messenger showed an unread/new messages divider, strictly keep only messages after it.
    if has_divider:
        filtered = [p for p in parsed if p["after_divider"]]
        # If user replied after the divider, drop messages prior to user's reply
        last_own_idx = -1
        for idx, p in enumerate(filtered):
            if p["sender"].lower() in own_names:
                last_own_idx = idx
        if last_own_idx != -1:
            filtered = filtered[last_own_idx + 1:]
        return [(p["sender"], p["body"]) for p in filtered]

    # Rule 2: If no unread divider, find the last message sent by user.
    # Everything before (and including) user's last message is already seen/responded to.
    last_own_idx = -1
    for idx, p in enumerate(parsed):
        if p["sender"].lower() in own_names:
            last_own_idx = idx

    if last_own_idx != -1:
        # User spoke in this visible window: strictly take incoming messages sent AFTER user's last reply
        candidates = parsed[last_own_idx + 1:]
    else:
        # User didn't speak in this recent window: take messages from the latest contiguous incoming batch
        candidates = parsed[-MAX_MSGS:]

    # Rule 3: Date transition filter on candidates.
    # In Messenger aria labels:
    # Today's messages are just time: e.g. "10:14 PM", "2:05 AM"
    # Older messages contain day/date words: e.g. "Mon 8:30 PM", "Yesterday at 9:00 PM", "Nov 10 at 4:00 PM"
    # If the newest incoming message is today (time only), discard any preceding messages that have day/date markers.
    if candidates:
        latest_time = candidates[-1]["time"].lower()
        # Check if the latest message was sent today (does not contain weekday/date keywords)
        day_indicators = ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "yesterday", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        is_latest_today = not any(day in latest_time for day in day_indicators)

        if is_latest_today:
            # Drop older messages from prior days
            valid_candidates = []
            for p in candidates:
                p_time = p["time"].lower()
                if not any(day in p_time for day in day_indicators):
                    valid_candidates.append(p)
            candidates = valid_candidates

    return [(p["sender"], p["body"]) for p in candidates]



def get_convo_header_name(page):
    """Read the actual conversation name from the main panel header and resolve nickname if present."""

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

    raw_header = poll_until(_get, timeout=5, interval=0.2)
    if raw_header:
        real_name, _ = resolve_name(raw_header)
        return real_name
    return None


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
                    "--disable-gpu",
                    "--disable-extensions",
                    "--mute-audio",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--renderer-process-limit=2",
                    '--js-flags="--max-old-space-size=512"',
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

                # Suppress pre-scan announcement until conversations are validated

                for target in unread:
                    if not find_and_click(page, target):
                        print(f"Could not find: {target}")
                        continue

                    wait_conversation_loaded(page, target)
                    header = get_convo_header_name(page) or target
                    msgs = extract_messages(page)

                    if msgs:
                        # If the most recent message in the thread is from user/you, skip reporting this inbox
                        last_sender = msgs[-1][0].strip().lower()
                        own_senders = {"you"}
                        if last_sender in own_senders:
                            continue
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
                    f"\nAction required: Autonomously reply to messages not needing user's opinion via send_message_to_messenger.py,Do Not send automated messages to group chats;escalate the rest."
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
