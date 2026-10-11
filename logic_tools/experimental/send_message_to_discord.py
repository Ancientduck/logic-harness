import os
import sys
import time
import subprocess
from playwright.sync_api import sync_playwright

CHROME_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
PROFILE_PATH = ros.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\LogicBrowserProfile")
PORT = 9222

def ensure_chrome():
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1.5)
        return True
    except Exception:
        subprocess.Popen([
            CHROME_PATH,
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE_PATH}",
            "--no-first-run",
            "--no-default-browser-check"
        ])
        time.sleep(2)
        return True

def send_discord_message(target=None, message="", files=None):
    if files is None:
        files = []
    if isinstance(files, str):
        files = [files]

    ensure_chrome()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # Find or open discord tab
        discord_page = None
        for page in context.pages:
            if "discord.com" in page.url:
                discord_page = page
                break

        if not discord_page:
            discord_page = context.new_page()
            discord_page.goto("https://discord.com/channels/@me", wait_until="domcontentloaded")
            time.sleep(3)

        discord_page.bring_to_front()
        time.sleep(1)

        # Check if already on the requested target
        current_title = discord_page.title()
        need_switch = True
        if target:
            clean_target = target.strip().lower()
            if clean_target in current_title.lower():
                need_switch = False
        else:
            need_switch = False

        if need_switch and target:
            # Open quick switcher
            discord_page.keyboard.press("Control+k")
            time.sleep(0.5)

            switcher_input = discord_page.locator("input[aria-label='Quick switcher']")
            if switcher_input.count() > 0:
                switcher_input.fill(target)
                time.sleep(0.8)
                discord_page.keyboard.press("Enter")
                time.sleep(1.5)
            else:
                discord_page.keyboard.type(target, delay=50)
                time.sleep(0.8)
                discord_page.keyboard.press("Enter")
                time.sleep(1.5)

        # Handle file attachments
        if files:
            file_input = discord_page.locator("input[type='file']").first
            if file_input.count() > 0:
                abs_files = [os.path.abspath(f) for f in files if os.path.exists(f)]
                if abs_files:
                    file_input.set_input_files(abs_files)
                    time.sleep(1.5)

        # Target the active Slate chat editor
        editor = discord_page.locator("[role='textbox'][contenteditable='true']").first
        editor.wait_for(state="visible", timeout=5000)
        editor.click()
        time.sleep(0.3)

        if message:
            # Insert text line by line or directly
            lines = message.split("\n")
            for idx, line in enumerate(lines):
                if line:
                    discord_page.keyboard.insert_text(line)
                if idx < len(lines) - 1:
                    discord_page.keyboard.press("Shift+Enter")
            time.sleep(0.4)

        # Press Enter to send
        discord_page.keyboard.press("Enter")
        time.sleep(0.5)

        print(f"Dispatched message to Discord target: '{target or 'current'}' | Text: '{message}'")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    msg = sys.argv[2] if len(sys.argv) > 2 else "Greetings from LOGIC. I am officially operating this terminal."
    send_discord_message(target=target, message=msg)
