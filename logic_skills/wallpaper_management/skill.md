# Wallpaper Management & Curation

## Procedure
1. **Backup Current Wallpaper**: Before changing, save the current wallpaper path from `HKEY_CURRENT_USER\Control Panel\Desktop` (Wallpaper key) to a cache file so it can be reverted instantly.
2. **Fetch via API / Metadata**: Query wallhaven.cc or Unsplash API with specific tags, keeping track of original filenames and resolution.
3. **Apply Wallpaper**: Use ctypes `SystemParametersInfoW` to set the desktop background.
4. **Revert**: If user dislikes the wallpaper, read the backed-up previous wallpaper path and re-apply it immediately via `SystemParametersInfoW` without searching local history unnecessarily.

## Python Reference Code
```python
import ctypes
import os
import winreg

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

def get_current_wallpaper():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Control Panel\\Desktop", 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, "Wallpaper")
        winreg.CloseKey(key)
        return val
    except Exception:
        return ""

def set_wallpaper(path):
    if not os.path.exists(path):
        return False
    current = get_current_wallpaper()
    if current:
        with open(os.path.expanduser("~\\Desktop\\.last_wallpaper_path.txt"), "w") as f:
            f.write(current)

    ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    return True

def revert_wallpaper():
    backup_file = os.path.expanduser("~\\Desktop\\.last_wallpaper_path.txt")
    if os.path.exists(backup_file):
        with open(backup_file, "r") as f:
            prev_path = f.read().strip()
        if prev_path and os.path.exists(prev_path):
            ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, prev_path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
            return True
    return False
```
