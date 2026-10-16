"""
Patch logic_tui.py — fix tool wrappers that were turned into generators.

call_logic does handler(args) without iterating, so any wrapper that uses
`yield` / `yield from` never runs. Same bug as run_gen_code, for:

  - search_net
  - check_screen
  - gen_code

Run from the same folder as logic_tui.py:
    python patch_tui_tools.py
"""

from __future__ import annotations

import pathlib
import re
import sys

TARGET = pathlib.Path(__file__).resolve().parent / "logic_tui.py"

OLD_SEARCH = """    _orig_search_net = getattr(ai_instance, "search_net", None)
    if _orig_search_net is not None:
        def tui_search_net(args):
            quest = ""
            if isinstance(args, list) and args:
                quest = str(args[0])
            elif args is not None:
                quest = str(args)
            yield (
                "\\n\\n<tool_result>\\n"
                f"[searching web: {quest}...]\\n"
                "</tool_result>\\n\\n"
            )
            yield from _orig_search_net(args)
        ai_instance.search_net = tui_search_net"""

NEW_SEARCH = """    _orig_search_net = getattr(ai_instance, "search_net", None)
    if _orig_search_net is not None:
        def tui_search_net(args):
            # call_logic invokes handlers without iterating — must NOT be a generator
            return _orig_search_net(args)
        ai_instance.search_net = tui_search_net"""

OLD_GEN = """    _orig_gen_code = getattr(ai_instance, "gen_code", None)
    if _orig_gen_code is not None:
        def tui_gen_code(args):
            yield (
                "\\n\\n<tool_result>\\n"
                "[gen_code] request sent to code model (groq)...\\n"
                "</tool_result>\\n\\n"
            )
            yield from _orig_gen_code(args)
        ai_instance.gen_code = tui_gen_code"""

NEW_GEN = """    _orig_gen_code = getattr(ai_instance, "gen_code", None)
    if _orig_gen_code is not None:
        def tui_gen_code(args):
            # call_logic invokes handlers without iterating — must NOT be a generator
            return _orig_gen_code(args)
        ai_instance.gen_code = tui_gen_code"""

OLD_SCREEN = """    _orig_check_screen = getattr(ai_instance, "check_screen", None)
    if _orig_check_screen is not None:
        def tui_check_screen(args):
            yield (
                "\\n\\n<tool_result>\\n"
                "[screen] capturing and analyzing screenshot...\\n"
                "</tool_result>\\n\\n"
            )
            yield from _orig_check_screen(args)
        ai_instance.check_screen = tui_check_screen"""

NEW_SCREEN = """    _orig_check_screen = getattr(ai_instance, "check_screen", None)
    if _orig_check_screen is not None:
        def tui_check_screen(args):
            # call_logic invokes handlers without iterating — must NOT be a generator
            return _orig_check_screen(args)
        ai_instance.check_screen = tui_check_screen"""


def _replace(src, old, new, label):
    if old in src:
        return src.replace(old, new, 1), True
    if new in src:
        print(f"[SKIP] already patched: {label}")
        return src, False
    # flexible whitespace
    pattern = re.sub(r"[ \t]+", r"[ \\t]+", re.escape(old))
    m = re.search(pattern, src)
    if m:
        return src[: m.start()] + new + src[m.end() :], True
    print(f"[WARN] pattern not found: {label}")
    return src, False


def main():
    if not TARGET.exists():
        print(f"[ERROR] {TARGET.name} not found next to this script.")
        print(f"        Expected: {TARGET}")
        return 1

    src = TARGET.read_text(encoding="utf-8")
    original = src
    changes = 0

    for label, old, new in [
        ("search_net", OLD_SEARCH, NEW_SEARCH),
        ("gen_code", OLD_GEN, NEW_GEN),
        ("check_screen", OLD_SCREEN, NEW_SCREEN),
    ]:
        src, ok = _replace(src, old, new, label)
        if ok:
            changes += 1
            print(f"[OK] patched {label}")

    if src == original:
        print("\nNo changes written (nothing matched or already up to date).")
        return 0

    backup = TARGET.with_suffix(".py.bak_tools")
    backup.write_text(original, encoding="utf-8")
    TARGET.write_text(src, encoding="utf-8")
    print(f"\n[DONE] {changes} change(s) applied.")
    print(f"       Backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())