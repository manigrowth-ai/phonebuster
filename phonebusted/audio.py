"""
audio.py - Audio alert manager for PhoneBusted

Windows: uses winsound (built-in) to play WAV files — zero extra install.
macOS:   uses subprocess + afplay (built-in macOS CLI) to play WAV files.

Randomly rotates through all 4 alert files, never repeating the same file
twice in a row. Falls back to the next file automatically if one fails.
"""

import random
import subprocess
import sys
import threading
from pathlib import Path

from debug import log
from resource_path import resource_path

ASSETS_DIR = Path(resource_path("assets"))

ALERT_FILES = [
    ASSETS_DIR / "alert1.wav",
    ASSETS_DIR / "alert2.wav",
    ASSETS_DIR / "alert3.wav",
    ASSETS_DIR / "alert4.wav",
]


class AudioManager:
    def __init__(self) -> None:
        self._playing    = False
        self._lock       = threading.Lock()
        self._last_index = -1   # prevents same file playing twice in a row

        # Log which files exist on startup
        existing = [f.name for f in ALERT_FILES if f.exists()]
        missing  = [f.name for f in ALERT_FILES if not f.exists()]
        log(f"[Audio] Alert files found: {existing}")
        if missing:
            log(f"[Audio] Alert files missing: {missing}")

    # ── File selection ────────────────────────────────────────────────────────

    def _pick_next_file(self) -> Path | None:
        """
        Pick a random WAV file, never the same index as last time.
        Returns None if no files exist.
        """
        available = [(i, p) for i, p in enumerate(ALERT_FILES) if p.exists()]
        if not available:
            log("[Audio] WARNING: No alert WAV files found in assets/")
            return None

        if len(available) == 1:
            self._last_index = available[0][0]
            return available[0][1]

        # Exclude last-played index
        choices = [(i, p) for i, p in available if i != self._last_index]
        if not choices:
            choices = available   # safety: all files tried, reset pool

        idx, picked = random.choice(choices)
        self._last_index = idx
        return picked

    # ── Playback ──────────────────────────────────────────────────────────────

    def _play_wav(self, wav: Path) -> None:
        """Play a single WAV file using the platform-appropriate method."""
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(
                str(wav),
                winsound.SND_FILENAME | winsound.SND_NODEFAULT,
            )
        else:
            # macOS (and Linux fallback): afplay is available on every Mac
            subprocess.run(
                ["afplay", str(wav)],
                check=True,
                capture_output=True,
            )

    # ── Worker ────────────────────────────────────────────────────────────────

    def _worker(self, first_choice: Path) -> None:
        """
        Try first_choice, then cycle through remaining files if it fails.
        Logs every attempt. Runs in a daemon thread.
        """
        remaining = [p for p in ALERT_FILES
                     if p.exists() and p != first_choice]
        random.shuffle(remaining)
        try_order = [first_choice] + remaining

        played = False
        for wav in try_order:
            if not wav.exists():
                log(f"[Audio] Skipping {wav.name} - file not found on disk")
                continue
            log(f"[Audio] Attempting: {wav.name}")
            try:
                self._play_wav(wav)
                log(f"[Audio] Finished: {wav.name}")
                played = True
                break
            except Exception as exc:
                log(f"[Audio] Failed ({wav.name}): {exc} — trying next file")

        if not played:
            log("[Audio] WARNING: All alert files failed. No sound played.")

        with self._lock:
            self._playing = False

    # ── Public API ────────────────────────────────────────────────────────────

    def play_alert(self) -> None:
        """Fire the alert in a background thread. No-op if already playing."""
        with self._lock:
            if self._playing:
                log("[Audio] Already playing — skipping this trigger")
                return
            self._playing = True

        chosen = self._pick_next_file()
        if chosen is None:
            with self._lock:
                self._playing = False
            return

        log(f"[Audio] Selected: {chosen.name}  (index={self._last_index})")
        t = threading.Thread(
            target=self._worker, args=(chosen,), daemon=True, name="AudioWorker",
        )
        t.start()

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing
