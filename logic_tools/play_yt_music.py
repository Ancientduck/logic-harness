import time
import sys
import psutil
import json
import urllib.request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def is_chrome_debugging_running(port=9222):
    try:
        url = f"http://localhost:{port}/json/version"
        response = urllib.request.urlopen(url, timeout=5)
        data = json.loads(response.read().decode())
        return data.get("webSocketDebuggerUrl")
    except Exception:
        return None

def launch_fresh_chrome(port=9222):
    options = Options()
    options.add_argument(r"user-data-dir=C:\\Users\\USER\\AppData\\Local\\Google\\Brave\\LogicBrowserProfile")
    options.add_argument(f"--remote-debugging-port={port}")
    options.add_experimental_option("detach", True)
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Brave(options=options)
    print(f"Launched new Brave with debugging on port {port} and LogicBrowserProfile")
    return driver

def attach_to_existing_chrome(port=9222):
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    driver = webdriver.Brave(options=options)
    print(f"Attached to existing Brave debugging session on port {port}")
    return driver

def play_youtube_song(song_name):
    debugger_url = is_chrome_debugging_running(9222)

    if debugger_url:
        driver = attach_to_existing_chrome(9222)
    else:
        driver = launch_fresh_chrome(9222)

    try:
        wait = WebDriverWait(driver, 15)

        youtube_found = False
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if "youtube.com" in driver.current_url:
                youtube_found = True
                break

        if not youtube_found:
            driver.execute_script("window.open('https://www.youtube.com', '_blank');")
            driver.switch_to.window(driver.window_handles[-1])
            print("Opened YouTube in a new tab")
        else:
            print("Found existing YouTube tab")

        search_box = wait.until(EC.element_to_be_clickable((By.NAME, "search_query")))
        search_box.send_keys(song_name)
        search_box.send_keys(Keys.RETURN)

        video_element = wait.until(EC.element_to_be_clickable((By.XPATH, "(//a[@id='video-title'])[1]")))
        video_element.click()

        print(f"Playing: {song_name}")
    except Exception as e:
        print(f"Failed to play music: {e}")

if __name__ == '__main__':
    song = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "chill lofi music"
    play_youtube_song(song)