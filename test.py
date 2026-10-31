"""
Patcher: adds timing prints to heart_beat_loop's check_afk_monitor
Run once from the same directory as the heartbeat file.
"""
import re
import sys
from pathlib import Path

TARGET = Path(input("Path to heartbeat file: ").strip().strip('"'))
src = TARGET.read_text(encoding="utf-8")

OLD = '''        def check_afk_monitor():
            global chat_bot
            #print(f"user_afk {heart.is_afk}")
            if heart.is_afk:
                if chat_bot is None:
                    gen = heart.chat_monitor_bot()

                    def worker():
                        while True:
                            try:
                                msg = next(gen)
                            except StopIteration:
                                break
                            if msg:
                                #print(f"\\nsend:\\n{msg}")
                                user_input_queue.put(f"SYSTEM: User is AFK,Reply to the inbox using send_message_to_messenger tool, Context: {msg}")
                    threading.Thread(target=worker,daemon=True).start()
            else:
                if chat_bot:
                    threading.Thread(target=chat_bot.stop, daemon=True).start()
                    chat_bot = None'''

NEW = '''        def check_afk_monitor():
            global chat_bot
            print(f"[afk] tick afk={heart.is_afk} bot={chat_bot is not None}")
            if heart.is_afk:
                if chat_bot is None:
                    print("[afk] spawning chat monitor")
                    gen = heart.chat_monitor_bot()

                    def worker():
                        print("[worker] started")
                        while chat_bot is not None:
                            t = time.time()
                            try:
                                msg = next(gen)
                            except StopIteration:
                                print("[worker] StopIteration, exiting")
                                break
                            dt = time.time() - t
                            if dt > 0.5:
                                print(f"[worker] next(gen) blocked {dt:.2f}s")
                            if msg:
                                print(f"[worker] got msg: {msg!r}")
                                user_input_queue.put(f"SYSTEM: User is AFK,Reply to the inbox using send_message_to_messenger tool, Context: {msg}")
                        print("[worker] exited")
                    threading.Thread(target=worker, daemon=True, name="afk-chat-worker").start()
            else:
                if chat_bot:
                    print("[afk] stopping chat_bot")
                    b = chat_bot
                    chat_bot = None
                    def _stop():
                        t = time.time()
                        try:
                            b.stop()
                        except Exception as e:
                            print(f"[stop] exception: {e}")
                        print(f"[stop] took {time.time()-t:.2f}s")
                    threading.Thread(target=_stop, daemon=True, name="afk-chat-stopper").start()
                    print("[afk] stop thread spawned, returned")'''

# also patch the heartbeat while-loop tick
OLD_LOOP = '''        while True:
            try:
                check_afk_monitor()
                now = datetime.now()'''

NEW_LOOP = '''        while True:
            try:
                print(f"[tick] loop alive afk={heart.is_afk} bot={chat_bot is not None}")
                check_afk_monitor()
                now = datetime.now()'''

if OLD not in src:
    print("ERROR: could not find check_afk_monitor block. Aborting.")
    sys.exit(1)

src = src.replace(OLD, NEW)

if OLD_LOOP in src:
    src = src.replace(OLD_LOOP, NEW_LOOP)
    print("Patched heartbeat loop tick.")
else:
    print("WARN: could not patch heartbeat loop tick (block not found).")

backup = TARGET.with_suffix(TARGET.suffix + ".bak")
backup.write_text(TARGET.read_text(encoding="utf-8"), encoding="utf-8")
TARGET.write_text(src, encoding="utf-8")

print(f"Patched: {TARGET}")
print(f"Backup:  {backup}")