# Skill: Live Browser LLM Interaction via CDP

## Metadata
- ID: skill_cdp_web_llm_bridge
- Version: 2.0.0
- Trigger: Prompt a web LLM (ChatGPT, Claude, etc.) in an existing Brave session and extract the streamed reply.
- Non-Goals: Mass scraping, persistent state, headless auth, logged-in multi-account flows.

## 1. Prerequisites
- Brave running with CDP on port 9222, OR spawn it:
  C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=<LogicBrowserProfile>
  Note: x86 path. --remote-allow-origins=* is mandatory (Brave 149+ rejects WS without it).
- Target tab open to the LLM domain.
- websocket-client and urllib available. No Selenium.

## 2. CDP Connection (Critical)
    targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5).read())
    tab = next(t for t in targets if "chatgpt.com" in t.get("url",""))
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=120)

Rules:
- Do NOT call ws.recv() immediately after connecting with nothing in flight. Send a command first, then loop on recv() filtering by id.
- Use an incremental id counter for request/response matching.

## 3. Text Injection

### Logged-Out (ChatGPT landing page)
Uses <textarea>. Use native value setter to bypass React synthetic events:
    ta = document.querySelector('textarea')
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
    set.call(ta, prompt_text)
    ta.dispatchEvent(new Event('input', {bubbles: true}))
Then submit with:
    ta.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', which:13, keyCode:13, bubbles:true}))

### Logged-In
Uses [contenteditable="true"]. Use execCommand('insertText', false, text).
Submit: click button[data-testid="send-button"].

Never use send_keys for large text. Truncation + UI stalls.

## 4. Submit and Wait
- After Enter, page transitions landing -> conversation view in ~3-5s.
- sleep(4) before starting poll. Polling before transition = reading the landing page = false positive.

## 5. Response Polling (Content-Based)

data-testid="stop-button" does NOT exist in logged-out mode. Do not use it as primary signal.

Primary signal: response text length stabilizes (4 consecutive reads same length, length > 100).

Selector priority (logged-out):
  1. [data-message-author-role="assistant"] - cleanest if present
  2. document.body.innerText.slice(-2000) - catches "ChatGPT said:" block
  3. [class*="markdown"] last match

Selector priority (logged-in):
  1. [data-message-author-role="assistant"]
  2. stop-button absent + input re-enabled = done

Poll loop:
    start = time.time()
    prev = 0; stable = 0
    while time.time() - start < 90:
        text = ev(READ_JS) or ""
        cur = len(text)
        if cur > prev: prev = cur; stable = 0
        else: stable += 1
        if stable >= 4 and cur > 100: break
        time.sleep(1.5)

## 6. Extraction and Output
- Logged-out: reply is embedded in body.innerText after the literal string "ChatGPT said:". Parse from there.
- Logged-in: [data-message-author-role="assistant"] innerText.
- Log to logic_hall_results/ or return to caller.
- Untrusted output. Validate before operational use.

## 7. Known Pitfalls
| Pitfall | Fix |
|---|---|
| ws.recv() timeout on connect | Send command first, then loop |
| --remote-allow-origins missing | 403 on WS handshake; add the flag |
| Brave path wrong | Use C:\Program Files (x86)\ on this machine |
| Polling before page transition | sleep(4) after Enter |
| Stale element refs during stream | Re-query DOM every poll, never cache handles |
| send_keys truncation | Use native value setter + input event |
| Logged-out vs logged-in selectors | Check textarea vs [contenteditable] first |
| body.innerText includes nav/sidebar | Slice from "ChatGPT said:" marker or use role selectors |


