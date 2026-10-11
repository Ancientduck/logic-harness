# Valheim Save Parser

Description: Python script pattern to read and parse Valheim character files (.fch) from `AppData\LocalLow\IronGate\Valheim\characters_local` to extract inventory items, weapons, armor, and stats using regex/binary scanning.

Procedure:
1. Locate character files in `%USERPROFILE%\AppData\LocalLow\IronGate\Valheim\characters_local\*.fch` or via Steam userdata path if local folders are empty.
2. Read file in binary mode (`rb`).
3. Use `re.findall(b'[a-zA-Z0-9_]{3,30}', data)` to extract valid item strings and game IDs.
4. Filter matching keywords (e.g., `item_sword_iron`, `item_mace_iron`, etc.) to determine user gear and inventory contents without manual input.
