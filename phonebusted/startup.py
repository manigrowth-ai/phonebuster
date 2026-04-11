"""
startup.py - Cross-platform startup entry + first-run setup

Windows:  Registry key HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
          Desktop shortcut via PowerShell (.lnk)

macOS:    LaunchAgent plist at ~/Library/LaunchAgents/com.phonebusted.plist
          No desktop shortcut (not needed on Mac)

First-run flag: ~/.phonebusted/first_run_done.txt
  - Desktop shortcut (Windows): checked + recreated EVERY launch (if missing)
  - Startup entry: set on FIRST launch only
"""

import os
import sys
import subprocess
from pathlib import Path

from debug import log

_FIRST_RUN   = Path.home() / ".phonebusted" / "first_run_done.txt"
_PLIST_LABEL = "com.phonebusted"
_PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"

# Windows-only constants — imported only on Windows to avoid import errors on Mac
if sys.platform == "win32":
    import winreg
    _REG_PATH  = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _REG_VALUE = "PhoneBusted"


# ── Exe path ──────────────────────────────────────────────────────────────────

def get_exe_path() -> str:
    """
    Returns the path to the running executable.
    - Frozen (PyInstaller .exe / .app): sys.executable
    - Dev (python main.py):             absolute path to main.py
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


# ── Startup: public API ───────────────────────────────────────────────────────

def get_startup() -> bool:
    """Return True if the startup entry exists for this app."""
    if sys.platform == "win32":
        return _win_get_startup()
    elif sys.platform == "darwin":
        return _mac_get_startup()
    return False


def set_startup(enabled: bool) -> bool:
    """Add or remove the startup entry. Returns True on success."""
    if sys.platform == "win32":
        return _win_set_startup(enabled)
    elif sys.platform == "darwin":
        return _mac_set_startup(enabled)
    return False


# ── Windows: registry ─────────────────────────────────────────────────────────

def _win_get_startup() -> bool:
    """Return True if the registry startup entry exists."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, _REG_VALUE)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception as exc:
        log(f"[Startup] get_startup error: {exc}")
        return False


def _win_set_startup(enabled: bool) -> bool:
    """Add or remove the Windows registry startup entry."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            if enabled:
                winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ, get_exe_path())
                log(f"[Startup] Registry entry added: {get_exe_path()}")
            else:
                try:
                    winreg.DeleteValue(key, _REG_VALUE)
                    log("[Startup] Registry entry removed.")
                except FileNotFoundError:
                    pass   # already gone
            return True
        finally:
            winreg.CloseKey(key)
    except Exception as exc:
        log(f"[Startup] set_startup error: {exc}")
        return False


# ── macOS: LaunchAgent plist ──────────────────────────────────────────────────

def _mac_get_startup() -> bool:
    """Return True if the LaunchAgent plist exists."""
    return _PLIST_PATH.exists()


def _mac_set_startup(enabled: bool) -> bool:
    """Write or remove the LaunchAgent plist and register it with launchctl."""
    try:
        if enabled:
            _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            exe = get_exe_path()
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
            _PLIST_PATH.write_text(plist, encoding="utf-8")
            subprocess.run(
                ["launchctl", "load", str(_PLIST_PATH)],
                capture_output=True,
            )
            log(f"[Startup] LaunchAgent created: {_PLIST_PATH}")
        else:
            if _PLIST_PATH.exists():
                subprocess.run(
                    ["launchctl", "unload", str(_PLIST_PATH)],
                    capture_output=True,
                )
                _PLIST_PATH.unlink()
                log("[Startup] LaunchAgent removed.")
        return True
    except Exception as exc:
        log(f"[Startup] macOS set_startup error: {exc}")
        return False


# ── Desktop shortcut (Windows only) ──────────────────────────────────────────

def create_desktop_shortcut() -> bool:
    """Create PhoneBusted.lnk on the Windows Desktop via PowerShell."""
    if sys.platform != "win32":
        return False

    exe      = get_exe_path()
    desktop  = Path.home() / "Desktop"
    shortcut = desktop / "PhoneBusted.lnk"

    # Escape backslashes for PowerShell string
    exe_ps = exe.replace("\\", "\\\\")
    lnk_ps = str(shortcut).replace("\\", "\\\\")

    ps = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{lnk_ps}"); '
        f'$s.TargetPath = "{exe_ps}"; '
        f'$s.Description = "PhoneBusted - Webcam Phone Detection"; '
        f'$s.Save()'
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            log(f"[Startup] Desktop shortcut created: {shortcut}")
            return True
        else:
            log(f"[Startup] Shortcut PowerShell error: "
                f"{result.stderr.decode(errors='replace')}")
            return False
    except Exception as exc:
        log(f"[Startup] create_desktop_shortcut error: {exc}")
        return False


def ensure_desktop_shortcut() -> None:
    """
    Windows: check if the Desktop shortcut exists — create if missing.
    macOS:   no-op (not needed on Mac).
    """
    if sys.platform != "win32":
        return

    desktop  = Path.home() / "Desktop"
    shortcut = desktop / "PhoneBusted.lnk"

    if shortcut.exists():
        log("[Startup] Desktop shortcut already exists.")
        return

    log("[Startup] Desktop shortcut missing — creating...")
    create_desktop_shortcut()


# ── First-run gate ────────────────────────────────────────────────────────────

def is_first_run() -> bool:
    return not _FIRST_RUN.exists()


def mark_first_run_done() -> None:
    try:
        _FIRST_RUN.parent.mkdir(parents=True, exist_ok=True)
        _FIRST_RUN.write_text("done", encoding="utf-8")
    except Exception as exc:
        log(f"[Startup] mark_first_run_done error: {exc}")


def first_run_setup() -> None:
    """
    Called on every launch (from background thread).

    Always (Windows only):
      - Ensures the Desktop shortcut exists (recreates if user deleted it).

    First launch only:
      - Enables startup entry (registry on Windows, LaunchAgent plist on macOS).
      - Marks first-run as done so startup step is skipped next time.
    """
    # ── Always (Windows): ensure Desktop shortcut ─────────────────────────────
    try:
        ensure_desktop_shortcut()
    except Exception as exc:
        log(f"[Startup] Shortcut check error: {exc}")

    # ── First launch only: startup entry ──────────────────────────────────────
    if not is_first_run():
        return

    log("[Startup] First run — setting up startup entry...")
    try:
        set_startup(True)
    except Exception as exc:
        log(f"[Startup] Startup setup error: {exc}")

    mark_first_run_done()
    log("[Startup] First-run setup complete.")
