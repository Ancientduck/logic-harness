
"""Patch LOGIC's TUI to forward AFK-return alerts promptly."""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


START_RE = re.compile(
    r"(?m)^    def _heartbeat_loop\(self\) -> None:\s*$"
)
END_RE = re.compile(
    r"(?m)^    def _voice_listen_loop\(self\) -> None:\s*$"
)


REPLACEMENT = '''    def _heartbeat_loop(self) -> None:
        """Forward presence alerts promptly; run slower pulse checks separately."""
        if not USE_REAL or user_input_queue is None:
            return

        try:
            from logic_heartbeat import Heart
        except Exception as e:
            sys.stderr.write(f"[TUI] heartbeat import failed: {e}\\\\n")
            return

        # TEST SETTINGS — restore AFK_THRESHOLD_SECONDS to 600 for production.
        TEST_INTERVAL_SECONDS = 30
        AFK_THRESHOLD_SECONDS = 10
        PRESENCE_POLL_SECONDS = 0.25

        try:
            heart = Heart(
                afk_threshold_seconds=AFK_THRESHOLD_SECONDS
            )
        except Exception as e:
            sys.stderr.write(f"[TUI] heartbeat init failed: {e}\\\\n")
            return

        # Poll presence independently of beat(), which may take a while.
        def _forward_presence_alerts() -> None:
            while True:
                try:
                    presence = heart.pop_presence_alert()
                    if presence:
                        user_input_queue.put(f"SYSTEM:{presence}")
                except Exception as e:
                    sys.stderr.write(
                        f"[TUI] presence alert forwarding error: {e}\\\\n"
                    )

                time.sleep(PRESENCE_POLL_SECONDS)

        try:
            import threading

            threading.Thread(
                target=_forward_presence_alerts,
                daemon=True,
                name="logic-presence-alert-forwarder",
            ).start()
        except Exception as e:
            sys.stderr.write(
                f"[TUI] presence forwarder start failed: {e}\\\\n"
            )
            return

        # Pulse checks continue on their own slower schedule.
        while True:
            try:
                report = heart.beat()

                if report:
                    formatted_report = "\\\\n\\\\n".join(
                        f"[{sec}]\\\\n{text}"
                        for sec, text in report.items()
                    )
                    user_input_queue.put(
                        f"SYSTEM: Pulse Check Report:\\\\n{formatted_report}"
                    )

            except Exception as e:
                sys.stderr.write(f"[TUI] heartbeat error: {e}\\\\n")

            time.sleep(TEST_INTERVAL_SECONDS)

'''


def choose_target() -> Path:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1]).expanduser()

        if target.is_dir():
            target = target / "logic_tui.py"

        if not target.is_file():
            raise FileNotFoundError(
                f"TUI file not found: {target}"
            )

        return target

    candidates = [
        Path.cwd() / "logic_tui.py",
        Path.cwd() / "logic_tui(1).py",
        Path(r"D:\Ai\logic\logic_tui.py"),
    ]

    for target in candidates:
        if target.is_file():
            return target

    raise FileNotFoundError(
        "Could not find logic_tui.py. Run this script from "
        r"D:\Ai\logic or pass the full file path."
    )


def main() -> int:
    try:
        target = choose_target()
        original = target.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 2

    if "logic-presence-alert-forwarder" in original:
        print("[INFO] Presence-forwarding patch already appears installed.")
        print(f"File: {target}")
        return 0

    starts = list(START_RE.finditer(original))

    if len(starts) != 1:
        print(
            "[FAIL] Expected exactly one _heartbeat_loop definition; "
            f"found {len(starts)}. No changes made."
        )
        print(f"File checked: {target}")
        return 3

    start_match = starts[0]

    ends = list(
        END_RE.finditer(original, start_match.end())
    )

    if not ends:
        print(
            "[FAIL] Could not find _voice_listen_loop after "
            "_heartbeat_loop. No changes made."
        )
        return 3

    start = start_match.start()
    end = ends[0].start()
    old_block = original[start:end]

    if (
        "pop_presence_alert()" not in old_block
        or "heart.beat()" not in old_block
    ):
        print(
            "[FAIL] The heartbeat function does not match the expected "
            "source. No changes made."
        )
        return 3

    patched = original[:start] + REPLACEMENT + original[end:]

    try:
        compile(patched, str(target), "exec")
    except SyntaxError as exc:
        print(
            f"[FAIL] Patched code has a syntax error at line "
            f"{exc.lineno}: {exc}"
        )
        print("No changes made.")
        return 4

    backup = target.with_name(
        target.name
        + ".bak_afk_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    try:
        shutil.copy2(target, backup)

        # Preserve the original file's newline style.
        with target.open(
            "w", encoding="utf-8", newline=""
        ) as file:
            file.write(patched)

        # Verify the actual file written to disk.
        actual = target.read_text(encoding="utf-8")
        compile(actual, str(target), "exec")

    except Exception as exc:
        print(f"[FAIL] Could not write or verify patch: {exc}")
        print(f"Original backup: {backup}")

        try:
            if backup.exists():
                shutil.copy2(backup, target)
                print("[INFO] Original file restored from backup.")
        except Exception as restore_exc:
            print(f"[WARN] Restore failed: {restore_exc}")

        return 5

    print("[OK] Patch applied.")
    print("[OK] On-disk Python syntax check passed.")
    print(f"Target : {target}")
    print(f"Backup : {backup}")
    print()
    print("Test settings:")
    print("  AFK threshold: 10 seconds")
    print("  Presence polling: 0.25 seconds")
    print("  Pulse checks: every 30 seconds")
    print()
    print("Next: launch LOGIC, leave it idle for over 10 seconds,")
    print("then move the mouse or press a key.")
    print("Syntax verification does not prove live alert delivery.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())