import threading
import sounddevice as sd
import numpy as np
import queue
import time
import re
import tkinter as tk
from piper import PiperVoice, SynthesisConfig
from speech_overlay import LOGICOverlay

PIPER_CMD = "piper"


# MODEL = "logic_voices/miro/en_GB-miro.onnx"
# CONFIG = "logic_voices/miro/en_GB-miro.onnx.json"
# SPEAKER = 0

MODEL = "logic_voices/main_voice/en_US-libritts_r-medium.onnx"
CONFIG = "logic_voices/main_voice/en_US-libritts_r-medium.onnx.json"
SPEAKER = 217


voice = PiperVoice.load(MODEL, CONFIG)
SAMPLE_RATE = voice.config.sample_rate
synthesis_config = SynthesisConfig(speaker_id=SPEAKER)

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
    for chunk in voice.synthesize(text, synthesis_config):
        chunks.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
    return np.concatenate(chunks) if chunks else np.array([], dtype=np.int16)


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
    time.sleep(30)