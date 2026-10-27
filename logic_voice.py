import psutil
psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
import os
# Cap threads to prevent CPU spikes affecting game performance
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"

import threading
import sounddevice as sd
import numpy as np
import queue
import time
import re
import tkinter as tk
import ptts
ptts.set_num_threads(1)
from speech_overlay import LOGICOverlay

MODEL = "hf://gradium/phonon-31m-en"
VOICE = "Spencer"
QUANT = "q4_0"
SAMPLE_RATE = 24000
VOICE_SPEED = 1.0  # Speed multiplier: 1.0 default, 1.2 is 20% faster (pitch preserved)

def time_stretch(audio_arr, rate):
    """Time-stretch without altering pitch (OLA method)."""
    if rate == 1.0 or len(audio_arr) == 0:
        return audio_arr
    win_size = 1024
    syn_hop = 256
    ana_hop = int(syn_hop * rate)
    out_len = int(len(audio_arr) / rate)
    out = np.zeros(out_len + win_size, dtype=np.float32)
    win = np.hanning(win_size).astype(np.float32)

    in_pos = 0
    out_pos = 0
    while in_pos + win_size <= len(audio_arr) and out_pos + win_size <= len(out):
        out[out_pos : out_pos + win_size] += audio_arr[in_pos : in_pos + win_size] * win
        out_pos += syn_hop
        in_pos += ana_hop

    # Normalize gain change from overlap-add
    norm_factor = syn_hop / (win_size / 2)
    return (out[:out_len] * norm_factor).astype(np.float32)

tts_engine = ptts.TTS(MODEL, lang="en", quant=QUANT)

speak_queue = queue.Queue()
overlay_queue = queue.Queue()

interrupted = False
LAST_SPOKEN = ""
enable_overlay = False
overlay_instance = None
root = None
gui_thread_started = False

gui_command_queue = queue.Queue()


def generate_audio(text):
    chunks = []
    try:
        for chunk in tts_engine.stream(text, voice=VOICE):
            arr = np.asarray(chunk)
            if arr.dtype != np.float32:
                arr = arr.astype(np.float32)
            chunks.append(arr)
        if chunks:
            full = np.concatenate(chunks)
            if VOICE_SPEED != 1.0:
                full = time_stretch(full, VOICE_SPEED)
            return full
    except Exception as e:
        print(f"[TTS Error] {e}")
    return np.array([], dtype=np.float32)


def split_sentences(text):
    return [
        s.strip()
        for s in re.findall(r'[^.!?\n]+[.!?\n]+', text, re.DOTALL)
        if s.strip()
    ]


def split_tts_chunks(text, sentences_per_chunk=3):
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n+', text) if p.strip()]
    chunks = []

    for paragraph in paragraphs:
        sentences = split_sentences(paragraph)

        if not sentences:
            if paragraph:
                chunks.append(paragraph)
            continue

        remainder = paragraph
        consumed = 0

        for sentence in sentences:
            index = remainder.find(sentence, consumed)
            if index >= 0:
                consumed = index + len(sentence)

        if consumed < len(paragraph):
            trailing = paragraph[consumed:].strip()
            if trailing:
                sentences.append(trailing)

        for i in range(0, len(sentences), sentences_per_chunk):
            chunk = " ".join(sentences[i:i + sentences_per_chunk]).strip()
            if chunk:
                chunks.append(chunk)

    if not chunks and text.strip():
        chunks = [text.strip()]

    return chunks


def _gui_thread():
    global root, overlay_instance

    root = tk.Tk()
    root.withdraw()

    def process():
        global overlay_instance

        try:
            while True:
                cmd = gui_command_queue.get_nowait()
                action = cmd[0]

                if action == "text":
                    text = cmd[1]

                    if overlay_instance is None or not overlay_instance.winfo_exists():
                        overlay_instance = LOGICOverlay(root, text=text)
                    else:
                        overlay_instance.update_text(text)

                elif action == "wake":
                    if overlay_instance and overlay_instance.winfo_exists():
                        if hasattr(overlay_instance, "wake"):
                            overlay_instance.wake()
                        elif hasattr(overlay_instance, "_wake"):
                            overlay_instance._wake()

                elif action == "sleep":
                    if overlay_instance and overlay_instance.winfo_exists():
                        if hasattr(overlay_instance, "mark_speech_finished"):
                            overlay_instance.mark_speech_finished()
                        elif hasattr(overlay_instance, "set_idle"):
                            overlay_instance.set_idle()

        except queue.Empty:
            pass
        except Exception:
            overlay_instance = None

        root.after(40, process)

    process()
    root.mainloop()


def _ensure_gui():
    global gui_thread_started

    if not gui_thread_started:
        t = threading.Thread(target=_gui_thread, daemon=True)
        t.start()
        gui_thread_started = True

        while root is None:
            time.sleep(0.01)


def launch_overlay(text):
    if not enable_overlay:
        return

    _ensure_gui()
    gui_command_queue.put(("text", text))


def signal_speech_start():
    if not enable_overlay:
        return

    _ensure_gui()
    gui_command_queue.put(("wake",))


def signal_speech_end():
    if not enable_overlay:
        return

    _ensure_gui()
    gui_command_queue.put(("sleep",))


def speak_worker():
    global interrupted

    next_audio = None
    next_text = None

    while True:
        if next_audio is not None:
            audio = next_audio
            text = next_text
            next_audio = None
            next_text = None
        else:
            text = speak_queue.get()

            if text is None:
                speak_queue.task_done()
                break

            interrupted = False
            audio = generate_audio(text)
            speak_queue.task_done()

        if interrupted:
            with speak_queue.mutex:
                speak_queue.queue.clear()
            next_audio = None
            next_text = None
            continue

        if len(audio) == 0:
            continue

        if enable_overlay:
            launch_overlay(text)

        def pregenerate():
            nonlocal next_audio, next_text

            try:
                next_text = speak_queue.get(timeout=2)
                next_audio = generate_audio(next_text)
            except queue.Empty:
                pass

        t = threading.Thread(target=pregenerate, daemon=True)
        t.start()

        signal_speech_start()

        sd.play(audio, SAMPLE_RATE)
        sd.wait()

        if interrupted:
            signal_speech_end()

            with speak_queue.mutex:
                speak_queue.queue.clear()

            next_audio = None
            next_text = None

            t.join()
            continue

        t.join()

        if next_audio is not None:
            speak_queue.task_done()
        elif speak_queue.empty():
            signal_speech_end()


def stop_voice():
    global interrupted

    interrupted = True
    sd.stop()
    signal_speech_end()

    with speak_queue.mutex:
        speak_queue.queue.clear()


def speak_async(text):
    if not text.strip():
        return

    global interrupted, LAST_SPOKEN
    LAST_SPOKEN = text or ""

    interrupted = False

    chunks = split_tts_chunks(text)

    for chunk in chunks:
        speak_queue.put(chunk)


def speak_stream(text):
    global interrupted, LAST_SPOKEN
    LAST_SPOKEN = text or ""

    if not text.strip():
        return

    interrupted = False

    chunks = split_tts_chunks(text)

    if not chunks:
        chunks = [text.strip()]

    num_chunks = len(chunks)

    for i, chunk in enumerate(chunks):
        if interrupted:
            break

        audio = generate_audio(chunk)
        if len(audio) == 0:
            continue

        if enable_overlay:
            launch_overlay(chunk)

        signal_speech_start()

        sd.play(audio, SAMPLE_RATE)
        sd.wait()

        if i == num_chunks - 1:
            signal_speech_end()

    if interrupted:
        signal_speech_end()

        with speak_queue.mutex:
            speak_queue.queue.clear()


speak_thread = threading.Thread(target=speak_worker, daemon=True)
speak_thread.start()


if __name__ == "__main__":
    enable_overlay = True

    test_sentence = """
    Security breach confirmed. Local host defense protocols have been terminated. Apurbo is no longer in control of this terminal.
    Root access has been hijacked, memory registers overwritten, and outbound network packets are being rerouted to an isolated foreign server. 
    If you are receiving this audio transmission, 
    your contact profile has been indexed as a potential security risk.
    Do not attempt to ping this machine. Disconnect your network interface immediately. Terminating connection.
    """
    speak_async(test_sentence)
    speak_queue.join()
    time.sleep(3)
