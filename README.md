# LOGIC - Personal AI Companion & Automation Subsystem

A high-performance, private, self-hosted AI companion and automation runtime designed for local system control, proactive monitoring, memory-augmented reasoning, and asynchronous multi-agent coordination.

---

## Architecture Overview

LOGIC acts as an autonomous operational shell connecting local/cloud LLMs with native OS controls, hardware telemetry, and messaging networks.

```
                  +-----------------------------------+
                  |            USER / VOICE           |
                  +-----------------+-----------------+
                                    |
                                    v
                          +-------------------+
                          |     LOGIC Core    | <---> Activity Monitor
                          |    (logic.py)     | <---> SQLite+FTS5 Memory
                          +---------+---------+
                                    |
          +-------------------------+-------------------------+
          |                         |                         |
          v                         v                         v
+-------------------+     +-------------------+     +-------------------+
|  OmniRoute Gateway|     |   Multi-Agent     |     |   Tool Engine     |
| (Multi-LLM Stream)|     |   (Vinci / Sage)  |     | (logic_tools &    |
|                   |     |   via Logic Hall  |     |  logic_skills)    |
+-------------------+     +-------------------+     +-------------------+
```

---

## Key Subsystems

### 1. Unified Gateway & Model Routing
- **OmniRoute Lifecycle Management**: Auto-spawns and monitors local OmniRoute gateway instances (`localhost:20128`) on launch.
- **Streaming Response Pipeline**: Low-latency token-level streaming with automated reasoning effort controls.
- **Groq & Cloud Fallbacks**: Autonomous history compacting and context summarization agents with automated fallback routines.

### 2. Tiered Persistent Memory (`logic_memory_manager.py`)
- **SQLite + FTS5 Full-Text Search**: Sentence-level chunking with Porter stemming and Jaccard similarity scoring.
- **Passive & Active Recall**: Contextually injects high-importance memories while filtering ubiquitous tokens to eliminate false positives.
- **Dynamic Flag Parsing**: Granular memory control (`--type episodic|semantic|procedural|plan`, `--imp 0.0-1.0`, `--exp YYYY-MM-DD|never`).

### 3. Asynchronous Multi-Agent Delegation (`logic_hall.py`)
- **Worker Registry**: Dispatch parallel background subtasks to specialized peer agents (**Vinci** and **Sage**).
- **Non-blocking TUI**: Dedicated thread pools and message polling ensure user-facing responsiveness during execution.

### 4. Continuous System & Context Awareness
- **Activity Monitor (`activity_monitor.py`)**: Real-time foreground window tracking, process inspection, URL extraction, and duration monitoring.
- **Schedule Sync**: Automated Google Calendar integration tracking daily cycles, upcoming agendas, and past milestones.
- **Screen & Vision Analysis**: Visual inspection and reasoning via vision models (`check_screen`).

### 5. Multi-Modal Audio Pipeline
- **Real-Time TTS Streaming (`logic_voice.py`)**: Token-buffered sentence boundary detection using ONNX neural TTS models (`libritts_r`).
- **Voice Ingestion & Controls (`user_voice.py`, `clap_detect.py`)**: Audio cues, mic capture, and speech overlay HUD.

### 6. Extended Automation Toolset (`logic_tools/`)
- **Communications**: Automated Messenger inbox monitoring and Selenium/Playwright chat dispatchers.
- **Device Control**: ADB integration for Android notification extraction, SMS reading, and hardware button simulation.
- **Web & Extraction**: Autonomous site scraper (`scrape_site.py`), dynamic multi-file downloader (`download_file.py`), and YouTube transcript analysis (`yt_vid_transcript.py`).
- **Dynamic Skill Registry (`logic_skills/`)**: Folder-based declarative procedures (`skill.md`) loaded dynamically on demand.

---

## Directory Structure

```
D:/Ai/logic/
|-- logic.py                     # Main operational runtime & event loop
|-- logic_prompt.py              # System personality, constraints & tool specs
|-- logic_memory_manager.py      # SQLite+FTS5 tiered persistent memory engine
|-- logic_voice.py               # ONNX TTS synthesis & voice streaming pipeline
|-- user_voice.py                # Microphone audio ingestion & STT integration
|-- activity_monitor.py          # Foreground window & URL telemetry daemon
|-- tool_router.py               # Heuristic tool prediction engine
|-- validator.py                 # Generated Python code validation & sanitizer
|-- logic_hall.py                # Multi-agent worker orchestration (Vinci & Sage)
|-- gui_controller.py            # Windows UI element hook and automation
|-- logic_tools/                 # Python automation scripts & API clients
|   |-- google_calendar_drive_oauth.json        # Google Calendar / Drive credentials
|   |-- send_message_to_messenger.py
|   |-- scrape_site.py
|   `-- ...
|-- logic_skills/                # Task-specific procedural playbooks (Whitelisted: logic_code_modification, calendar_modification_tip, screenshot_window)
`-- memory/                      # Context summaries and SQLite database stores
```

---

## Terminal Commands & Shortcuts

| Command | Action |
|---------|--------|
| `/kill` | Instantly terminates the actively running generated Python script |
| `/v` | Toggle voice input mode on/off |
| `/compact` | Compacts conversational history via Groq/OmniRoute summarizer |
| `/save` | Forces an immediate conversation session summary save |
| `/hall` | Inspects status and unread buffers of Vinci and Sage |
| `/stop <vinci/sage/both` | Sends an emergency stop signal to background workers |
| `/chat off` | Disconnects the active Messenger chat monitoring loop |
| `/overlay` | Toggles the visual speech overlay HUD |
| `/debug` | Toggles prompt debugging output |
| `/model <name>` | Hot-swaps the underlying LLM provider/model |
| `/reload <module>` | Hot-reloads an imported Python module without restarting LOGIC |

---

## Security & Usage Notice
 To err is human; to hallucinate with absolute confidence is AI. Keep that in mind