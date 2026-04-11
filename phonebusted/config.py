"""
config.py - Settings management for PhoneBusted
Saves/loads settings from ~/.phonebusted/settings.json
"""

import json
from pathlib import Path

# ── App directories ──────────────────────────────────────────────────────────
APP_DIR       = Path.home() / ".phonebusted"
SETTINGS_FILE = APP_DIR / "settings.json"
LICENSE_FILE  = APP_DIR / "license.key"

# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS: dict = {
    "confidence_threshold": 0.50,   # YOLO confidence needed to trigger alert
    "cooldown_seconds":     8,      # Seconds between alerts (8 for testing)
    "monitoring_enabled":   True,   # Whether detection loop is running
    "alerts_muted":         False,  # When True audio is silenced via tray
}


def ensure_app_dir() -> None:
    """Create ~/.phonebusted/ if it doesn't exist."""
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    """Load settings from disk, falling back to defaults for missing keys."""
    ensure_app_dir()
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            settings = DEFAULT_SETTINGS.copy()
            settings.update(saved)
            return settings
        except Exception as exc:
            print(f"[Config] Failed to load settings ({exc}), using defaults.")
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    """Persist settings to disk."""
    ensure_app_dir()
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    print("[Config] Settings saved.")
