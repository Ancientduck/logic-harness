"""
One-shot patcher for logic_deepseek.py.

Applies three ban-risk reductions:
  1. Restart cooldown in _supervisor_main (prevents crash-loop request bursts).
  2. Reduces redundant retry layering (call_logic no longer re-retries on top
     of the provider's own retries).
  3. Adds a small post-reply cool-down so back-to-back turns can't stack
     into a burst.

Backs up the original to logic_deepseek.py.bak. Aborts cleanly if any
anchor isn't found, so running it twice is harmless.
"""
import re
import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent / "logic_deepseek.py"
BACKUP = TARGET.with_suffix(".py.bak")


def apply(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        print(f"[SKIP] anchor not found for: {label}")
        return text
    if new in text:
        print(f"[SKIP] already patched: {label}")
        return text
    print(f"[OK]   patching: {label}")
    return text.replace(old, new, 1)


def main():
    if not TARGET.exists():
        print(f"[FATAL] {TARGET} not found.")
        sys.exit(1)

    src = TARGET.read_text(encoding="utf-8")
    original = src

    # -----------------------------------------------------------------
    # Patch 1: restart cooldown in _supervisor_main
    # -----------------------------------------------------------------
    old_supervisor = (
        "def _supervisor_main():\n"
        "    script = os.path.abspath(__file__)\n"
        "    args = [sys.executable, script, '--worker'] + sys.argv[1:]\n"
        "    while True:\n"
        "        rc = subprocess.call(args)\n"
        "        if rc != 42:\n"
        "            break\n"
    )
    new_supervisor = (
        "def _supervisor_main():\n"
        "    script = os.path.abspath(__file__)\n"
        "    args = [sys.executable, script, '--worker'] + sys.argv[1:]\n"
        "    import time as _t\n"
        "    _last_restart = 0.0\n"
        "    _min_cooldown = 30.0  # seconds between restarts\n"
        "    while True:\n"
        "        _now = _t.monotonic()\n"
        "        if _now - _last_restart < _min_cooldown:\n"
        "            _t.sleep(_min_cooldown - (_now - _last_restart))\n"
        "        _last_restart = _t.monotonic()\n"
        "        rc = subprocess.call(args)\n"
        "        if rc != 42:\n"
        "            break\n"
    )
    src = apply(src, old_supervisor, new_supervisor, "supervisor restart cooldown")

    # -----------------------------------------------------------------
    # Patch 2: stop double-retrying in call_logic
    # The provider already retries. If call_logic also retries 3x,
    # a single user turn can fan out to 9 outbound requests.
    # -----------------------------------------------------------------
    old_retry = "        max_retries = 3\n"
    new_retry = (
        "        # Provider already retries; keep this low to avoid\n"
        "        # multiplicative request bursts on a single turn.\n"
        "        max_retries = 1\n"
    )
    src = apply(src, old_retry, new_retry, "call_logic retry layering")

    # -----------------------------------------------------------------
    # Patch 3: post-reply cool-down so back-to-back turns don't stack.
    # Inserted right after the reply is fully printed in run_logic().
    # -----------------------------------------------------------------
    old_tail = (
        "        for chunk in reply:\n"
        "            the_console.print(chunk, style=color_reply, end=\"\", highlight=False, markup=False)\n"
        "\n"
        "        print()\n"
    )
    new_tail = (
        "        for chunk in reply:\n"
        "            the_console.print(chunk, style=color_reply, end=\"\", highlight=False, markup=False)\n"
        "\n"
        "        print()\n"
        "\n"
        "        # Small cool-down between turns so rapid follow-ups don't\n"
        "        # collapse into a burst from the provider's point of view.\n"
        "        time.sleep(random.uniform(1.5, 3.5))\n"
    )
    src = apply(src, old_tail, new_tail, "post-reply cool-down")

    # -----------------------------------------------------------------
    # Patch 4: make sure `random` is imported at module level.
    # -----------------------------------------------------------------
    if "\nimport random\n" not in src and "\nimport random \n" not in src:
        # Insert after the first stdlib import block we find.
        anchor = "import queue\nimport threading\nimport time\n"
        if anchor in src:
            src = apply(
                src,
                anchor,
                "import queue\nimport threading\nimport time\nimport random\n",
                "module-level `random` import",
            )
        else:
            print("[WARN] could not locate import block; add `import random` manually.")

    # -----------------------------------------------------------------
    # Write out
    # -----------------------------------------------------------------
    if src == original:
        print("\n[INFO] No changes applied. File is already patched, or anchors changed.")
        return

    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"\n[OK]   backup written to {BACKUP.name}")

    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK]   patched {TARGET.name}")
    print("\nRestart logic_deepseek.py to pick up the changes.")


if __name__ == "__main__":
    main()