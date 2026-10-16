"""
LOGIC AI — Terminal UI (Carbon Edition, R3)
================================================================================
Run:
    python logic_tui.py

Set USE_REAL = True  to connect to live logic.py & logic_hall.py backend.
Set USE_REAL = False to run the self-contained simulation.

R3 overhaul — the TUI is now a pure presentation layer:

  * logic.py owns execution. Subprocess spawning, validate(), tool dispatch,
    memory, reminders, GUI control, agent dispatch — none of it is
    reimplemented here anymore.
  * Streaming is done through a single backend-provided hook:
    logic.py's `run_code_streamed` calls `self._line_sink(line)` for every
    line the child process emits. The TUI installs that sink and merges the
    lines into the token stream. When your logic.py does not call the sink,
    the TUI falls back to a stdout tee so live output still arrives.
  * `run_gen_code` and `run_scripts` are the BACKEND's — validate() runs on
    their output, exactly like CLI mode. The TUI only prepends the child-side
    tee shim to generated code so that `subprocess.run(capture_output=True)`
    calls inside generated scripts still stream live.
  * search_net / gen_code / check_screen get cosmetic "…working…" markers
    only; the backend does all the work.
  * Chat streams are painted as cheap plain text while tokens arrive; the
    expensive Markdown / Panel rebuild happens exactly ONCE on finalize.
  * Dirty-flag rendering, pinned auto-scroll, batched hall drain, 1 Hz
    top bar — all preserved from R2.
  * 17 runnable sandbox scenarios (/demo) in simulation mode.

R3.1 lag fixes:
  * Tighter stream/hall intervals + smaller retention caps
  * Final Group cached after finalize (no re-layout of finished bubbles)
  * Live path is Text-only (no Panel/Markdown until done)
  * Fast-path skip of parse_stream for pure prose
  * Output panels use one Text instead of N styled lines
"""

import contextlib
import inspect
import io
import json
import os
import queue
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.worker import get_current_worker


# ══════════════════════════════════════════════════════════════════════════════
#  INPUT UPGRADES (auto-inserted by patch_tui.py)
#  Ctrl+Backspace deletes word-by-word · Ctrl+Enter inserts a newline
# ══════════════════════════════════════════════════════════════════════════════

from textual.message import Message  # noqa: E402
USE_REAL = True  

# When True, mount SCRIPT_RESULT / THREAD payloads as SYS so you can see
# exactly what Logic receives (search results, full script output, etc.).
# Live [OUTPUT] panels still show streamed lines either way.
SHOW_LOGIC_FEEDBACK = True


try:
    from textual.widgets.text_area import Selection  # noqa: F401,E402
except Exception:
    Selection = None


class ChatComposer(TextArea):
    """Multiline chat composer.

    Enter = send · Ctrl+Enter / Shift+Enter / Ctrl+J = newline
    Ctrl+Backspace / Ctrl+W / Ctrl+H = delete previous word
    """

    class Submitted(Message):
        """Posted when Enter is pressed (`event.value` holds the text)."""

        def __init__(self, composer: "ChatComposer", value: str) -> None:
            super().__init__()
            self.composer = composer
            self.value = value

        @property
        def input(self):
            # compat alias so old handler code using `event.input.*` keeps working
            return self.composer

    BINDINGS = [
        Binding("enter", "submit", "Send", show=False),
        Binding("ctrl+enter", "newline", "New line", show=False),
        Binding("shift+enter", "newline", show=False),
        Binding("ctrl+j", "newline", show=False),           # terminal fallback
        Binding("ctrl+backspace", "delete_word_left", show=False),
        Binding("alt+backspace", "delete_word_left", show=False),
        Binding("ctrl+w", "delete_word_left", show=False),  # terminal fallback
        Binding("ctrl+h", "delete_word_left", show=False),  # terminal fallback
        Binding("tab", "app.focus_next", show=False),
    ]

    MAX_VISIBLE_LINES = 8

    def __init__(self, *args, **kwargs) -> None:
        if args:  # Input-style positional placeholder
            self._placeholder = str(args[0])
            args = args[1:]
        else:
            self._placeholder = kwargs.pop("placeholder", "")
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(*args, **kwargs)

    # ── Input-compatible .value ────────────────────────────────────────────
    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new_value: str) -> None:
        try:
            self.text = new_value
        except Exception:
            self.load_text(new_value)

    def clear(self) -> None:
        self.value = ""
        self._sync_height()

    def on_mount(self) -> None:
        try:
            super().on_mount()
        except Exception:
            pass
        try:
            self.placeholder = self._placeholder   # newer Textual only
        except Exception:
            pass
        self._sync_height()
        self.set_interval(0.12, self._sync_height)

    def _sync_height(self) -> None:
        lines = max(1, self.text.count("\n") + 1)
        self.styles.height = min(lines, self.MAX_VISIBLE_LINES)

    # ── actions ────────────────────────────────────────────────────────────
    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self.post_message(self.Submitted(self, text))
        self.clear()

    def action_newline(self) -> None:
        self.insert("\n")
        self._sync_height()

    def action_delete_word_left(self) -> None:
        # If a range is selected, delete it wholesale first.
        try:
            if self.selected_text:
                sel = self.selection
                start, end = sorted((sel.anchor, sel.cursor))
                self.delete(start, end)
                try:
                    self.selection = Selection(self.cursor_location, self.cursor_location)
                except Exception:
                    pass
                return
        except Exception:
            pass

        row, col = self.cursor_location
        lines = self.text.split("\n")
        if col == 0:
            if row > 0:  # start of line → join with the line above
                self.delete((row - 1, len(lines[row - 1])), (row, 0))
            return
        line = lines[row] if row < len(lines) else ""
        start = col
        while start > 0 and not line[start - 1].isspace():
            start -= 1
        if start == col:  # caret sat right after whitespace: eat it + word
            while start > 0 and line[start - 1].isspace():
                start -= 1
            while start > 0 and not line[start - 1].isspace():
                start -= 1
        if start != col:
            self.delete((row, start), (row, col))
        self._sync_height()


class SmartInput(Input):
    """Single-line Input with Ctrl+Backspace word delete (for any other inputs)."""

    BINDINGS = [
        Binding("ctrl+backspace", "delete_word_left", show=False),
        Binding("ctrl+w", "delete_word_left", show=False),
        Binding("ctrl+h", "delete_word_left", show=False),
    ]

    def action_delete_word_left(self) -> None:
        value, pos = self.value, self.cursor_position
        if pos == 0:
            return
        start = pos
        while start > 0 and not value[start - 1].isspace():
            start -= 1
        if start == pos:
            while start > 0 and value[start - 1].isspace():
                start -= 1
            while start > 0 and not value[start - 1].isspace():
                start -= 1
        self.value = value[:start] + value[pos:]
        self.cursor_position = start
# ──────────────────────────── end INPUT UPGRADES ────────────────────────────



# ─── Configuration ────────────────────────────────────────────────────────────

# Set True to connect to logic.py backend; False for demo

TOOL_TAGS = ("tool", "tool_call", "function_call")
RESULT_TAGS = ("tool_result", "tool_output", "exec_output", "output", "result")

# Performance tuning — limits only affect *what the TUI draws*. Agents still
# receive full stdout through their own pipelines.
# R3.1: tighter caps + cached finals, but keep stream paint responsive.
HALL_DRAIN_INTERVAL = 0.15
HALL_MAX_EVENTS_PER_TICK = 150
HALL_REFRESH_MIN_INTERVAL = 0.12
CHAT_STREAM_MIN_INTERVAL = 0.07
MAX_STDOUT_KEEP = 4_000
MAX_STDOUT_RENDER = 120
MAX_RAW_STREAM_CHARS = 80_000
MAX_AGENT_BLOCKS_PER_COLUMN = 6
MAX_CHAT_OUTPUT_LINES = 150
MAX_CHAT_MESSAGES = 150
LIVE_STREAM_OUTPUT_LINES = 120
SPINNER_INTERVAL = 0.12
TOPBAR_INTERVAL = 1.0


# ══════════════════════════════════════════════════════════════════════════════
#  STREAM PARSER & FENCE BUFFER
# ══════════════════════════════════════════════════════════════════════════════

_TOOL_FULL_RE = re.compile(
    r"<(?P<tag>" + "|".join(TOOL_TAGS) + r")>(?P<body>.*?)</(?P=tag)>", re.S
)
_RES_FULL_RE = re.compile(
    r"<(?P<tag>" + "|".join(RESULT_TAGS) + r")>(?P<body>.*?)</(?P=tag)>", re.S
)
_OPEN_TAG_RE = re.compile(r"<(?P<tag>" + "|".join(TOOL_TAGS + RESULT_TAGS) + r")>")


def normalize_fences(text: str) -> str:
    text = re.sub(r"```python[ \t]+thread[ \t]*(?=\r?\n)", "```python", text)
    text = re.sub(r"```(?!\w+\r?\n|\r?\n|\s|$)", "```\n", text)
    text = re.sub(r"(?<=\S)```", "\n```", text)
    return text

def balance_markdown_fences(text: str) -> str:
    fence_count = len(re.findall(r"^```", text, re.MULTILINE))
    if fence_count % 2 != 0:
        return text + "\n```"
    return text


def parse_tool_json(raw: str):
    s = raw.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("tool") or "tool"
            args = obj.get("args")
            if args is None:
                args = obj.get("arguments")
            if args is None:
                args = {k: v for k, v in obj.items() if k not in ("name", "tool")}
            return str(name), args, ""
        return "tool", obj, ""
    except Exception:
        m_name = re.search(r'"name"\s*:\s*"([^"]+)"', s)
        name = m_name.group(1) if m_name else "tool"

        m_args = re.search(r'"args"\s*:\s*(.*)', s, re.DOTALL)
        if m_args:
            args_str = m_args.group(1).strip().rstrip(",}] ")
            try:
                args_obj = json.loads(args_str)
                return name, args_obj, ""
            except Exception:
                return name, args_str, ""

        return name, s, ""


def parse_stream(raw: str):
    if not raw:
        return []
    if not isinstance(raw, str):
        raw = str(raw)
    events_list = []
    for m in _TOOL_FULL_RE.finditer(raw):
        events_list.append((m.start(), m.end(), "tool", m.group("body")))
    for m in _RES_FULL_RE.finditer(raw):
        events_list.append((m.start(), m.end(), "result", m.group("body")))
    events_list.sort(key=lambda e: (e[0], e[1]))

    segments = []
    pos = 0
    last_end = 0
    for s, e, kind, body in events_list:
        if s < last_end:
            continue
        if s > pos:
            txt = raw[pos:s]
            if txt.strip():
                segments.append(("text", normalize_fences(txt).strip()))
        segments.append((kind, body))
        pos = e
        last_end = e

    tail = raw[pos:]
    if tail:
        mopen = _OPEN_TAG_RE.search(tail)
        if mopen:
            before = tail[: mopen.start()]
            if before.strip():
                segments.append(("text", normalize_fences(before).strip()))
            kind = "tool_open" if mopen.group("tag") in TOOL_TAGS else "result_open"
            segments.append((kind, tail[mopen.end():]))
        elif tail.strip():
            segments.append(("text", normalize_fences(tail).strip()))

    return segments


# ══════════════════════════════════════════════════════════════════════════════
#  OUTPUT STYLING & TERMINAL CARDS
# ══════════════════════════════════════════════════════════════════════════════

_TOKEN_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[\w./\\-]+\.(?:py|json|log|txt|md|yaml|yml|pdf|tar|gz|zip))"
    r"|(?P<url>https?://[^\s\"'>]+)"
    r"|(?P<dur>\b\d+(?:\.\d+)?(?:ms|s)\b)"
    r"|(?P<num>\b\d+\b)"
)


def _styled_out_line(line: str) -> Text:
    low = line.lower()
    base = None
    if any(k in low for k in ("error", "traceback", "exception", "failed", "fatal", "timed out")):
        base = "#ff5555"
    elif any(k in low for k in ("warn", "deprecat", "attempt")):
        base = "#e5e5e5"
    elif any(k in low for k in ("success", "done", "complete", "finished", " ok")):
        base = "#d4d4d4"

    t = Text("  ", style="")
    pos = 0
    for m in _TOKEN_RE.finditer(line):
        if m.start() > pos:
            t.append(line[pos:m.start()], style=base or "#a3a3a3")
        if m.group("url"):
            t.append(m.group("url"), style="underline #e5e5e5")
        elif m.group("path"):
            t.append(m.group("path"), style="underline #a3a3a3")
        elif m.group("dur"):
            t.append(m.group("dur"), style="#a3a3a3")
        else:
            t.append(m.group("num"), style="bold #d4d4d4")
        pos = m.end()
    if pos < len(line):
        t.append(line[pos:], style=base or "#a3a3a3")
    return t


def build_tool_call_panel(payload: str, running: bool = False) -> Panel:
    name, args, cmd = parse_tool_json(payload)
    color = "#e5e5e5" if running else "#e5e5e5"
    tag = "[CALLING]" if running else "[CALL]"

    title = Text()
    title.append(f" {tag} ", style=f"bold black on {color}")
    title.append(f" {name} ", style="bold #f5f5f5")
    if cmd and cmd not in ("builtin", name):
        title.append(f"({cmd}) ", style="#a3a3a3")

    lines = []
    if isinstance(args, list):
        if not args:
            lines.append(Text("  (no arguments passed)", style="italic #737373"))
        else:
            for idx, item in enumerate(args):
                prefix = f"  [{idx}] " if len(args) > 1 else "  ▸ "
                t = Text(prefix, style="bold #e5e5e5")
                if isinstance(item, (dict, list)):
                    t.append(json.dumps(item, indent=2, ensure_ascii=False), style="#d4d4d4")
                else:
                    t.append(str(item), style="#e5e5e5")
                lines.append(t)
    elif isinstance(args, dict):
        if not args:
            lines.append(Text("  (empty arguments)", style="italic #737373"))
        else:
            for k, v in args.items():
                t = Text(f"  ▸ {k}: ", style="bold #e5e5e5")
                if isinstance(v, (dict, list)):
                    t.append(json.dumps(v, ensure_ascii=False), style="#d4d4d4")
                else:
                    t.append(str(v), style="#e5e5e5")
                lines.append(t)
    elif isinstance(args, str):
        clean_args = args.strip().strip('"')
        if clean_args:
            for ln in clean_args.splitlines():
                t = Text("  ▸ ", style="bold #e5e5e5")
                t.append(ln, style="#e5e5e5")
                lines.append(t)
        else:
            lines.append(Text("  (streaming arguments...)", style="italic #737373"))
    else:
        t = Text("  ▸ ", style="bold #e5e5e5")
        t.append(str(args), style="#e5e5e5")
        lines.append(t)

    return Panel(
        Group(*lines),
        title=title,
        border_style=color,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


# ── Result accent palette ────────────────────────────────────────────────
# Only the panel border and header label get colored; content lines keep
# their grayscale/red styling so nothing becomes a color soup.
_TOOL_ACCENT    = "#5fd7d7"   # cyan  — search_net, check_screen, builtins
_SCRIPT_ACCENT  = "#e0af68"   # amber — run_gen_code, run_scripts, EXEC
_NEUTRAL_ACCENT = "#e5e5e5"   # carbon fallback

_SCRIPT_AGENT_NAMES = frozenset({
    "run_gen_code", "run_scripts", "gen_code",
    "exec", "python", "python3",
})


def _result_accent(agent: str) -> str:
    """Return the accent color for a tool-result panel, based on tool name."""
    name = str(agent or "").strip().lower()
    if name in _SCRIPT_AGENT_NAMES:
        return _SCRIPT_ACCENT
    if not name:
        return _NEUTRAL_ACCENT
    return _TOOL_ACCENT


def build_chat_output_panel(output: str, running: bool = False, agent: str = "EXEC") -> Panel:
    color = _result_accent(agent)
    tag = "[RUNNING]" if running else "[OUTPUT]"

    title = Text()
    title.append(f" {tag} ", style=f"bold black on {color}")
    title.append(f" {agent} ", style=f"bold {color}")

    lines = output.strip().splitlines() if output.strip() else (["(executing...)"] if running else ["(no output)"])
    n = len(lines)
    if n > MAX_CHAT_OUTPUT_LINES:
        head = MAX_CHAT_OUTPUT_LINES // 2
        tail = MAX_CHAT_OUTPUT_LINES - head
        lines = lines[:head] + [f"... +{n - MAX_CHAT_OUTPUT_LINES} lines truncated ..."] + lines[-tail:]
    # Single Text is far cheaper than N styled Text objects + regex token scans.
    # Only apply error red to the whole block if a clear failure marker appears.
    joined = "\n".join("  " + ln for ln in lines)
    low = joined.lower()
    if any(k in low for k in ("traceback (most recent call last)", "exception:", "error:", "[stderr]", "timed out")):
        body_style = "#ff5555"
    else:
        body_style = "#a3a3a3"
    body = Text(joined, style=body_style)

    return Panel(
        body,
        title=title,
        border_style=color,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


def build_report_panel(report_text: str, agent: str = "AGENT") -> Panel:
    title = Text()
    title.append(" [REPORT TO LOGIC] ", style="bold black on #e5e5e5")
    title.append(f" {agent.upper()} ", style="bold #f5f5f5")

    lines = report_text.strip().splitlines() if report_text.strip() else ["(empty report)"]
    body = [Text(f"  {ln}", style="#e5e5e5") for ln in lines]

    return Panel(
        Group(*body),
        title=title,
        border_style="#e5e5e5",
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  HALL STREAM INTERCEPTOR & QUEUE
# ══════════════════════════════════════════════════════════════════════════════

hall_stream_queue = queue.Queue()

def _discard_pending_script_results() -> None:
    """Remove SCRIPT_RESULT / THREAD results currently sitting in user_input_queue."""
    if user_input_queue is None:
        return
    try:
        with user_input_queue.mutex:
            kept = [
                item for item in user_input_queue.queue
                if not (
                    isinstance(item, str)
                    and (
                        item.startswith("SCRIPT_RESULT:")
                        or item.startswith("system:[THREAD")
                    )
                )
            ]
            user_input_queue.queue.clear()
            user_input_queue.queue.extend(kept)
    except Exception:
        pass


def push_hall_event(agent: str, kind: str, payload: str):
    """Pushes a live event directly to the TUI Hall stream pump."""
    hall_stream_queue.put((agent.lower(), kind, payload))


# ══════════════════════════════════════════════════════════════════════════════
#  REAL BACKEND HOOKS
# ══════════════════════════════════════════════════════════════════════════════
#
#  Design contract (R3):
#
#    * logic.py owns execution. Subprocess spawning, validate(), tool
#      dispatch, memory, reminders, GUI control, agent dispatch — none of
#      that is reimplemented here.
#
#    * The TUI installs a single `_line_sink` on the backend instance. Your
#      patched `logic.py::run_code_streamed` calls `self._line_sink(line)`
#      for every line the child process emits; the sink funnels those lines
#      into a queue that the top-level stream merger drains.
#
#    * If your logic.py doesn't call `_line_sink` (older version, different
#      patch shape), the TUI detects that and installs a stdout-tee fallback
#      so live output still arrives. Nothing else changes.
#
#    * Top-level `call_logic` is wrapped exactly once (at depth 1) to merge
#      sink lines with the generator's own yields. Nested `call_logic`
#      hops — memory_manager / gui_controller / reminder / dispatch — get a
#      cosmetic <tool_result> echo so users see what tools returned without
#      watching the LLM's reply.

logic_ai = None
voice_module = None
_stop_voice = None
terminal_code_fn = None
toggle_voice_mode_fn = None
get_voice_mode_fn = None
user_input_queue = None
logic_hall_module = None
logic_module_ref = None
backend_load_error = ""
logic_model_name = "simulation"


# Sentinel prefix marking network telemetry lines emitted by the prelude's
# requests hook. The TUI displays them live (as "⚡ ..." lines). Whether they
# also reach LOGIC depends on whether your logic.py strips them before
# building SCRIPT_RESULT — see the note in the docstring of `_sink` below.
NET_TEL_PREFIX = "__LOGIC_NET__|"


def _net_tel_display(body: str) -> str:
    return "⚡ " + body


# Module-level queue: the backend's `_line_sink` writes each streamed line
# here; the depth-1 merger drains it alongside the generator.
_sink_q: "queue.Queue[str]" = queue.Queue()


def _merge_sink_and_gen(gen):
    """Yield `gen`'s items, interleaving lines the backend pushed to _sink_q.

    Live lines are wrapped once in <tool_result>…</tool_result> so the TUI's
    stream parser paints them as a live output block. Generator items (LLM
    tokens, tool-result echoes) pass through verbatim, closing any open live
    block first.
    """
    DONE = object()
    chunk_q: "queue.Queue[object]" = queue.Queue()

    def _drive():
        try:
            for c in gen:
                chunk_q.put(c)
        except BaseException as e:
            chunk_q.put(f"\n[stream error: {type(e).__name__}: {e}]\n")
        finally:
            chunk_q.put(DONE)

    threading.Thread(target=_drive, daemon=True, name="tui-logic-driver").start()

    opened = False
    while True:
        # 1. Drain any lines the backend's sink pushed since last loop.
        while True:
            try:
                line = _sink_q.get_nowait()
            except queue.Empty:
                break
            if not opened:
                yield "\n\n<tool_result>\n"
                opened = True
            yield line

        # 2. Wait for the next generator chunk.
        try:
            item = chunk_q.get(timeout=0.03)
        except queue.Empty:
            continue

        if item is DONE:
            # Sink writes are happens-before DONE (same thread), so one
            # final drain captures any trailing lines.
            while True:
                try:
                    line = _sink_q.get_nowait()
                except queue.Empty:
                    break
                if not opened:
                    yield "\n\n<tool_result>\n"
                    opened = True
                yield line
            if opened:
                yield "</tool_result>\n\n"
            return

        # Non-sink chunk: close any open live block, then emit verbatim.
        if opened:
            yield "</tool_result>\n\n"
            opened = False
        yield item


# ──────────────────────────────────────────────────────────────────────────────
#  CHILD-SIDE TEE PRELUDE
# ──────────────────────────────────────────────────────────────────────────────
# Model-generated code very often does:
#     r = subprocess.run(cmd, capture_output=True, text=True); print(r.stdout)
# `capture_output=True` buffers the child's ENTIRE stdout until it exits —
# from the TUI's seat that looks like a long freeze followed by a giant burst.
# We prepend this prelude to generated scripts so that, for capture_output
# calls only, the child's stdout is simultaneously captured (return value is
# IDENTICAL to before) AND teed live to the script's own stdout — which
# logic.py's run_code_streamed already forwards to the sink.
#
# Escape hatch: set LOGIC_NO_TEE=1 to disable the subprocess tee.
#              set LOGIC_NO_NET_TEE=1 to disable the requests telemetry hook.

_GEN_CODE_TEE_PRELUDE = '''
# --- LOGIC live-output shim (injected by the TUI; LOGIC_NO_TEE=1 disables) ---
import os as _lg_os
if not _lg_os.environ.get("LOGIC_NO_TEE"):
    import subprocess as _lg_sp
    import sys as _lg_sys
    import threading as _lg_th

    _lg_orig_run = _lg_sp.run

    def _lg_run(cmd, *args, **kwargs):
        if not (
            kwargs.get("capture_output")
            and kwargs.get("stdout") is None
            and kwargs.get("stderr") is None
            and "stdin" not in kwargs
            and "input" not in kwargs
            and kwargs.get("timeout") is None
        ):
            return _lg_orig_run(cmd, *args, **kwargs)
        kw = dict(kwargs)
        kw.pop("capture_output", None)
        kw.pop("timeout", None)
        check = bool(kw.pop("check", False))
        text_mode = bool(
            kw.get("text") or kw.get("universal_newlines") or kw.get("encoding")
        )
        kw["stdout"] = _lg_sp.PIPE
        kw["stderr"] = _lg_sp.PIPE
        proc = _lg_sp.Popen(cmd, *args, **kw)
        bag_out, bag_err = [], []
        sink = _lg_sys.stdout if text_mode else getattr(_lg_sys.stdout, "buffer", None)
        sentinel = "" if text_mode else b""

        def _tee(stream, bag, live):
            try:
                for chunk in iter(stream.readline, sentinel):
                    if not chunk:
                        break
                    bag.append(chunk)
                    if live and sink is not None:
                        try:
                            sink.write(chunk)
                            sink.flush()
                        except Exception:
                            pass
            finally:
                stream.close()

        t1 = _lg_th.Thread(target=_tee, args=(proc.stdout, bag_out, True), daemon=True)
        t2 = _lg_th.Thread(target=_tee, args=(proc.stderr, bag_err, False), daemon=True)
        t1.start()
        t2.start()
        try:
            rc = proc.wait()
        except BaseException:
            proc.kill()
            rc = proc.wait()
        t1.join(timeout=2)
        t2.join(timeout=2)
        out = ("" if text_mode else b"").join(bag_out)
        err = ("" if text_mode else b"").join(bag_err)
        if check and rc:
            raise _lg_sp.CalledProcessError(rc, cmd, output=out, stderr=err)
        return _lg_sp.CompletedProcess(cmd, rc, out, err)

    try:
        _lg_sp.run = _lg_run
    except Exception:
        pass
if not _lg_os.environ.get("LOGIC_NO_NET_TEE"):
    try:
        import requests as _lg_rq
        import time as _lg_time
        import sys as _lg_sys2

        _lg_orig_request = _lg_rq.sessions.Session.request

        def _lg_request(self, method, url, **kwargs):
            _t0 = _lg_time.perf_counter()
            _resp = _lg_orig_request(self, method, url, **kwargs)
            try:
                _ms = (_lg_time.perf_counter() - _t0) * 1000
                _lg_sys2.stdout.write(
                    "__LOGIC_NET__|"
                    + str(method).upper()
                    + " "
                    + str(url)
                    + " -> "
                    + str(getattr(_resp, "status_code", "?"))
                    + " ("
                    + ("%.0f" % _ms)
                    + " ms)" + chr(10)
                )
                _lg_sys2.stdout.flush()
            except Exception:
                pass
            return _resp

        _lg_rq.sessions.Session.request = _lg_request
    except Exception:
        pass
# --- end LOGIC live-output shim ---

'''


def _inject_stream_prelude(code: str) -> str:
    """Prepend the child-side tee shim to generated code.

    Placement rules: keep a shebang on line 1 and keep any `from __future__`
    imports before the shim (they must precede all other statements).
    """
    if os.environ.get("LOGIC_NO_TEE"):
        return code
    lines = code.splitlines(keepends=True)
    idx = 0
    if lines and lines[0].startswith("#!"):
        idx = 1
    for i in range(idx, min(idx + 8, len(lines))):
        if lines[i].strip().startswith("from __future__ import"):
            idx = i + 1
    return "".join(lines[:idx]) + _GEN_CODE_TEE_PRELUDE + "".join(lines[idx:])


def _install_line_sink(ai_instance) -> None:
    """Hook logic.py's existing run_code_streamed via `_line_sink`.

    logic.py already does:
        sink = getattr(self, "_line_sink", None)
        ...
        if sink is not None: sink(line)

    So the TUI must NOT replace run_code_streamed — just install the sink.
    That keeps stdin/encoding/kill behavior owned by logic.py.
    """
    if getattr(ai_instance, "_tui_sink_installed", False):
        return

    def _sink(line, _ai=ai_instance):
        try:
            _sink_q.put(line if line is not None else "")
        except Exception:
            pass
        try:
            _ai._tui_lines_streamed = True
        except Exception:
            pass

    ai_instance._line_sink = _sink
    ai_instance._tui_sink_installed = True



def _drain_if_generator(fn):
    """Wrap a backend method so generator handlers still run their side effects.

    logic.py call_logic does `handler(args)` without iterating. Methods that
    `yield` (save_script, chat_monitor, self_reload) would otherwise never
    execute. We drain them here so TUI stays compatible without changing logic.py.
    """
    if fn is None or getattr(fn, "_tui_drained", False):
        return fn

    def wrapped(*args, **kwargs):
        result = fn(*args, **kwargs)
        if inspect.isgenerator(result):
            collected = []
            try:
                for item in result:
                    collected.append(item)
            except Exception:
                raise
            return collected
        return result

    wrapped._tui_drained = True
    try:
        wrapped.__name__ = getattr(fn, "__name__", "wrapped")
    except Exception:
        pass
    return wrapped


def _patch_session_rollback(session_cls):
    """Class-level safety net against retry duplicates (logic.py stays stock).

    OpenAIChatSession.send_message_stream appends the user prompt to history
    BEFORE hitting the API. If the stream dies mid-turn, that dangling user
    message stays and call_logic's retry loop re-appends the same prompt, so
    the model receives it twice (observed as a duplicated boot greeting /
    schedule). Wrapping at class level covers every session instance logic.py
    creates, including ones swapped in later by /model or reset_chat.
    """
    if getattr(session_cls, "_tui_rollback_patched", False):
        return
    _orig_sms = session_cls.send_message_stream

    def send_message_stream_with_rollback(self, prompt):
        snapshot = list(self.history) if isinstance(self.history, list) else None
        gen = _orig_sms(self, prompt)
        try:
            for chunk in gen:
                yield chunk
        except BaseException:
            # Failed turn: drop whatever it appended (the dangling user
            # prompt) so the retry in call_logic starts clean and cannot
            # duplicate the prompt in history.
            if snapshot is not None:
                try:
                    self.history[:] = snapshot
                except Exception:
                    pass
            raise

    try:
        send_message_stream_with_rollback.__name__ = getattr(
            _orig_sms, "__name__", "send_message_stream"
        )
    except Exception:
        pass
    session_cls.send_message_stream = send_message_stream_with_rollback
    session_cls._tui_rollback_patched = True


def _repair_session_history(session):
    """One-shot startup cleanup of a polluted chat history.

    Removes adjacent duplicate "system:" user entries and a trailing user
    entry that never got a reply (turn killed mid-stream), so a /reload
    restore or an old double-greeting can't carry duplicates forward.
    """
    hist = getattr(session, "history", None)
    if not isinstance(hist, list) or not hist:
        return
    try:
        cleaned = []
        for msg in hist:
            if (
                isinstance(msg, dict)
                and msg.get("role") == "user"
                and isinstance(msg.get("content"), str)
                and msg["content"].startswith("system:")
                and cleaned
                and isinstance(cleaned[-1], dict)
                and cleaned[-1].get("role") == "user"
                and cleaned[-1].get("content") == msg["content"]
            ):
                continue  # adjacent duplicate system prompt
            cleaned.append(msg)
        if cleaned and isinstance(cleaned[-1], dict) and cleaned[-1].get("role") == "user":
            cleaned.pop()  # unanswered dangling turn
        if len(cleaned) != len(hist):
            session.history = cleaned
            sys.stderr.write(
                f"[TUI] history repair: {len(hist)} -> {len(cleaned)} entries\n"
            )
    except Exception as e:
        sys.stderr.write(f"[TUI] history repair skipped: {e}\n")


def patch_logic_ai_stream_output(ai_instance):
    """Wire the TUI into the backend's real executors, without replacing them.

    Execution is the backend's. The TUI only:
      * installs `_line_sink` so logic.py's own run_code_streamed pushes lines to _sink_q,
      * wraps call_logic at depth 1 to merge sink lines with generator yields,
      * echoes non-streamed depth-2 SCRIPT_RESULT payloads so tools like
        search_net are visible to the user,
      * prepends the child-side tee prelude to generated code,
      * adds cosmetic markers around the slow tools.

    # v3-marker: echo depth-2 SCRIPT_RESULT
    """
    if getattr(ai_instance, "_tui_patched", False):
        return
    ai_instance._tui_patched = True

    # Retry-duplicate protection + startup history repair. Installed here so
    # logic.py itself stays completely stock.
    _patch_session_rollback(type(ai_instance.chat))
    _repair_session_history(ai_instance.chat)

    _install_line_sink(ai_instance)

    _orig_call_logic = ai_instance.call_logic

    def tui_call_logic(prompt="something"):
        depth = getattr(ai_instance, "_tui_depth", 0) + 1
        ai_instance._tui_depth = depth
        try:
            if depth == 1:
                # Fresh top-level turn: clear any stale streamed marker.
                try:
                    ai_instance._tui_lines_streamed = False
                except Exception:
                    pass
                yield from _merge_sink_and_gen(_orig_call_logic(prompt))
                return

            is_script_result = (
                isinstance(prompt, str)
                and prompt.startswith("SCRIPT_RESULT:")
            )

            if is_script_result:
                streamed = getattr(ai_instance, "_tui_lines_streamed", False)
                try:
                    ai_instance._tui_lines_streamed = False
                except Exception:
                    pass
                if not streamed:
                    content = prompt[len("SCRIPT_RESULT:"):].strip()
                    if content:
                        yield "\n\n<tool_result>\n"
                        yield content + "\n"
                        yield "</tool_result>\n\n"
            else:
                text = prompt if isinstance(prompt, str) else repr(prompt)
                yield "\n\n<tool_result>\n"
                yield text if text.endswith("\n") else text + "\n"
                yield "</tool_result>\n\n"

            yield from _orig_call_logic(prompt)
        finally:
            ai_instance._tui_depth = depth - 1

    ai_instance.call_logic = tui_call_logic

    _orig_run_gen_code = getattr(ai_instance, "run_gen_code", None)
    if _orig_run_gen_code is not None:
        def tui_run_gen_code(code):
            try:
                code = _inject_stream_prelude(code)
            except Exception:
                pass
            # call_logic invokes this without iterating — must NOT be a generator
            return _orig_run_gen_code(code)
        ai_instance.run_gen_code = tui_run_gen_code

    _orig_search_net = getattr(ai_instance, "search_net", None)
    if _orig_search_net is not None:
        def tui_search_net(args):
            # call_logic invokes handlers without iterating — must NOT be a generator
            return _orig_search_net(args)
        ai_instance.search_net = tui_search_net

    _orig_gen_code = getattr(ai_instance, "gen_code", None)
    if _orig_gen_code is not None:
        def tui_gen_code(args):
            # call_logic invokes handlers without iterating — must NOT be a generator
            return _orig_gen_code(args)
        ai_instance.gen_code = tui_gen_code

    _orig_check_screen = getattr(ai_instance, "check_screen", None)
    if _orig_check_screen is not None:
        def tui_check_screen(args):
            # call_logic invokes handlers without iterating — must NOT be a generator
            return _orig_check_screen(args)
        ai_instance.check_screen = tui_check_screen

    # Drain yield-based handlers so their side effects still run under call_logic.
    for _name in ("save_script", "chat_monitor", "self_reload"):
        _fn = getattr(ai_instance, _name, None)
        if _fn is not None:
            setattr(ai_instance, _name, _drain_if_generator(_fn))


def hook_worker_for_live_hall_stream(worker):
    """Attach stream hooks to a Vinci or Sage hall worker."""
    agent_key = worker.name.lower()
    orig_call = worker.ai.call

    def hooked_call(task="something"):
        push_hall_event(agent_key, "task_start", str(task))
        try:
            for chunk in orig_call(task):
                push_hall_event(agent_key, "chunk", chunk)
                yield chunk
        finally:
            push_hall_event(agent_key, "task_end", "")

    worker.ai.call = hooked_call

    orig_call_logic = getattr(worker.ai, "call_logic", None)
    if orig_call_logic:

        def hooked_call_logic(args):
            text = args[0] if isinstance(args, list) else str(args)
            push_hall_event(agent_key, "report", text)
            yield from orig_call_logic(args)

        worker.ai.call_logic = hooked_call_logic

    orig_sink = worker._push_line

    def hooked_sink(msg):
        push_hall_event(agent_key, "stdout", str(msg))
        try:
            orig_sink(msg)
        except Exception:
            pass

    worker._push_line = hooked_sink
    try:
        import logic_hall as _lh_mod
        _lh_mod.log_sinks[worker.name] = hooked_sink
        _lh_mod.log_sinks[worker.name.lower()] = hooked_sink
    except Exception:
        if logic_hall_module and hasattr(logic_hall_module, "log_sinks"):
            logic_hall_module.log_sinks[worker.name] = hooked_sink
            logic_hall_module.log_sinks[worker.name.lower()] = hooked_sink


if USE_REAL:
    try:
        import logic as _logic_module

        logic_module_ref = _logic_module
        logic_ai = getattr(_logic_module, "logic_ai", None)
        voice_module = getattr(_logic_module, "logic_voice", None)
        _stop_voice = getattr(_logic_module, "stop_voice", lambda: None)
        terminal_code_fn = getattr(_logic_module, "terminal_code", None)
        toggle_voice_mode_fn = getattr(_logic_module, "toggle_voice_mode", None)
        get_voice_mode_fn = getattr(_logic_module, "get_voice_mode", None)
        user_input_queue = getattr(_logic_module, "user_input_queue", queue.Queue())
        logic_model_name = (
            getattr(_logic_module, "MODEL", None)
            or getattr(getattr(logic_ai, "chat", None), "model", None)
            or "unknown"
        )

        load_last_summary = getattr(
            _logic_module, "load_last_summary",
            lambda path=None: "No prior summary.",
        )
        get_local_day_schedule = getattr(
            _logic_module, "get_local_day_schedule",
            lambda: datetime.now().strftime("Schedule for %B %d, %Y: System active."),
        )

        import logic_hall as _lh

        logic_hall_module = _lh
        if not getattr(_lh, "workers", None):
            _lh.init_hall(user_input_queue)

        for w in getattr(_lh, "workers", {}).values():
            hook_worker_for_live_hall_stream(w)
            w._tui_hooked = True

        if logic_ai is None:
            raise AttributeError("logic.py imported, but logic_ai instance was not found.")

        patch_logic_ai_stream_output(logic_ai)

    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        backend_load_error = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[WARN] Failed to load real backend: {backend_load_error}\n")
        USE_REAL = False
        logic_ai = None
        logic_hall_module = None
        user_input_queue = None
        _stop_voice = None
        terminal_code_fn = None
        toggle_voice_mode_fn = None
        get_voice_mode_fn = None
        logic_model_name = "simulation"


# ══════════════════════════════════════════════════════════════════════════════
#  SIMULATION LAYER (DEMO FALLBACK)
# ══════════════════════════════════════════════════════════════════════════════

SANDBOX_DIR = os.path.join(tempfile.gettempdir(), "logic_sandbox")
os.makedirs(SANDBOX_DIR, exist_ok=True)


def run_sandboxed(code: str, filename: str = "scratch.py", timeout: float = 8.0):
    """
    Write `code` to a file in SANDBOX_DIR and run it, streaming stdout/stderr
    line by line. Yields ("stdout", line) and ("stderr", line) tuples,
    then finally ("exit", returncode).
    """
    path = os.path.join(SANDBOX_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=SANDBOX_DIR,
        )
    except Exception as e:
        yield ("stderr", f"Failed to launch: {e}\n")
        yield ("exit", 1)
        return

    import threading as _t

    out_q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def reader(stream, tag):
        for line in iter(stream.readline, ""):
            out_q.put((tag, line))
        stream.close()

    t1 = _t.Thread(target=reader, args=(proc.stdout, "stdout"), daemon=True)
    t2 = _t.Thread(target=reader, args=(proc.stderr, "stderr"), daemon=True)
    t1.start(); t2.start()

    deadline = time.monotonic() + timeout
    done_readers = 0
    while done_readers < 2:
        if time.monotonic() > deadline and proc.poll() is None:
            proc.kill()
            yield ("stderr", f"\n[timeout after {timeout}s — process killed]\n")
        try:
            tag, line = out_q.get(timeout=0.05)
            yield (tag, line)
        except queue.Empty:
            if not t1.is_alive():
                done_readers = max(done_readers, 1)
            if not t2.is_alive():
                done_readers = max(done_readers, 2)
            if proc.poll() is not None and out_q.empty() and not t1.is_alive() and not t2.is_alive():
                break

    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()
    yield ("exit", proc.returncode)


CODING_SCENARIOS = {
    "counter": {
        "desc": "Count from 1 to N with formatted output",
        "code": '''\
# counter.py — formatted counter
import sys
N = 10
print(f"{'='*30}")
print(f"  COUNTING 1..{N}")
print(f"{'='*30}")
total = 0
for i in range(1, N + 1):
    total += i
    bar = "#" * i
    print(f"  {i:>3} | {bar:<10} | running sum: {total}")
print(f"{'='*30}")
print(f"Done. Sum = {total}")
''',
    },
    "fibonacci": {
        "desc": "Generate Fibonacci sequence with memoization",
        "code": '''\
# fibonacci.py — memoized fib with timing
from functools import lru_cache
import time

@lru_cache(maxsize=None)
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

print("Fibonacci(0..20):")
t0 = time.perf_counter()
seq = [fib(i) for i in range(21)]
elapsed = (time.perf_counter() - t0) * 1000
for i, v in enumerate(seq):
    print(f"  fib({i:>2}) = {v}")
print(f"\\nComputed 21 values in {elapsed:.3f}ms (memoized)")
''',
    },
    "primes": {
        "desc": "Sieve of Eratosthenes up to 100",
        "code": '''\
# primes.py — sieve of eratosthenes
def sieve(limit):
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit ** 0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
    return [i for i, p in enumerate(is_prime) if p]

primes = sieve(100)
print(f"Found {len(primes)} primes below 100:")
for i in range(0, len(primes), 10):
    row = primes[i:i+10]
    print("  " + " ".join(f"{p:>3}" for p in row))
print(f"\\nLargest: {primes[-1]}")
''',
    },
    "sorting": {
        "desc": "Benchmark bubble vs quicksort on random data",
        "code": '''\
# sorting.py — benchmark two sorts
import random, time

def bubble_sort(a):
    a = a[:]
    n = len(a)
    for i in range(n):
        for j in range(n - i - 1):
            if a[j] > a[j + 1]:
                a[j], a[j + 1] = a[j + 1], a[j]
    return a

def quick_sort(a):
    if len(a) <= 1:
        return a
    p = a[len(a) // 2]
    return (quick_sort([x for x in a if x < p])
            + [x for x in a if x == p]
            + quick_sort([x for x in a if x > p]))

random.seed(42)
data = [random.randint(0, 999) for _ in range(400)]
print(f"Dataset: {len(data)} integers")

t0 = time.perf_counter(); s1 = bubble_sort(data); t1 = time.perf_counter()
print(f"  bubble_sort : {(t1-t0)*1000:7.1f} ms")

t0 = time.perf_counter(); s2 = quick_sort(data);  t1 = time.perf_counter()
print(f"  quick_sort  : {(t1-t0)*1000:7.1f} ms")

assert s1 == s2, "sorts disagree!"
print("  results match: OK")
print(f"  first 10: {s1[:10]}")
''',
    },
    "json_parse": {
        "desc": "Parse nested JSON and walk the tree",
        "code": '''\
# json_walk.py — parse + walk
import json

payload = {
    "service": "logic-ai",
    "version": "1.4.2",
    "agents": [
        {"name": "vinci", "role": "os",   "load": 0.62},
        {"name": "sage",  "role": "web",  "load": 0.31},
    ],
    "endpoints": {
        "chat":   "/api/v1/chat",
        "hall":   "/api/v1/hall",
        "health": "/api/v1/health",
    },
}

def walk(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{prefix}[{i}]")
    else:
        print(f"  {prefix:<28} = {obj!r}")

print("JSON tree:")
walk(payload)
print(f"\\nSerialized size: {len(json.dumps(payload))} bytes")
''',
    },
    "http_client": {
        "desc": "Simulated HTTP fetch with retry logic",
        "code": '''\
# http_fetch.py — mocked HTTP with retries
import time, random

random.seed(7)

def fake_get(url, attempts=3):
    for attempt in range(1, attempts + 1):
        latency = random.uniform(0.05, 0.25)
        time.sleep(latency)
        if random.random() < 0.4 and attempt < attempts:
            print(f"  [{attempt}] GET {url} -> 503 retry-after 0.1s ({latency*1000:.0f}ms)")
            time.sleep(0.1)
            continue
        print(f"  [{attempt}] GET {url} -> 200 OK ({latency*1000:.0f}ms)")
        return {"status": 200, "body": f"<html>content of {url}</html>"}
    print(f"  FAILED after {attempts} attempts")
    return None

for url in ["https://example.com/a", "https://example.com/b", "https://example.com/c"]:
    resp = fake_get(url)
    if resp:
        print(f"      body length: {len(resp['body'])}")
''',
    },
    "buggy": {
        "desc": "Buggy script that raises (traceback demo)",
        "code": '''\
# buggy.py — intentional bug for demo
def divide(a, b):
    return a / b

print("Starting computation...")
print("  10 / 2 =", divide(10, 2))
print("  7 / 3  =", divide(7, 3))
print("Now trying 5 / 0...")
result = divide(5, 0)   # <-- ZeroDivisionError
print("Never reached:", result)
''',
    },
    "csv_report": {
        "desc": "Generate and summarize a CSV report",
        "code": '''\
# csv_report.py — build + summarize CSV in memory
import csv, io, statistics

rows = [
    {"name": "alpha", "cpu": 12.5, "mem": 220, "ok": True},
    {"name": "beta",  "cpu": 48.1, "mem": 890, "ok": True},
    {"name": "gamma", "cpu": 91.7, "mem": 1450, "ok": False},
    {"name": "delta", "cpu": 33.2, "mem": 610, "ok": True},
]

buf = io.StringIO()
w = csv.DictWriter(buf, fieldnames=["name", "cpu", "mem", "ok"])
w.writeheader()
w.writerows(rows)
csv_text = buf.getvalue()

print("Generated CSV:")
for line in csv_text.strip().splitlines():
    print("  " + line)

cpus = [r["cpu"] for r in rows]
mems = [r["mem"] for r in rows]
print(f"\\nCPU  mean={statistics.mean(cpus):.2f}  max={max(cpus)}")
print(f"MEM  mean={statistics.mean(mems):.0f}  max={max(mems)}")
print(f"Failed rows: {[r['name'] for r in rows if not r['ok']]}")
''',
    },
    "montecarlo": {
        "desc": "Estimate pi with Monte Carlo sampling (progress lines)",
        "code": '''\
# montecarlo.py — estimate pi by sampling the unit square
import random, time

random.seed(99)
N = 600_000
inside = 0
t0 = time.perf_counter()
print(f"Monte Carlo pi | {N:,} samples")
for i in range(1, N + 1):
    x = random.random()
    y = random.random()
    if x * x + y * y <= 1.0:
        inside += 1
    if i % 150_000 == 0:
        print(f"  {i:>9,} samples -> pi ~ {4.0 * inside / i:.6f}")
elapsed = time.perf_counter() - t0
print(f"final: pi ~ {4.0 * inside / N:.6f}  (true 3.141593)")
print(f"{N / elapsed:,.0f} samples/sec in {elapsed:.2f}s")
''',
    },
    "matrix_mult": {
        "desc": "Pure-Python 60x60 matrix multiply with checksum",
        "code": '''\
# matrix_mult.py — pure python matmul benchmark
import random, time

N = 60
random.seed(1)
A = [[random.random() for _ in range(N)] for _ in range(N)]
B = [[random.random() for _ in range(N)] for _ in range(N)]

print(f"multiplying {N}x{N} matrices (pure python, {N**3:,} FMAs)...")
t0 = time.perf_counter()
C = [[sum(A[i][k] * B[k][j] for k in range(N)) for j in range(N)] for i in range(N)]
ms = (time.perf_counter() - t0) * 1000

checksum = sum(sum(row) for row in C)
print(f"done in {ms:.1f} ms")
print(f"checksum: {checksum:.6f}")
print(f"C[0][:4] = {[round(v, 4) for v in C[0][:4]]}")
''',
    },
    "word_freq": {
        "desc": "Word-frequency histogram over an embedded corpus",
        "code": """\
# word_freq.py — keyword histogram
import re
from collections import Counter

text = '''
The logic engine routes every request through the same core loop: parse the
intent, pick the tools, stream the tokens, and log the trace. The loop is the
product. When the loop is fast the agent feels alive; when the loop stalls the
agent feels broken. Keep the loop lean, keep the tools honest, and keep the
trace readable. Measure the loop, cache the tools, trim the trace, and never
let the loop block on a tool that the cache could answer.
'''
words = re.findall(r"[a-z']+", text.lower())
stop = {"the", "a", "an", "and", "of", "to", "in", "is", "it", "on", "as",
        "at", "by", "be", "this", "that", "every", "could", "when", "never"}
count = Counter(w for w in words if w not in stop)
print(f"total words: {len(words)} | unique: {len(set(words))} | kept: {sum(count.values())}")
print("top 12 keywords:")
for w, c in count.most_common(12):
    bar = "#" * c
    print(f"  {w:<12} {c:>3} {bar}")
""",
    },
    "regex_lab": {
        "desc": "Extract emails/URLs/IPs/timestamps from a log buffer",
        "code": '''\
# regex_lab.py — pattern extraction over a log buffer
import re

sample = """
2026-09-21 08:14:32 INFO  user alice@example.com logged in from 192.168.1.10
2026-09-21 08:15:01 WARN  retrying https://api.example.io/v2/status after timeout
2026-09-21 08:16:44 ERROR mail relay failed for bob.smith+dev@corp.net
2026-09-21 08:17:02 INFO  docs moved to https://docs.corp.net/logic/overview
2026-09-21 08:18:59 ERROR connection from 10.0.0.77 refused
"""

patterns = {
    "emails":     r"[\\w.+-]+@[\\w-]+\\.[\\w.]+",
    "urls":       r"https?://[^\\s]+",
    "ipv4":       r"\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b",
    "timestamps": r"\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}",
}

print("scanning log buffer (5 lines)...")
for name, pat in patterns.items():
    hits = re.findall(pat, sample)
    print(f"  {name:<11} {len(hits)} match(es)")
    for h in hits:
        print(f"    - {h}")
''',
    },
    "ascii_chart": {
        "desc": "Random-walk telemetry rendered as an ASCII chart",
        "code": '''\
# ascii_chart.py — random walk rendered as a terminal chart
import random

random.seed(5)
series = []
v = 50.0
for _ in range(48):
    v += random.uniform(-6, 6)
    v = max(5.0, min(95.0, v))
    series.append(v)

rows = 10
lo, hi = min(series), max(series)
span = max(hi - lo, 1e-9)
grid = [[" "] * len(series) for _ in range(rows)]
for x, val in enumerate(series):
    lvl = round((val - lo) / span * (rows - 1))
    grid[rows - 1 - lvl][x] = "#"

print("random-walk telemetry (48 samples)")
print(f"  max {hi:6.1f} |")
for r in grid:
    print("          |" + "".join(r))
print("          +" + "-" * len(series))
print(f"  min {lo:6.1f} | sample[0]={series[0]:.1f}  sample[-1]={series[-1]:.1f}")
''',
    },
    "tree_walk": {
        "desc": "Recursively render a nested project tree",
        "code": '''\
# tree_walk.py — recursive tree rendering
FS = {
    "logic-ai": {
        "core": {
            "logic.py": "18 KB",
            "logic_hall.py": "9 KB",
            "logic_memory_manager.py": "6 KB",
        },
        "tools": {
            "find_file.py": "2 KB",
            "scrape_site.py": "3 KB",
            "download_file.py": "4 KB",
        },
        "docs": {
            "ARCHITECTURE.md": "7 KB",
        },
        "README.md": "5 KB",
    }
}

def walk(node, prefix=""):
    items = list(node.items())
    for i, (name, val) in enumerate(items):
        last = i == len(items) - 1
        branch = "\\\\-- " if last else "+-- "
        print(prefix + branch + name + (f"  ({val})" if isinstance(val, str) else "/"))
        if isinstance(val, dict):
            walk(val, prefix + ("    " if last else "|   "))

print("project tree:")
walk(FS)
''',
    },
    "hash_bench": {
        "desc": "Benchmark md5/sha1/sha256/blake2b on a 200 KB payload",
        "code": '''\
# hash_bench.py — hashlib benchmark
import hashlib, time

data = (b"logic-ai-" * 20000)[:200_000]
algos = [
    ("md5", hashlib.md5),
    ("sha1", hashlib.sha1),
    ("sha256", hashlib.sha256),
    ("blake2b", hashlib.blake2b),
]

print(f"payload: {len(data):,} bytes x 50 iterations")
results = []
for name, fn in algos:
    t0 = time.perf_counter()
    for _ in range(50):
        fn(data).hexdigest()
    ms = (time.perf_counter() - t0) * 1000
    results.append((name, ms))
    print(f"  {name:<8} {ms:7.1f} ms")

fastest = min(results, key=lambda r: r[1])
print(f"fastest: {fastest[0]} ({fastest[1]:.1f} ms)")
''',
    },
    "async_demo": {
        "desc": "Six asyncio jobs racing to completion (as_completed)",
        "code": '''\
# async_demo.py — concurrent jobs with asyncio
import asyncio, random, time

async def job(i, delay):
    t0 = time.perf_counter()
    await asyncio.sleep(delay)
    return (i, delay, (time.perf_counter() - t0) * 1000)

async def main():
    random.seed(3)
    tasks = [
        asyncio.create_task(job(i, random.uniform(0.05, 0.30)))
        for i in range(1, 7)
    ]
    print(f"launched {len(tasks)} async jobs")
    for coro in asyncio.as_completed(tasks):
        i, d, ms = await coro
        print(f"  job {i}: target {d*1000:5.1f}ms -> wall {ms:5.1f}ms")
    print("all jobs complete")

asyncio.run(main())
''',
    },
    "palindrome": {
        "desc": "Hunt palindromic numbers with an is-palindrome check",
        "code": '''\
# palindrome.py — palindromic numbers below 10 000
import time

def is_pal(n: int) -> bool:
    s = str(n)
    return s == s[::-1]

t0 = time.perf_counter()
hits = [n for n in range(10_000) if is_pal(n)]
print(f"found {len(hits)} palindromes below 10,000")
for i in range(0, min(len(hits), 40), 10):
    print("  " + " ".join(f"{n:>5}" for n in hits[i:i + 10]))
print(f"...")
print(f"largest: {hits[-1]}  ({(time.perf_counter()-t0)*1000:.1f} ms)")
''',
    },
}


def _demo_list_text() -> str:
    lines = [
        "Simulation sandbox demos — ask naturally or use /demo <key>:",
        "",
    ]
    for key, sc in CODING_SCENARIOS.items():
        lines.append(f"  {key:<13} {sc['desc']}")
    lines.append("")
    lines.append("Also try: /vinci benchmark sorting · /sage todoist api · /both run primes")
    return "\n".join(lines)


def _pick_scenario(prompt_lower: str) -> str | None:
    for key in CODING_SCENARIOS:
        if key in prompt_lower:
            return key
    if any(k in prompt_lower for k in ("counter", "count to", "count from", "1 to 10")):
        return "counter"
    if "fib" in prompt_lower:
        return "fibonacci"
    if any(k in prompt_lower for k in ("prime", "sieve")):
        return "primes"
    if any(k in prompt_lower for k in ("sort", "benchmark sort", "quicksort", "bubble")):
        return "sorting"
    if any(k in prompt_lower for k in ("json", "parse", "walk json")):
        return "json_parse"
    if any(k in prompt_lower for k in ("http", "fetch", "request", "get ", "download")):
        return "http_client"
    if any(k in prompt_lower for k in ("bug", "traceback", "crash", "zero division")):
        return "buggy"
    if any(k in prompt_lower for k in ("csv", "report", "summarize")):
        return "csv_report"
    if "monte" in prompt_lower or "estimate pi" in prompt_lower:
        return "montecarlo"
    if "matrix" in prompt_lower or "matmul" in prompt_lower:
        return "matrix_mult"
    if any(k in prompt_lower for k in ("word count", "wordcount", "word freq", "frequency", "histogram")):
        return "word_freq"
    if "regex" in prompt_lower:
        return "regex_lab"
    if any(k in prompt_lower for k in ("chart", "plot", "ascii art", "telemetry")):
        return "ascii_chart"
    if "tree" in prompt_lower:
        return "tree_walk"
    if "hash" in prompt_lower or "checksum" in prompt_lower:
        return "hash_bench"
    if "async" in prompt_lower or "concurrent" in prompt_lower:
        return "async_demo"
    if "palindrome" in prompt_lower:
        return "palindrome"
    if any(k in prompt_lower for k in ("code", "script", "write", "run", "program")):
        return "counter"
    return None


class SimulatedLogicAI:
    def __init__(self):
        self.turn_count = 0
        self.vinci_busy = False
        self.sage_busy = False
        self._memories = [
            {"id": 1, "text": "CLI preferences: carbon grayscale theme", "type": "core", "importance": 0.95},
            {"id": 2, "text": "Runtime: Python 3.11 with Textual", "type": "env", "importance": 0.88},
            {"id": 3, "text": "Agents: VINCI (OS) + SAGE (Web)", "type": "arch", "importance": 0.80},
            {"id": 4, "text": "UI R3: TUI presentation-only, backend owns execution", "type": "core", "importance": 0.74},
            {"id": 5, "text": "Sandbox: 17 verified demo scenarios", "type": "capability", "importance": 0.66},
            {"id": 6, "text": "Hall caps: 200-line paint window, 8 blocks/column", "type": "tuning", "importance": 0.52},
        ]

    def call_logic(self, prompt):
        self.turn_count += 1
        prompt_lower = prompt.lower().strip()

        if prompt_lower.startswith("system:"):
            yield from self._stream(
                "System online. Simulation mode active — type /demo to see all "
                f"{len(CODING_SCENARIOS)} runnable sandbox scenarios."
            )
            return

        scenario = _pick_scenario(prompt_lower)
        if scenario:
            yield from self._code_scenario(scenario, prompt)
            return

        if any(k in prompt_lower for k in ("hello", "hi ", "hey", "greet")):
            yield from self._stream(
                "Hey. Simulation layer ready. Type /demo for the scenario list — "
                "try: 'estimate pi with monte carlo', 'benchmark matrix multiply', "
                "'regex lab', or 'async job demo'."
            )
            return

        yield from self._stream(
            f"Received: '{prompt}'. In simulation mode I can "
            "write and run scripts (try 'code a counter'), benchmark algorithms "
            "('matrix multiply', 'hash benchmark'), estimate pi, mine logs with "
            "regex, draw ASCII charts, or race async jobs. /demo lists everything."
        )

    def _code_scenario(self, key: str, prompt: str):
        sc = CODING_SCENARIOS[key]
        filename = f"{key}.py"

        yield from self._stream(
            f"Understood. I'll write a script for: {sc['desc']}.\n\n"
        )
        time.sleep(0.12)

        yield "```python\n" + sc["code"] + "```\n\n"
        time.sleep(0.15)

        yield (
            '<tool>{"name": "run_gen_code", '
            f'"args": ["{filename}"]}}</tool>'
        )
        time.sleep(0.12)

        yield "\n\n<tool_result>\n"
        yield f"[sandbox] $ python -u {filename}\n"
        yield f"[sandbox] cwd={SANDBOX_DIR}\n"
        yield "-" * 60 + "\n"

        exit_code = 0
        for tag, line in run_sandboxed(sc["code"], filename=filename, timeout=10.0):
            if tag == "exit":
                exit_code = line
                break
            if tag == "stderr":
                yield f"[stderr] {line}"
            else:
                yield line

        yield "-" * 60 + "\n"
        yield f"[sandbox] process exited with code {exit_code}\n"
        yield "</tool_result>\n\n"
        time.sleep(0.08)

        if exit_code == 0:
            yield from self._stream(
                f"Script `{filename}` executed successfully. "
                "Output verified against expected behavior. "
                "Artifact cached in sandbox for inspection."
            )
        else:
            yield from self._stream(
                f"Script `{filename}` exited with code {exit_code}. "
                "See traceback above. Root cause: division by zero (intentional). "
                "I can patch and re-run if you'd like."
            )

    def _stream(self, text):
        for word in text.split(" "):
            yield word + " "
            time.sleep(random.uniform(0.010, 0.018))

    def reset_chat(self):
        self.turn_count = 0

    def get_memories(self):
        return self._memories

    def dispatch_worker(self, name, task):
        if name == "vinci":
            target = self._run_vinci_task
        else:
            target = self._run_sage_task
        threading.Thread(target=target, args=(task,), daemon=True).start()

    def _run_vinci_task(self, task: str):
        push_hall_event("vinci", "task_start", task)
        self.vinci_busy = True
        try:
            push_hall_event("vinci", "stdout", "[VINCI] sandbox ready")
            time.sleep(0.12)

            push_hall_event(
                "vinci", "chunk",
                f"Analyzing task: '{task}'. Preparing isolated subprocess. "
            )
            time.sleep(0.15)

            key = _pick_scenario(task.lower()) or "primes"
            sc = CODING_SCENARIOS[key]
            filename = f"vinci_{key}.py"

            push_hall_event("vinci", "chunk", f"Writing {filename}.\n\n")
            tool_payload = json.dumps({
                "name": filename,
                "args": {
                    "path": os.path.join(SANDBOX_DIR, filename),
                    "lines": len(sc["code"].splitlines()),
                },
            })
            push_hall_event("vinci", "chunk", f"<tool>{tool_payload}</tool>")
            time.sleep(0.2)

            exec_payload = json.dumps({
                "name": filename,
                "args": [filename],
            })
            push_hall_event("vinci", "chunk", f"\n<tool>{exec_payload}</tool>")
            time.sleep(0.12)

            push_hall_event("vinci", "stdout", f"[sandbox] $ python -u {filename}")
            exit_code = 0
            for tag, line in run_sandboxed(sc["code"], filename=filename, timeout=8.0):
                if tag == "exit":
                    exit_code = line
                    break
                prefix = "[stderr] " if tag == "stderr" else ""
                push_hall_event("vinci", "stdout", f"{prefix}{line.rstrip()}")
                time.sleep(0.015)

            push_hall_event("vinci", "stdout", f"[sandbox] exited with code {exit_code}")
            time.sleep(0.15)

            if exit_code == 0:
                report_msg = (
                    f"Task '{task}' complete. Script {filename} ran cleanly "
                    f"in sandbox (exit 0). Output streamed to console."
                )
            else:
                report_msg = (
                    f"Task '{task}' finished with errors. {filename} exited {exit_code}. "
                    "Traceback preserved in sandbox logs."
                )
            push_hall_event("vinci", "report", report_msg)
            user_input_queue.put(f"system: [VINCI reports to LOGIC] {report_msg}")
        finally:
            self.vinci_busy = False
            push_hall_event("vinci", "task_end", "")

    def _run_sage_task(self, task: str):
        push_hall_event("sage", "task_start", task)
        self.sage_busy = True
        try:
            push_hall_event("sage", "stdout", "[SAGE] index online, 4 shards mounted")
            time.sleep(0.12)

            push_hall_event("sage", "chunk", f"Query: '{task}'. Decomposing into sub-queries. ")
            time.sleep(0.15)

            queries = [
                f"{task} overview",
                f"{task} implementation details",
                f"{task} benchmarks 2025",
            ]
            tool_payload = json.dumps({
                "name": "search_net",
                "args": queries,
            })
            push_hall_event("sage", "chunk", f"\n<tool>{tool_payload}</tool>")
            time.sleep(0.15)

            domains = ["arxiv.org", "github.com", "docs.python.org", "stackoverflow.com"]
            for i, q in enumerate(queries, 1):
                domain = random.choice(domains)
                latency = random.randint(80, 340)
                push_hall_event(
                    "sage", "stdout",
                    f"  [{i}/{len(queries)}] GET https://{domain}/search?q={q.replace(' ', '+')}"
                )
                time.sleep(latency / 1000.0)
                push_hall_event("sage", "stdout", f"        -> 200 OK ({latency}ms), 3 results parsed")
            time.sleep(0.15)

            push_hall_event("sage", "stdout", "  indexing 9 documents...")
            for i in range(1, 10):
                push_hall_event("sage", "stdout", f"    doc_{i:02d}.md  {random.randint(4, 88)} KB")
                time.sleep(0.025)

            push_hall_event("sage", "stdout", "  ranking by relevance (BM25)...")
            time.sleep(0.2)
            push_hall_event("sage", "stdout", "  top-3 selected, caching summaries")

            report_msg = (
                f"Web research completed for '{task}'. "
                f"3 sub-queries, 9 docs indexed, top-3 cached."
            )
            push_hall_event("sage", "report", report_msg)
            user_input_queue.put(f"system: [SAGE reports to LOGIC] {report_msg}")
        finally:
            self.sage_busy = False
            push_hall_event("sage", "task_end", "")


if not USE_REAL:
    logic_ai = SimulatedLogicAI()
    user_input_queue = queue.Queue()

    def load_last_summary(path=None):
        return "Simulation: previous session archived."

    def get_local_day_schedule():
        return f"Schedule for {datetime.now().strftime('%B %d, %Y')}: Standby."


# ══════════════════════════════════════════════════════════════════════════════
#  CUSTOM WIDGETS
# ══════════════════════════════════════════════════════════════════════════════


class RichLabel(Widget):
    def __init__(self, renderable, **kwargs):
        super().__init__(**kwargs)
        self._renderable = renderable

    def update(self, renderable):
        self._renderable = renderable
        self.refresh()

    def render(self):
        return self._renderable


class TopBar(Widget):
    """Single-line status strip: brand, mode, model, turns, agent pills, clock."""

    def on_mount(self) -> None:
        self.set_interval(TOPBAR_INTERVAL, self.refresh)

    def render(self) -> Text:
        app = self.app
        t = Text()
        t.append(" ◢ LOGIC", style="bold #e5e5e5")
        t.append(" AI ", style="bold #f5f5f5")
        if USE_REAL:
            t.append(" LIVE ", style="bold black on #d4d4d4")
        else:
            t.append(" SIM ", style="bold black on #e5e5e5")
        if backend_load_error:
            t.append(" ⚠fallback ", style="bold #ff5555")
        t.append("  ", style="")

        model = logic_model_name
        if USE_REAL and logic_ai is not None:
            model = getattr(getattr(logic_ai, "chat", None), "model", None) or logic_model_name
        turns = getattr(logic_ai, "turn_count", 0)
        t.append(f"{model}", style="#a3a3a3")
        t.append(f"  ·  turns {turns}", style="#737373")

        vinci_busy, sage_busy = app._agent_busy()
        t.append("   ", style="")
        v_style = "bold black on #e5e5e5" if vinci_busy else "bold #e5e5e5 on #181818"
        s_style = "bold black on #e5e5e5" if sage_busy else "bold #a3a3a3 on #181818"
        t.append(" VINCI ● " if vinci_busy else " VINCI ○ ", style=v_style)
        t.append(" ", style="")
        t.append(" SAGE ● " if sage_busy else " SAGE ○ ", style=s_style)

        clock = datetime.now().strftime("%H:%M:%S")
        width = self.size.width or 80
        gap = max(1, width - len(t) - len(clock) - 2)
        t.append(" " * gap, style="")
        t.append(clock, style="#737373")
        t.append(" ", style="")
        return t


class ChatInputArea(TextArea):
    """Multi-line prompt with chat-oriented keybindings."""

    BINDINGS = [
        Binding("enter", "submit_chat", "Send", show=False, priority=True),
        Binding("shift+enter", "insert_newline", "Newline", show=False, priority=True),
        Binding("alt+enter", "insert_newline", "Newline", show=False, priority=True),
        Binding("ctrl+a", "select_all", "Select all", show=False, priority=True),
        Binding(
            "alt+backspace,ctrl+w",
            "delete_word_left",
            "Delete word left",
            show=False,
            priority=True,
        ),
    ]

    def action_submit_chat(self) -> None:
        self.app.action_submit_chat()

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def on_key(self, event: events.Key) -> None:
        key = event.key or ""
        char = event.character or ""

        # Ctrl+J is no longer a newline binding; swallow it so it does not
        # fall through to the "enter" binding on terminals that send LF for it.
        if key == "ctrl+j" or char == "\n":
            event.prevent_default()
            event.stop()
            return

        if key in (
            "enter",
            "shift+enter",
            "alt+enter",
            "ctrl+a",
        ):
            return

        is_word_delete_alias = (
            key == "ctrl+h"
            or key == "ctrl+w"
            or char == "\x17"
        )
        if is_word_delete_alias:
            event.prevent_default()
            event.stop()
            try:
                self.action_delete_word_left()
            except Exception:
                pass
            return


class ChatRow(Horizontal):
    """Full-width wrapper for one ChatMessage."""

    def __init__(self, message: "ChatMessage", **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self.add_class(f"row-{message.sender.lower()}")

    def compose(self) -> ComposeResult:
        yield self.message


class ChatMessage(Widget):
    """One chat bubble. Plain text while streaming; full rich render on finalize.

    R3.1: caches the final Group so layout is not rebuilt on every parent refresh.
    Live path stays a single Text object (no Panels/Markdown) until finalize.
    """

    def __init__(self, sender: str, text: str = "", **kwargs):
        super().__init__(**kwargs)
        self.sender = sender
        self.text_content = text
        self._final = False
        self.timestamp = datetime.now().strftime("%H:%M:%S")
        self._t0 = time.monotonic()
        self.segments = [("text", text)] if sender != "LOGIC" and text else []
        self._last_refresh = 0.0
        self._cached_final = None  # Group cached after finalize

        self.add_class(f"msg-{sender.lower()}")

    def update_text(self, text: str) -> None:
        self.text_content = text
        self._cached_final = None
        self.refresh(layout=True)

    def set_segments(self, segments, force: bool = False) -> None:
        self.segments = segments
        self.text_content = " ".join(p for k, p in segments if k == "text")
        self._cached_final = None
        now = time.monotonic()
        if force or (now - self._last_refresh) >= CHAT_STREAM_MIN_INTERVAL:
            self._last_refresh = now
            # Always layout=True: growing tool stdout / open fences must expand
            # the bubble or the UI looks frozen until finalize.
            self.refresh(layout=True)

    # Line-anchored markers for a genuine tool failure. Prose inside a
    # successful search result that happens to mention "errors" no longer
    # trips the red bubble.
    _ERROR_LINE_STARTS = (
        "traceback (most recent call last):",
        "error:",
        "error.",
        "failed:",
        "failed.",
        "fatal:",
        "fatal error",
        "exception:",
        "response [4",
        "response [5",
        "timed out",
        "[stderr]",
        "script_error:",
        "screen_error:",
    )
    _EXC_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*error\s*:", re.I)
    _EXC_NAME_RE2 = re.compile(r"^[a-z_][a-z0-9_]*exception\s*:", re.I)
    _EXIT_CODE_RE = re.compile(r"\bprocess exited with code ([1-9]\d*)\b")

    @classmethod
    def _looks_like_error(cls, result_segments) -> bool:
        """True only for structural failure markers, not prose mentions."""
        for payload in result_segments:
            for line in str(payload).splitlines():
                s = line.strip()
                if not s:
                    continue
                low = s.lower()
                for p in cls._ERROR_LINE_STARTS:
                    if low.startswith(p):
                        return True
                if cls._EXC_NAME_RE.match(s) or cls._EXC_NAME_RE2.match(s):
                    return True
                if cls._EXIT_CODE_RE.search(s):
                    return True
        return False

    def finalize(self) -> None:
        self._final = True
        # Error bubbles get the red carbon edge: only when a tool RESULT
        # segment carries a structural failure marker. Prose that merely
        # mentions failure words does not count.
        result_segments = [
            p for k, p in self.segments if k in ("result", "result_open")
        ]
        if self._looks_like_error(result_segments):
            self.add_class("msg-error")
        self._cached_final = None  # force rebuild once
        self._last_refresh = 0.0  # ensure final paint is not throttled away
        self.refresh(layout=True)

    def _render_header(self) -> Text:
        t = Text()
        if self.sender == "YOU":
            t.append(" YOU ", style="bold black on #525252")
        elif self.sender == "LOGIC":
            t.append(" LOGIC ", style="bold black on #e5e5e5")
        else:
            t.append(" SYS ", style="bold black on #525252")

        t.append(f" {self.timestamp}", style="#525252")
        return t

    _LIVE_ERR_KEYS = ("error", "traceback", "exception", "failed", "fatal", "timed out")

    def _render_streaming(self):
        """Cheap live view: single Text, no Panels/Markdown, truncated tails."""
        body = Text()
        current_tool = "EXEC"
        for kind, payload in self.segments:
            if kind == "text":
                if payload:
                    # Cap streamed prose so a long reply doesn't rebuild a
                    # multi-thousand-character Text every 100 ms.
                    if len(payload) > 4_000:
                        payload = "…\n" + payload[-4_000:]
                    body.append(f"\n{payload}", style="#e5e5e5")
            elif kind in ("tool", "tool_open"):
                name, _, _ = parse_tool_json(payload)
                if name and name != "tool":
                    current_tool = name
                body.append_text(self._live_tool_view(kind, payload))
            elif kind in ("result", "result_open"):
                body.append_text(self._live_result_view(kind, payload, agent=current_tool))
        if not body.plain.strip():
            body.append("\n…", style="#737373")
        body.append(" ▌", style="bold #e5e5e5")
        return Group(self._render_header(), body)

    def _live_tool_view(self, kind: str, payload: str) -> Text:
        running = kind == "tool_open"
        color = "#e5e5e5"
        name, args, cmd = parse_tool_json(payload)
        t = Text()
        t.append(f"\n⚙ {'CALLING' if running else 'CALL'} ", style=f"bold {color}")
        t.append(name, style="bold #f5f5f5")
        if cmd and cmd not in ("builtin", name):
            t.append(f" ({cmd})", style="#a3a3a3")
        if running:
            t.append("  receiving args…", style="italic #737373")
        # Only preview args once the tool call is closed; open calls change
        # every token and the JSON is incomplete anyway.
        if not running:
            for line in self._args_preview_lines(args):
                t.append(f"\n  ▸ {line}", style="#d4d4d4")
        return t

    @staticmethod
    def _args_preview_lines(args, max_lines: int = 2) -> "list[str]":
        if isinstance(args, dict):
            items = [f"{k}: {v}" for k, v in args.items()]
        elif isinstance(args, list):
            items = [
                json.dumps(a, ensure_ascii=False) if isinstance(a, (dict, list)) else str(a)
                for a in args
            ]
        elif args not in (None, ""):
            items = str(args).splitlines()
        else:
            items = []
        if len(items) > max_lines:
            items = items[:max_lines] + [f"+{len(items) - max_lines} more"]
        return [i if len(i) <= 80 else i[:77] + "..." for i in items]

    def _live_result_view(self, kind: str, payload: str, agent: str = "EXEC") -> Text:
        """Tail-only live output — no per-line style scanning of the whole body."""
        running = kind == "result_open"
        color = _result_accent(agent)
        t = Text()
        if running:
            elapsed = time.monotonic() - getattr(self, "_t0", time.monotonic())
            t.append(f"\n▶ OUTPUT — live · {elapsed:4.1f}s", style=f"bold {color}")
        else:
            t.append("\n▶ OUTPUT", style=f"bold {color}")
        if not payload or not payload.strip():
            t.append("\n  (waiting for output…)", style="italic #737373")
            return t
        lines = payload.splitlines()
        total = len(lines)
        if total > LIVE_STREAM_OUTPUT_LINES:
            t.append(
                f"\n  … ({total - LIVE_STREAM_OUTPUT_LINES} earlier lines)",
                style="italic #525252",
            )
            lines = lines[-LIVE_STREAM_OUTPUT_LINES:]
        # Single style for the whole tail; only scan the last few lines for errors.
        tail_check = lines[-8:] if len(lines) > 8 else lines
        has_err = any(
            any(k in ln.lower() for k in self._LIVE_ERR_KEYS) for ln in tail_check
        )
        style = "#ff5555" if has_err else ("#e5e5e5" if running else "#a3a3a3")
        t.append("\n  " + "\n  ".join(lines), style=style)
        return t

    def _render_final(self):
        if self._cached_final is not None:
            return self._cached_final
        header = self._render_header()
        parts = [header]
        current_tool_name = "EXEC"
        has_body = False

        for kind, payload in self.segments:
            if kind == "text":
                if not payload:
                    continue
                has_body = True
                if "```" in payload:

                    balanced = balance_markdown_fences(payload)
                    parts.append(Markdown(balanced))
                else:
                    parts.append(Text(f"\n{payload}", style="#e5e5e5"))
            elif kind in ("tool", "tool_open"):
                has_body = True
                name, _, _ = parse_tool_json(payload)
                if name and name != "tool":
                    current_tool_name = name
                parts.append(Text("\n"))
                parts.append(build_tool_call_panel(payload, running=False))
            elif kind in ("result", "result_open"):
                has_body = True
                parts.append(Text("\n"))
                parts.append(build_chat_output_panel(payload, running=False, agent=current_tool_name))

        if not has_body:
            fallback = self.text_content.strip() if self.text_content else "..."
            parts.append(Text(f"\n{fallback}", style="#a3a3a3"))

        self._cached_final = Group(*parts)
        return self._cached_final

    def render(self):
        if self.sender == "YOU":
            header = self._render_header()
            msg_text = Text(f"\n{self.text_content}", style="#f5f5f5")
            return Group(header, msg_text)

        if self.sender == "LOGIC" and not self._final:
            return self._render_streaming()

        return self._render_final()


class AgentBlock(Widget):
    """Live streaming block inside the Logic Hall monitors.

    R3.1: cheap Text-only live path; full Panels only after set_done().
    Caches the final Group so idle refreshes are free.
    """

    def __init__(self, agent: str, task: str = "", **kwargs):
        super().__init__(**kwargs)
        self.agent = agent.upper()
        self.task_label = task
        self.timestamp = datetime.now().strftime("%H:%M:%S")
        self.started_at = time.monotonic()
        self.ended_at: float | None = None
        self.raw_stream = ""
        self.segments = []
        self.stdout_lines: list[str] = []
        self.report_text = ""
        self.is_done = False
        self._dirty = False
        self._segments_dirty = False
        self._last_refresh = 0.0
        self._parse_version = 0
        self._cached_final = None

        self.add_class(f"agent-{agent.lower()}")

    def _request_refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or (now - self._last_refresh) >= HALL_REFRESH_MIN_INTERVAL:
            self._last_refresh = now
            self._dirty = False
            # Always re-layout so live stdout lines expand the block height.
            self.refresh(layout=True)
        else:
            self._dirty = True

    def flush_if_dirty(self) -> None:
        if self._dirty:
            self._request_refresh(force=True)

    def append_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.raw_stream += chunk
        if len(self.raw_stream) > MAX_RAW_STREAM_CHARS:
            keep = MAX_RAW_STREAM_CHARS // 2
            self.raw_stream = (
                "…[earlier stream truncated]…\n" + self.raw_stream[-keep:]
            )
        self._segments_dirty = True
        self._cached_final = None
        self._request_refresh()

    def append_stdout(self, line: str) -> None:
        self.stdout_lines.append(line if line is not None else "")
        if len(self.stdout_lines) > MAX_STDOUT_KEEP:
            drop = len(self.stdout_lines) - MAX_STDOUT_KEEP
            self.stdout_lines = self.stdout_lines[drop:]
        self._cached_final = None
        self._request_refresh()

    def set_report(self, text: str) -> None:
        self.report_text = text
        self._cached_final = None
        self._request_refresh(force=True)

    def set_done(self) -> None:
        self.is_done = True
        self.ended_at = time.monotonic()
        if self._segments_dirty:
            self.segments = parse_stream(self.raw_stream)
            self._segments_dirty = False
        self._cached_final = None
        self._request_refresh(force=True)

    def _ensure_segments(self) -> None:
        if self._segments_dirty:
            self.segments = parse_stream(self.raw_stream)
            self._segments_dirty = False
            self._parse_version += 1

    def _render_header(self) -> Text:
        t = Text()
        color = "#e5e5e5" if self.agent == "VINCI" else "#a3a3a3"
        if self.is_done:
            t.append("● ", style="#d4d4d4")
        else:
            t.append("● ", style="#e5e5e5")
        t.append(f"{self.agent} ", style=f"bold {color}")
        t.append(f"{self.timestamp} ", style="#525252")
        elapsed = (self.ended_at or time.monotonic()) - self.started_at
        t.append(f"{elapsed:5.1f}s ", style="#525252")
        if self.task_label:
            label = self.task_label if len(self.task_label) <= 60 else self.task_label[:57] + "…"
            t.append(f"» {label}", style="bold #d4d4d4")
        return t

    def _render_live(self):
        """Text-only live view — no Panel/Markdown construction while streaming."""
        self._ensure_segments()
        body = Text()
        body.append_text(self._render_header())
        current_tool = "TOOL"
        for kind, payload in self.segments:
            if kind == "text":
                if not payload:
                    continue
                snippet = payload if len(payload) <= 2_000 else "…\n" + payload[-2_000:]
                body.append(f"\n{snippet}", style="#e5e5e5")
            elif kind in ("tool", "tool_open"):
                name, _, _ = parse_tool_json(payload)
                if name and name != "tool":
                    current_tool = name
                tag = "CALLING" if kind == "tool_open" else "CALL"
                body.append(f"\n⚙ {tag} ", style="bold #e5e5e5")
                body.append(current_tool, style="bold #f5f5f5")
            elif kind in ("result", "result_open"):
                color = _result_accent(current_tool)
                body.append(f"\n▶ OUTPUT", style=f"bold {color}")
                lines = payload.splitlines()
                if len(lines) > LIVE_STREAM_OUTPUT_LINES:
                    lines = lines[-LIVE_STREAM_OUTPUT_LINES:]
                if lines:
                    body.append("\n  " + "\n  ".join(lines), style="#a3a3a3")
        if self.stdout_lines:
            total = len(self.stdout_lines)
            window = self.stdout_lines[-MAX_STDOUT_RENDER:]
            body.append(
                f"\n▶ EXEC ({len(window)}/{total} lines)",
                style="bold #e0af68",
            )
            body.append("\n  " + "\n  ".join(window), style="#a3a3a3")
        if self.report_text:
            body.append(f"\n[REPORT] {self.report_text[:200]}", style="#e5e5e5")
        return body

    def _render_final(self):
        if self._cached_final is not None:
            return self._cached_final
        self._ensure_segments()
        header = self._render_header()
        parts = [header]

        current_tool = "TOOL"
        for kind, payload in self.segments:
            if kind == "text":
                if not payload:
                    continue
                if "```" in payload:
                    parts.append(Markdown(balance_markdown_fences(payload)))
                else:
                    parts.append(Text(f"\n{payload}", style="#e5e5e5"))
            elif kind in ("tool", "tool_open"):
                name, _, _ = parse_tool_json(payload)
                if name and name != "tool":
                    current_tool = name
                parts.append(Text("\n"))
                parts.append(build_tool_call_panel(payload, running=False))
            elif kind in ("result", "result_open"):
                parts.append(Text("\n"))
                parts.append(
                    build_chat_output_panel(
                        payload, running=False, agent=current_tool
                    )
                )

        if self.stdout_lines:
            total = len(self.stdout_lines)
            if total > MAX_STDOUT_RENDER:
                window = self.stdout_lines[-MAX_STDOUT_RENDER:]
                body = (
                    f"… showing last {MAX_STDOUT_RENDER} of {total} lines …\n"
                    + "\n".join(window)
                )
            else:
                body = "\n".join(self.stdout_lines)
            parts.append(Text("\n"))
            parts.append(
                build_chat_output_panel(
                    body,
                    running=False,
                    agent=f"{self.agent}:EXEC",
                )
            )

        if self.report_text:
            parts.append(Text("\n"))
            parts.append(build_report_panel(self.report_text, agent=self.agent))

        t_done = Text()
        t_done.append("\n  [DONE] ", style="bold black on #d4d4d4")
        t_done.append("Task concluded.", style="italic #737373")
        parts.append(t_done)

        self._cached_final = Group(*parts)
        return self._cached_final

    def render(self):
        if not self.is_done:
            return self._render_live()
        return self._render_final()


class StatusSpinner(Widget):
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._message = ""
        self._frame = 0
        self._timer = None

    def start(self, text: str) -> None:
        self._message = text
        if self._timer is None:
            self._timer = self.set_interval(SPINNER_INTERVAL, self._tick)
        self.refresh()

    def stop(self) -> None:
        self._message = ""
        self.refresh()

    def _tick(self) -> None:
        if self._message:
            self._frame = (self._frame + 1) % len(self.FRAMES)
            self.refresh()

    def render(self):
        if not self._message:
            return Text("")
        t = Text()
        t.append(f"{self.FRAMES[self._frame]} ", style="bold #e5e5e5")
        t.append(f"{self._message}", style="#a3a3a3")
        started = getattr(self.app, "_op_started", 0.0)
        if started:
            t.append(f" {time.monotonic() - started:.1f}s", style="#525252")
        return t


# ══════════════════════════════════════════════════════════════════════════════
#  PANELS (MEMORY & TOOLS)
# ══════════════════════════════════════════════════════════════════════════════


class MemoryPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield RichLog(id="memory-log", wrap=True, max_lines=500, markup=False)

    def refresh_memories(self) -> None:
        log = self.query_one("#memory-log", RichLog)
        log.clear()

        if USE_REAL:
            try:
                from logic_memory_manager import memorymanager

                all_mem = memorymanager.show_all()
                for line in all_mem:
                    log.write(Text(str(line), style="#a3a3a3"))
                if not all_mem:
                    log.write(Text("No stored memories retrieved.", style="italic #737373"))
            except Exception as e:
                log.write(Text(f"Memory access error: {e}", style="bold #ff5555"))
        else:
            for mem in logic_ai.get_memories():
                filled = int(mem["importance"] * 10)
                bar = "=" * filled + "-" * (10 - filled)
                p = Panel(
                    Text(mem["text"], style="bold #e5e5e5"),
                    title=f"[bold #e5e5e5]#{mem['id']} {mem['type'].upper()}[/] [{bar}]",
                    border_style="#333333",
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
                log.write(p)


class ToolPanel(Vertical):
    TOOLS = [
        ("search_net", "Web reference lookup and semantic parsing"),
        ("save", "Write verified code to logic_tools/ sandbox"),
        ("call_logic", "Return background results to primary thread"),
        ("```python```", "Sandboxed executor for model-written code"),
        ("<name>.py", "Run a whitelisted script from logic_tools/"),
        ("find_file.py", "Fast workspace path searching"),
        ("scrape_site.py", "DOM content and text extractor"),
        ("download_file.py", "2-step cached file downloader"),
    ]

    def compose(self) -> ComposeResult:
        table = Table(box=box.SIMPLE_HEAVY, expand=True, show_edge=False)
        table.add_column("Tool / Script", style="bold #e5e5e5", no_wrap=True)
        table.add_column("Description", style="#a3a3a3")
        for name, desc in self.TOOLS:
            table.add_row(name, desc)
        yield RichLabel(table)
        yield Static("Workspace Scripts (logic_tools/):", classes="subhead")
        yield RichLog(id="scripts-log", wrap=True, max_lines=200, markup=False)

    def refresh_scripts(self) -> None:
        log = self.query_one("#scripts-log", RichLog)
        log.clear()
        tools_dir = "logic_tools"
        if os.path.isdir(tools_dir):
            scripts = sorted(f for f in os.listdir(tools_dir) if f.endswith(".py"))
            for s in scripts:
                log.write(Text(f"  * {s}", style="#e5e5e5"))
            if not scripts:
                log.write(Text("  (no local scripts found)", style="italic #737373"))
        else:
            log.write(Text(f"  Folder '{tools_dir}' will be initialized when needed.", style="italic #737373"))


HELP_MARKDOWN = """\
### Keybindings
* **Esc** : Focus the chat input box
* **Ctrl + Q** : Exit application
* **Ctrl + 1..5** : Jump to Chat / Hall / Memory / Tools / Help
* **Enter** : Send message
* **Shift + Enter** : Insert newline *(requires a Kitty-protocol terminal:
* **Alt + Enter** : Insert newline *(works on every terminal — use this
  if Shift+Enter just sends on your setup)*
* **Ctrl + A** : Select all text in the input box
* **Alt + Backspace** / **Ctrl + W** : Delete previous word
* **Backspace** / **Ctrl + Backspace** : Delete one character

### Agent Directives (slash required)
* `/vinci <task>` : Dispatch to VINCI (OS/Code)
* `/sage <task>` : Dispatch to SAGE (Web/Research)
* `/both <task>` : Dispatch to both agents
* `/stop vinci` / `/stop sage` / `/stop both` : Interrupt sub-agents
* `/reset vinci` / `/reset sage` / `/reset both` : Wipe sub-agent memory

Plain text such as `vinci check logs` is sent to LOGIC as normal chat.

### Slash Commands
* `/clear` : Clear chat view
* `/compact` : Compact chat memory
* `/hall` : Switch to Logic Hall tab
* `/chat` : Switch back to the chat tab
* `/demo` : List runnable sandbox demos · `/demo <key>` : Run one (sim mode)
* `/help` : This documentation pane

### Architecture (R3)
* TUI is presentation-only. logic.py owns all execution, validation, and
  tool dispatch. Live subprocess output is streamed through the backend's
  `_line_sink` hook — no reimplementation of tools on the TUI side.

### Sandbox demos (simulation mode)
counter · fibonacci · primes · sorting · json_parse · http_client · buggy ·
csv_report · montecarlo · matrix_mult · word_freq · regex_lab · ascii_chart ·
tree_walk · hash_bench · async_demo · palindrome
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════


class LogicTUI(App):
    CSS = """
    /* ── Carbon & Graphite Palette ────────────────────── */
    * {
        scrollbar-background: #0a0a0a;
        scrollbar-color: #333333;
    }

    Screen {
        background: #0a0a0a;
        color: #e5e5e5;
    }

    #topbar {
        height: auto;
        min-height: 1;
        max-height: 2;
        dock: top;
        background: #141414;
        border-bottom: solid #262626;
        color: #e5e5e5;
        padding: 0 1;
    }

    #tabs {
        height: 1fr;
        background: #0a0a0a;
    }

    Tabs {
        background: #141414;
        height: 1;
    }

    Tab {
        padding: 0 2;
        color: #737373;
        background: #141414;
    }

    Tab:hover {
        color: #f5f5f5;
        background: #262626;
    }

    Tab.-active {
        color: #0a0a0a;
        background: #d4d4d4;
        text-style: bold;
    }

    TabPane {
        padding: 0 1;
        background: #0a0a0a;
    }

    #pane-chat {
        padding: 0;
    }

    #chat-scroll {
        width: 1fr;
        height: 1fr;
        padding: 1 2 1 2;
        overflow-x: hidden;
        overflow-y: auto;
        background: #0a0a0a;
    }

    ChatRow {
        width: 1fr;
        height: auto;
    }

    ChatRow.row-you {
        align-horizontal: right;
    }

    ChatMessage {
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        color: #e5e5e5;
        background: #0d0d0d;
        border-left: thick #262626;
    }

    ChatMessage.msg-you {
        width: auto;
        max-width: 78%;
        border-left: none;
        border-right: thick #525252;
        background: #181818;
    }

    ChatMessage.msg-logic {
        border-left: thick #e5e5e5;
        background: #101010;
        width: 1fr;
    }

    ChatMessage.msg-sys {
        border-left: thick #525252;
        background: #0d0d0d;
        width: 1fr;
    }

    ChatMessage.msg-error {
        border-left: thick #ff5555;
    }

    #bottom-deck {
        height: auto;
        dock: bottom;
        background: #141414;
        border-top: solid #333333;
        padding: 0 1;
    }

    #input-row {
        height: 4;
    }

    #prompt-glyph {
        width: 2;
        height: 4;
        content-align: center middle;
        color: #e5e5e5;
        text-style: bold;
        background: #0a0a0a;
        border: solid #333333;
        border-right: none;
    }

    #prompt-input {
        width: 1fr;
        height: 4;
        background: #0a0a0a;
        color: #f5f5f5;
        border: solid #333333;
        border-left: none;
    }

    #prompt-input:focus {
        border: solid #e5e5e5;
        border-left: none;
    }

    #send-btn {
        width: 10;
        min-width: 10;
        height: 4;
        margin-left: 1;
        border: none;
        background: #4d4d4d;
        color: #ffffff;
    }

    #send-btn:hover {
        background: #737373;
    }

    #hint-row {
        height: 1;
    }

    #dock-hint {
        width: 1fr;
        color: #525252;
    }

    #spinner-slot {
        width: auto;
    }

    #hall-panels {
        height: 1fr;
        width: 1fr;
    }

    .hall-box {
        width: 1fr;
        height: 1fr;
        border: round #333333;
        background: #141414;
        margin: 0 0 0 1;
        layout: vertical;
    }

    .hall-title {
        height: 1;
        width: 1fr;
        color: #0a0a0a;
        text-style: bold;
        text-align: center;
    }

    #vinci-title {
        background: #e5e5e5;
    }

    #sage-title {
        background: #a3a3a3;
    }

    .hall-scroll {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        overflow-x: hidden;
        overflow-y: auto;
        background: #0a0a0a;
    }

    AgentBlock {
        width: 1fr;
        height: auto;
        min-height: 3;
        margin-bottom: 1;
        padding: 1;
        background: #141414;
        color: #e5e5e5;
    }

    AgentBlock.agent-vinci {
        border-left: thick #e5e5e5;
    }

    AgentBlock.agent-sage {
        border-left: thick #a3a3a3;
    }

    .subhead {
        color: #e5e5e5;
        text-style: bold;
        margin: 1 0;
    }

    #memory-widget, #tools-widget {
        padding: 1;
    }
    """

    TITLE = "LOGIC AI"
    SUB_TITLE = "Carbon Terminal UI"
    AUTO_FOCUS = "#prompt-input"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "focus_input", "Focus Input"),
        ("ctrl+1", "switch_tab('pane-chat')", "Chat"),
        ("ctrl+2", "switch_tab('pane-hall')", "Hall"),
        ("ctrl+3", "switch_tab('pane-memory')", "Memory"),
        ("ctrl+4", "switch_tab('pane-tools')", "Tools"),
        ("ctrl+5", "switch_tab('pane-help')", "Help"),
    ]

    is_processing = reactive(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.active_agent_blocks = {"vinci": None, "sage": None}
        self._pending_prompts: list[str] = []
        self._op_started = 0.0
        self._last_op_s: float | None = None
        self._input_len = 0
        self._stream_state: "tuple[ChatMessage, str] | None" = None
        self._stream_chunk_at = 0.0

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")

        with TabbedContent(id="tabs"):
            with TabPane("Chat", id="pane-chat"):
                yield ScrollableContainer(id="chat-scroll")

            with TabPane("Logic Hall", id="pane-hall"):
                with Horizontal(id="hall-panels"):
                    with Vertical(classes="hall-box"):
                        yield Static(" VINCI · OS / CODE EXECUTION ", classes="hall-title", id="vinci-title")
                        yield ScrollableContainer(id="hall-vinci-scroll", classes="hall-scroll")
                    with Vertical(classes="hall-box"):
                        yield Static(" SAGE · WEB / RESEARCH ENGINE ", classes="hall-title", id="sage-title")
                        yield ScrollableContainer(id="hall-sage-scroll", classes="hall-scroll")

            with TabPane("Memory", id="pane-memory"):
                yield MemoryPanel(id="memory-widget")

            with TabPane("Tools", id="pane-tools"):
                yield ToolPanel(id="tools-widget")

            with TabPane("Help", id="pane-help"):
                with ScrollableContainer():
                    yield RichLabel(Markdown(HELP_MARKDOWN))

        with Vertical(id="bottom-deck"):
            with Horizontal(id="input-row"):
                yield Static(">", id="prompt-glyph")
                yield ChatInputArea(id="prompt-input")
                yield Button("SEND", variant="primary", id="send-btn")
            with Horizontal(id="hint-row"):
                yield Static(id="dock-hint")
                yield StatusSpinner(id="spinner-slot")

    def on_mount(self) -> None:
        self._update_hud()
        self.query_one(ToolPanel).refresh_scripts()

        self._ensure_hall_hooks()

        if backend_load_error:
            self._mount_chat_msg(
                "SYS",
                f"Warning: Failed to load logic.py backend ({backend_load_error}). Switched to Simulation.",
            )

        self._mark_busy_now()
        self._send_system_greeting()
        self.set_interval(HALL_DRAIN_INTERVAL, self._drain_hall_stream_queue)
        self.set_interval(0.25, self._background_poll)
        try:
            import threading as _threading
            _threading.Thread(target=self._voice_listen_loop, daemon=True).start()
        except Exception:
            pass

    def _ensure_hall_hooks(self) -> None:
        if not (USE_REAL and logic_hall_module):
            return
        workers = getattr(logic_hall_module, "workers", {}) or {}
        for w in workers.values():
            if getattr(w, "_tui_hooked", False):
                continue
            try:
                hook_worker_for_live_hall_stream(w)
                w._tui_hooked = True
            except Exception as e:
                sys.stderr.write(f"[WARN] hall hook failed for {getattr(w, 'name', '?')}: {e}\n")

    def _agent_busy(self) -> tuple[bool, bool]:
        if USE_REAL and logic_hall_module:
            workers = getattr(logic_hall_module, "workers", {}) or {}
            w_v = workers.get("vinci")
            w_s = workers.get("sage")
            return (
                w_v.is_busy() if w_v else False,
                w_s.is_busy() if w_s else False,
            )
        return (
            getattr(logic_ai, "vinci_busy", False),
            getattr(logic_ai, "sage_busy", False),
        )

    def _update_hud(self) -> None:
        self._refresh_hint()
        try:
            self.query_one("#topbar", TopBar).refresh()
        except Exception:
            pass

    def _refresh_hint(self) -> None:
        try:
            hint = self.query_one("#dock-hint", Static)
        except Exception:
            return
        t = Text()
        if self.is_processing:
            t.append(" logic is working — /stop halts agents · output keeps streaming ", style="#525252")
        else:
            t.append(" enter send · shift+enter / alt+enter newline · esc focus · ctrl+1-5 tabs · /help ", style="#525252")
        if self._pending_prompts:
            t.append(f"· {len(self._pending_prompts)} queued ", style="bold #e5e5e5")
        if self._last_op_s is not None:
            t.append(f"· last {self._last_op_s:.1f}s ", style="#333333")
        if self._input_len:
            t.append(f"· {self._input_len} ch ", style="#333333")
        hint.update(t)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if getattr(event.text_area, "id", None) == "prompt-input":
            self._input_len = len(event.text_area.text)
            self._refresh_hint()

    def action_focus_input(self):
        self.query_one("#prompt-input", ChatInputArea).focus()

    def action_switch_tab(self, pane_id: str) -> None:
        self._set_tab(pane_id)

    def _set_tab(self, pane_id: str):
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = pane_id
        if pane_id == "pane-memory":
            self.query_one(MemoryPanel).refresh_memories()
        elif pane_id == "pane-tools":
            self.query_one(ToolPanel).refresh_scripts()
        self.action_focus_input()

    def _chat(self) -> ScrollableContainer:
        return self.query_one("#chat-scroll", ScrollableContainer)

    def _chat_pinned(self) -> bool:
        try:
            c = self._chat()
            return (c.max_scroll_y - c.scroll_y) <= 2
        except Exception:
            return True

    def _scroll_chat_end(self, force: bool = False) -> None:
        if force or self._chat_pinned():
            try:
                self._chat().scroll_end(animate=False)
            except Exception:
                pass

    def _prune_chat(self) -> None:
        try:
            container = self._chat()
            children = list(container.children)
            excess = len(children) - MAX_CHAT_MESSAGES
            if excess <= 0:
                return
            removed = 0
            for child in children:
                if removed >= excess:
                    break
                target = getattr(child, "message", child)
                if isinstance(target, ChatMessage) and not target._final and target.sender == "LOGIC":
                    continue
                try:
                    child.remove()
                    removed += 1
                except Exception:
                    pass
        except Exception:
            pass

    def _mount_chat_msg(self, sender: str, text: str = "") -> ChatMessage:
        msg = ChatMessage(sender, text)
        self._chat().mount(ChatRow(msg))
        self._prune_chat()
        self._scroll_chat_end(force=False)
        return msg

    def _set_processing(self, value: bool) -> None:
        if value and not self.is_processing:
            self._op_started = time.monotonic()
        elif not value and self.is_processing and self._op_started:
            self._last_op_s = time.monotonic() - self._op_started
            self._op_started = 0.0
        self.is_processing = value
        self._refresh_hint()

    def _mark_busy_now(self) -> None:
        if not self.is_processing:
            self.is_processing = True
            self._op_started = time.monotonic()
            self._refresh_hint()

    def _queue_or_dispatch_prompt(self, prompt: str) -> None:
        if self.is_processing:
            self._pending_prompts.append(prompt)
            self._refresh_hint()
        else:
            # Normal user message → drop any stale script/tool results still in the queue.
            if (
                isinstance(prompt, str)
                and not prompt.startswith(("SCRIPT_RESULT:", "system:", "/"))
            ):
                _discard_pending_script_results()
            self._mark_busy_now()
            self._dispatch_prompt(prompt)

    @work(exclusive=True, thread=True, group="logic_stream")
    def _send_system_greeting(self) -> None:
        worker = get_current_worker()
        self.call_from_thread(self._set_processing, True)
        msg = self.call_from_thread(self._mount_chat_msg, "LOGIC", "")

        if getattr(logic_module_ref, "restored", False):
            self.call_from_thread(self._set_spinner, "Resuming...")
            prompt = (
                "system: SYSTEM RELOADED, any new code changes should work now"
            )
        else:
            self.call_from_thread(self._set_spinner, "Initializing system...")
            prompt = (
                "system: user is online. Greet briefly. "
                "If the schedule below is already covered in the previous summary, "
                "do not repeat those items — ask about the user's current activity "
                "or mood instead. "
                f"Previous session: {load_last_summary()} | "
                f"Schedule: {get_local_day_schedule()}"
            )

        try:
            self._stream_pipeline(msg, prompt, worker)
        finally:
            self.call_from_thread(self._set_processing, False)
            self.call_from_thread(self._set_spinner, "")
            if not USE_REAL:
                self.call_from_thread(self._mount_chat_msg, "SYS", _demo_list_text())
            self.call_from_thread(self._flush_pending_prompts)
    @work(exclusive=True, thread=True, group="logic_stream")
    def _dispatch_prompt(self, prompt: str) -> None:
        worker = get_current_worker()
        self.call_from_thread(self._set_processing, True)
        self.call_from_thread(self._set_spinner, "LOGIC is working...")
        msg = self.call_from_thread(self._mount_chat_msg, "LOGIC", "")

        if USE_REAL and _stop_voice:
            try:
                _stop_voice()
            except Exception:
                pass

        try:
            self._stream_pipeline(msg, prompt, worker)
            self.call_from_thread(self._update_hud)
        finally:
            self.call_from_thread(self._set_processing, False)
            self.call_from_thread(self._flush_pending_prompts)

    def _stream_pipeline(self, msg: ChatMessage, prompt: str, worker):
        buf = ""
        last_t = 0.0
        try:
            if logic_ai is None:
                buf = "[Stream Error: logic_ai is not available]"
            else:
                for chunk in logic_ai.call_logic(prompt):
                    if worker.is_cancelled:
                        buf += "\n[generation cancelled]"
                        break
                    buf += chunk if chunk is not None else ""
                    self._stream_state = (msg, buf)
                    now = time.monotonic()
                    if now - last_t >= CHAT_STREAM_MIN_INTERVAL:
                        self.call_from_thread(self._update_stream_chunk, msg, buf)
                        last_t = now
        except Exception as err:
            buf += f"\n[Stream Error: {err}]"

        self.call_from_thread(self._finalize_stream, msg, buf)
        self.call_from_thread(self._set_spinner, "")
        self._stream_state = None

    def _update_stream_chunk(self, msg: ChatMessage, buf: str, force: bool = False):
        if msg is None:
            return
        self._stream_chunk_at = time.monotonic()
        try:
            raw = buf or ""
            # Fast path: pure prose with no tool/result tags — skip full regex parse.
            # Note: match full open tags only (avoid false positives on prose).
            has_tags = "<" in raw and any(
                t in raw for t in (
                    "<tool>", "<tool_call>", "<function_call>",
                    "<tool_result>", "<tool_output>", "<exec_output>",
                    "<output>", "<result>",
                )
            )
            if not has_tags:
                segs = [("text", raw)] if raw else []
            else:
                segs = parse_stream(raw)
                if not segs and raw:
                    segs = [("text", raw)]
            msg.set_segments(segs, force=force)
            self._scroll_chat_end()
        except Exception as e:
            try:
                msg.set_segments(
                    [("text", (buf or "") + f"\n[render error: {e}]")], force=True
                )
            except Exception:
                pass

    def _finalize_stream(self, msg: ChatMessage, buf: str):
        if msg is None:
            return
        try:
            self._update_stream_chunk(msg, buf, force=True)
            msg.finalize()
            self._scroll_chat_end()
        except Exception:
            try:
                msg.update_text(buf or "[empty response]")
                msg.finalize()
            except Exception:
                pass

    def _set_spinner(self, msg: str):
        sp = self.query_one("#spinner-slot", StatusSpinner)
        sp.start(msg) if msg else sp.stop()

    def _prune_hall_blocks(self, container: ScrollableContainer) -> None:
        blocks = [c for c in container.children if isinstance(c, AgentBlock)]
        excess = len(blocks) - MAX_AGENT_BLOCKS_PER_COLUMN
        if excess <= 0:
            return
        removable = [b for b in blocks if getattr(b, "is_done", False)]
        if len(removable) < excess:
            removable = blocks
        for b in removable[:excess]:
            try:
                b.remove()
            except Exception:
                pass

    def _ensure_agent_block(self, agent: str, task: str = "") -> "AgentBlock | None":
        agent = agent.lower()
        if agent not in ("vinci", "sage"):
            return None
        try:
            container = self.query_one(f"#hall-{agent}-scroll", ScrollableContainer)
        except Exception:
            return None

        block = self.active_agent_blocks.get(agent)
        if block is not None and not getattr(block, "is_done", False):
            return block

        block = AgentBlock(agent=agent, task=str(task or ""))
        self.active_agent_blocks[agent] = block
        container.mount(block)
        self._prune_hall_blocks(container)
        container.scroll_end(animate=False)
        return block

    def _drain_hall_stream_queue(self) -> None:
        batched: dict[str, list[tuple[str, str]]] = {}
        drained = 0
        while drained < HALL_MAX_EVENTS_PER_TICK:
            try:
                agent, kind, payload = hall_stream_queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            agent = (agent or "").lower()
            if agent not in ("vinci", "sage"):
                continue
            batched.setdefault(agent, []).append((kind, payload if payload is not None else ""))

        touched: set[str] = set()

        for agent, events_list in batched.items():
            try:
                container = self.query_one(f"#hall-{agent}-scroll", ScrollableContainer)
            except Exception:
                continue

            for kind, payload in events_list:
                if kind == "task_start":
                    prev = self.active_agent_blocks.get(agent)
                    if prev is not None and not getattr(prev, "is_done", False):
                        prev.set_done()
                    block = AgentBlock(agent=agent, task=str(payload or ""))
                    self.active_agent_blocks[agent] = block
                    container.mount(block)
                    self._prune_hall_blocks(container)
                    touched.add(agent)
                    continue

                block = self.active_agent_blocks.get(agent)
                if block is None or getattr(block, "is_done", False):
                    block = self._ensure_agent_block(agent, task="(streaming)")
                    if block is None:
                        continue

                if kind == "chunk":
                    block.append_chunk(payload)
                elif kind == "stdout":
                    block.append_stdout(str(payload))
                elif kind == "report":
                    block.set_report(str(payload))
                elif kind == "task_end":
                    block.set_done()
                    self.active_agent_blocks[agent] = None
                touched.add(agent)

            if agent in touched:
                try:
                    block = self.active_agent_blocks.get(agent)
                    if block is not None:
                        block.flush_if_dirty()
                    container.scroll_end(animate=False)
                except Exception:
                    pass

        now = time.monotonic()
        for agent, block in list(self.active_agent_blocks.items()):
            if block is None:
                continue
            try:
                if getattr(block, "_dirty", False):
                    block.flush_if_dirty()
                # Soft repaint for the elapsed-time header only — no layout.
                elif not block.is_done and now - getattr(block, "_last_refresh", 0.0) >= 2.0:
                    block._request_refresh(force=False)
            except Exception:
                pass

        self._poll_worker_lines()

    def _poll_worker_lines(self) -> None:
        if not (USE_REAL and logic_hall_module):
            return
        workers = getattr(logic_hall_module, "workers", {}) or {}
        for key, w in workers.items():
            agent = key.lower()
            try:
                pending, done = w.poll()
            except Exception:
                continue

            hooked = getattr(w, "_tui_hooked", False)
            if hooked:
                pending = []

            if not pending and not done:
                continue

            block = self.active_agent_blocks.get(agent)
            if block is None or getattr(block, "is_done", False):
                if pending or (not hooked and w.is_busy()):
                    block = self._ensure_agent_block(agent, task="(live)")
            if block is None:
                if done and not hooked:
                    block = self._ensure_agent_block(agent, task="(done)")
                else:
                    for msg in done:
                        if "FINISHED" in msg or "STOPPED" in msg or "CRASHED" in msg:
                            prev = self.active_agent_blocks.get(agent)
                            if prev is not None:
                                prev.set_done()
                                self.active_agent_blocks[agent] = None
                    continue

            for line in pending:
                block.append_stdout(str(line))
            for msg in done:
                block.append_stdout(f"===== {msg} =====")
                if "FINISHED" in msg or "STOPPED" in msg or "CRASHED" in msg:
                    block.set_done()
                    self.active_agent_blocks[agent] = None
            try:
                if block is not None:
                    block.flush_if_dirty()
                container = self.query_one(f"#hall-{agent}-scroll", ScrollableContainer)
                container.scroll_end(animate=False)
            except Exception:
                pass

    def _flush_pending_prompts(self) -> None:
        if self.is_processing:
            return
        while self._pending_prompts:
            next_prompt = self._pending_prompts.pop(0)
            if not next_prompt:
                continue
            # Interrupting / follow-up user message wins over a pending SCRIPT_RESULT.
            if (
                isinstance(next_prompt, str)
                and not next_prompt.startswith(("SCRIPT_RESULT:", "system:", "/"))
            ):
                _discard_pending_script_results()
            self._mark_busy_now()
            self._dispatch_prompt(next_prompt)
            break

    def _enqueue_or_dispatch(self, injected: str) -> None:
        # SCRIPT_RESULT / THREAD results are already shown live via the sink.
        # Still feed them to Logic for the feedback loop. Optionally also mount
        # as SYS so you can inspect exactly what Logic receives.
        if isinstance(injected, str) and (
            injected.startswith("SCRIPT_RESULT:")
            or injected.startswith("system:[THREAD")
        ):
            if SHOW_LOGIC_FEEDBACK:
                self._mount_chat_msg("SYS", injected)
            self._queue_or_dispatch_prompt(injected)
            return
        self._mount_chat_msg("SYS", injected)
        self._queue_or_dispatch_prompt(injected)

    def _voice_listen_loop(self) -> None:
        """Daemon thread: while backend voice_mode is True, capture mic and
        push transcripts into user_input_queue for normal dispatch."""
        import time as _time
        get_voice_fn = getattr(logic_module_ref, "get_voice", None) if logic_module_ref else None
        if get_voice_fn is None:
            try:
                from user_voice import get_voice as get_voice_fn  # type: ignore
            except Exception:
                get_voice_fn = None
        if get_voice_fn is None or user_input_queue is None:
            return
        while True:
            try:
                mode_on = bool(get_voice_mode_fn()) if callable(get_voice_mode_fn) else False
            except Exception:
                mode_on = False
            if not mode_on:
                _time.sleep(0.25)
                continue
            try:
                text = get_voice_fn()
            except Exception:
                _time.sleep(0.5)
                continue
            if text:
                try:
                    user_input_queue.put(text)
                except Exception:
                    pass

    def _background_poll(self) -> None:
        if user_input_queue is not None:
            try:
                while True:
                    injected = user_input_queue.get_nowait()
                    # If the user already queued a normal follow-up, drop stale results.
                    if (
                        self._pending_prompts
                        and isinstance(injected, str)
                        and (
                            injected.startswith("SCRIPT_RESULT:")
                            or injected.startswith("system:[THREAD")
                        )
                    ):
                        continue
                    self._enqueue_or_dispatch(injected)
            except queue.Empty:
                pass

        mb = getattr(logic_module_ref, "monitor_bot", None) if logic_module_ref else None
        if mb is not None:
            try:
                injected = mb.get_injection()
            except Exception:
                injected = None
            if injected:
                self._enqueue_or_dispatch(
                    "system: [Messenger] Msg returned, send reply via "
                    "send_message_to_messenger.py directly, don't ask. "
                    f"Chat context:\n{injected}"
                )

        if not self.is_processing and self._pending_prompts:
            self._flush_pending_prompts()

        if self.is_processing and self._stream_state is not None:
            if time.monotonic() - self._stream_chunk_at >= 1.0:
                msg, buf = self._stream_state
                try:
                    self._update_stream_chunk(msg, buf, force=True)
                except Exception:
                    pass

    def execute_hall_command(self, cmd_text: str) -> None:
        raw = cmd_text.strip()
        if not raw:
            return

        cmd = raw.lstrip("/").strip()
        parts = cmd.split(" ", 1)
        action = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if action == "stop":
            target = arg.lower() if arg else "both"
            if USE_REAL and logic_hall_module:
                workers = getattr(logic_hall_module, "workers", {})
                targets = list(workers.keys()) if target == "both" else [target]
                for key in targets:
                    w = workers.get(key)
                    if w and w.stop():
                        push_hall_event(key, "stdout", f"[STOP] User issued stop signal to {key.upper()}.")
            self._mount_chat_msg("SYS", f"Stop signal sent to {target.upper()}")
            return

        if action == "reset":
            target = arg.lower() if arg else "both"
            if USE_REAL and logic_hall_module:
                workers = getattr(logic_hall_module, "workers", {})
                targets = list(workers.keys()) if target == "both" else [target]
                for key in targets:
                    w = workers.get(key)
                    if w:
                        w.reset_chat()
                        push_hall_event(key, "stdout", f"[RESET] Conversation memory cleared for {key.upper()}.")
            self._mount_chat_msg("SYS", f"History reset for {target.upper()}")
            return

        if action in ("vinci", "sage") and arg:
            self._dispatch_agent_task(action, arg)
            return

        if action == "both" and arg:
            self._dispatch_agent_task("vinci", arg)
            self._dispatch_agent_task("sage", arg)
            return

        self._mount_chat_msg(
            "SYS",
            f"Unrecognized Hall directive: '{cmd_text}'. Use '/vinci <task>' or '/sage <task>'",
        )

    def _dispatch_agent_task(self, agent_name: str, task: str) -> None:
        if USE_REAL and logic_hall_module:
            workers = getattr(logic_hall_module, "workers", {})
            w = workers.get(agent_name)
            if w:
                if w.is_busy():
                    self._mount_chat_msg("SYS", f"{agent_name.upper()} is currently busy with another task.")
                    return
                w.dispatch(task, sender="USER")
                self._mount_chat_msg("SYS", f"Task delegated directly to {agent_name.upper()}: '{task}'")
                return
        else:
            logic_ai.dispatch_worker(agent_name, task)
            self._mount_chat_msg("SYS", f"Simulation task delegated to {agent_name.upper()}: '{task}'")
            return

        self._mount_chat_msg("SYS", f"Could not locate agent '{agent_name}'.")

    def action_submit_chat(self) -> None:
        inp = self.query_one("#prompt-input", ChatInputArea)
        val = inp.text.strip()
        if not val:
            return
        is_cmd = val.startswith("/")
        if self.is_processing and not is_cmd:
            # Queue instead of hard-block so the user can interrupt / follow up.
            self._pending_prompts.append(val)
            self._mount_chat_msg("YOU", val)
            self._mount_chat_msg(
                "SYS",
                f"Queued ({len(self._pending_prompts)} waiting) — will run after current turn.",
            )
            inp.text = ""
            self._refresh_hint()
            return
        inp.text = ""
        self._process_command_or_chat(val)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self.action_submit_chat()

    def _process_command_or_chat(self, text: str):
        low = text.lower().strip()

        hall_cmds = (
            "/vinci", "/sage", "/both", "/stop", "/reset",
        )
        if low == "/vinci" or low == "/sage" or low == "/both":
            self._mount_chat_msg("SYS", f"Usage: {low} <task>")
            return
        if low in ("/stop", "/reset") or any(
            low.startswith(p + " ") or low.startswith(p + "\t") for p in hall_cmds
        ):
            self.execute_hall_command(text)
            return

        if text.startswith("/"):
            cmd = low
            if cmd == "/clear":
                for child in list(self._chat().children):
                    child.remove()
                self._mount_chat_msg("SYS", "Chat view cleared.")
            elif cmd == "/compact":
                if hasattr(logic_ai, "reset_chat"):
                    logic_ai.reset_chat()
                self._mount_chat_msg("SYS", "Conversation history compacted.")
                self._update_hud()
            elif cmd == "/v":
                if USE_REAL and callable(toggle_voice_mode_fn):
                    state = toggle_voice_mode_fn()
                else:
                    state = "simulated"
                self._mount_chat_msg("SYS", f"Voice mode: {'ON' if state is True else ('OFF' if state is False else state)}")
            elif cmd == "/hall":
                self._set_tab("pane-hall")
            elif cmd == "/chat":
                self._set_tab("pane-chat")
            elif cmd == "/help":
                self._set_tab("pane-help")
            elif cmd == "/demo" or cmd.startswith("/demo "):
                parts = cmd.split()
                if len(parts) == 1:
                    self._set_tab("pane-chat")
                    self._mount_chat_msg("SYS", _demo_list_text())
                else:
                    key = parts[1]
                    if USE_REAL:
                        self._mount_chat_msg("SYS", "/demo only runs the sandbox simulation (backend is live).")
                    elif key in CODING_SCENARIOS:
                        self._set_tab("pane-chat")
                        self._mount_chat_msg("YOU", f"/demo {key}")
                        self._queue_or_dispatch_prompt(f"run the {key} demo")
                    else:
                        self._mount_chat_msg(
                            "SYS",
                            f"Unknown demo '{key}'. Valid: {', '.join(CODING_SCENARIOS)}",
                        )
            else:
                if USE_REAL and callable(terminal_code_fn):
                    terminal_code_fn(text)
                    self._mount_chat_msg("SYS", f"Executed directive: {text}")
                else:
                    self._mount_chat_msg("SYS", f"Unknown directive '{text}'. Type /help.")
            return

        self._set_tab("pane-chat")
        self._mount_chat_msg("YOU", text)
        self._scroll_chat_end(force=True)

        self._queue_or_dispatch_prompt(text)

def _worker_main():
    LogicTUI().run()

def _supervisor_main():
    script = os.path.abspath(__file__)
    args = [sys.executable, script, '--worker'] + sys.argv[1:]
    while True:
        rc = subprocess.call(args)
        if rc != 42:
            break

if __name__ == "__main__":
    if '--worker' in sys.argv:
        _worker_main()
    else:
        _supervisor_main()