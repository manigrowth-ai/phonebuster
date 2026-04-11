"""
debug.py - PhoneBusted main control window

Clean user-facing window — no log widget.
All logs written silently to ~/.phonebusted/app.log.

Closing the window (X button) minimizes to tray.
Only the Quit button shuts the app down completely.
"""

import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

_FONT = _FONT if sys.platform == "win32" else "Arial"

# ── Log file setup ────────────────────────────────────────────────────────────
_LOG_FILE = Path.home() / ".phonebusted" / "app.log"
_log_q: queue.Queue = queue.Queue()


def _log_writer() -> None:
    """
    Background daemon thread — drains _log_q to app.log.
    Completely non-blocking for every caller of log().
    """
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    while True:
        try:
            line = _log_q.get(timeout=1)
            try:
                with open(_LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
            except Exception:
                pass
        except queue.Empty:
            pass


# Start the background writer immediately at import time
_writer = threading.Thread(target=_log_writer, daemon=True, name="LogWriter")
_writer.start()


def log(msg: str) -> None:
    """
    Non-blocking log — puts a line in the queue and returns instantly.
    Safe to call from any thread at any frequency.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _log_q.put(f"[{ts}] {msg}")


# ── DebugWindow ───────────────────────────────────────────────────────────────

class DebugWindow:
    """
    Main PhoneBusted control window.
    Build first, then call wire_up() once all components are ready.
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root          = root
        self._detector     = None
        self._live_view    = None
        self._on_quit      = None
        self._show_balloon = None   # injected by wire_up

        self._pause_end_time = 0.0
        self._pause_job      = None

        self._build()
        self._poll()

    # ── Wiring ────────────────────────────────────────────────────────────────

    def wire_up(self, detector, live_view, on_quit, show_balloon=None) -> None:
        """Call after all components are created so buttons are functional."""
        self._detector     = detector
        self._live_view    = live_view
        self._on_quit      = on_quit
        self._show_balloon = show_balloon

    # ── Public: show window (called from tray "Open PhoneBusted") ─────────────

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self) -> None:
        self.root.title("PhoneBusted")
        self.root.geometry("480x360")
        self.root.configure(bg="#0d0d1a")
        self.root.resizable(False, False)

        # X button → minimize to tray, NOT quit
        self.root.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg="#1a1a2e", pady=8)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr, text="  \U0001f6ab PHONEBUSTED",
            font=(_FONT, 13, "bold"), bg="#1a1a2e", fg="#e94560",
        ).pack(side=tk.LEFT)

        self._status_badge = tk.Label(
            hdr, text="  [Running \u2713]",
            font=(_FONT, 10, "bold"), bg="#1a1a2e", fg="#00e676",
        )
        self._status_badge.pack(side=tk.RIGHT, padx=10)

        # ── Info bar ─────────────────────────────────────────────────────────
        info = tk.Frame(self.root, bg="#111122", pady=6)
        info.pack(fill=tk.X)

        self._detections_label = tk.Label(
            info, text="Detections today: 0",
            font=(_FONT, 10), bg="#111122", fg="#c0c0d0",
        )
        self._detections_label.pack(side=tk.LEFT, padx=12)

        self._monitor_label = tk.Label(
            info, text="Status: Monitoring...",
            font=(_FONT, 10), bg="#111122", fg="#00e676",
        )
        self._monitor_label.pack(side=tk.RIGHT, padx=12)

        # ── Live View row ─────────────────────────────────────────────────────
        self._sep()
        lv = tk.Frame(self.root, bg="#0d0d1a", pady=7)
        lv.pack(fill=tk.X, padx=10)

        self._btn(lv, "\u25b6 Live View ON",  "#1e4d2b", "#00e676", self._live_view_on ).pack(side=tk.LEFT, padx=(0, 8))
        self._btn(lv, "\u25a0 Live View OFF",  "#3d1a1a", "#ff5252", self._live_view_off).pack(side=tk.LEFT)

        # ── Pause / Resume row ────────────────────────────────────────────────
        self._sep()
        pr = tk.Frame(self.root, bg="#0d0d1a", pady=7)
        pr.pack(fill=tk.X, padx=10)

        self._btn(pr, "\u23f8 Pause 60 mins", "#2d2000", "#ffab40", self._pause_60).pack(side=tk.LEFT, padx=(0, 8))
        self._btn(pr, "\u25b6 Resume",         "#1a1a2e", "#80c0ff", self._resume   ).pack(side=tk.LEFT)

        # ── Startup toggle row ────────────────────────────────────────────────
        self._sep()
        st = tk.Frame(self.root, bg="#0d0d1a", pady=7)
        st.pack(fill=tk.X, padx=10)

        self._startup_btn = self._btn(
            st, self._startup_label(), "#1a1a2e", "#a0a0c0", self._toggle_startup
        )
        self._startup_btn.pack(side=tk.LEFT)

        # ── Quit row ──────────────────────────────────────────────────────────
        self._sep()
        qr = tk.Frame(self.root, bg="#0d0d1a", pady=7)
        qr.pack(fill=tk.X, padx=10)

        self._btn(qr, "\u2715 Quit PhoneBusted", "#3d0000", "#ff4444", self._quit).pack(side=tk.LEFT)

        self._sep()

        log("PhoneBusted started.")

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _sep(self) -> None:
        tk.Frame(self.root, bg="#2a2a4a", height=1).pack(fill=tk.X, padx=6)

    def _btn(self, parent, text, bg, fg, cmd) -> tk.Button:
        return tk.Button(
            parent, text=text,
            font=(_FONT, 10, "bold"),
            bg=bg, fg=fg,
            activebackground=bg, activeforeground=fg,
            relief=tk.FLAT, padx=14, pady=7,
            cursor="hand2",
            command=cmd,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def set_status(self, text: str, color: str = "#00e676") -> None:
        self._status_badge.configure(text=f"  [{text}]", fg=color)

    # ── Button handlers ───────────────────────────────────────────────────────

    def _live_view_on(self) -> None:
        if self._live_view:
            self._live_view.show()
            log("[UI] Live View ON.")

    def _live_view_off(self) -> None:
        if self._live_view:
            self._live_view.hide()
            log("[UI] Live View OFF.")

    def _pause_60(self) -> None:
        self._pause_end_time = time.time() + 3600
        if self._detector:
            self._detector.pause(3600)
        log("[UI] Monitoring paused for 60 minutes.")
        self._tick_pause()

    def _resume(self) -> None:
        self._pause_end_time = 0.0
        if self._detector:
            self._detector.resume()
        if self._pause_job:
            self.root.after_cancel(self._pause_job)
            self._pause_job = None
        self._monitor_label.configure(text="Status: Monitoring...", fg="#00e676")
        self._status_badge.configure(text="  [Running \u2713]", fg="#00e676")
        log("[UI] Monitoring resumed.")

    def _tick_pause(self) -> None:
        remaining = self._pause_end_time - time.time()
        if remaining <= 0:
            self._resume()
            return
        mins = int(remaining) // 60
        secs = int(remaining) % 60
        self._monitor_label.configure(
            text=f"Paused: {mins:02d}:{secs:02d} remaining", fg="#ffab40",
        )
        self._status_badge.configure(text="  [Paused \u23f8]", fg="#ffab40")
        self._pause_job = self.root.after(1000, self._tick_pause)

    def _startup_label(self) -> str:
        try:
            from startup import get_startup
            on = get_startup()
        except Exception:
            on = False
        state = "ON" if on else "OFF"
        color_hint = " \u2713" if on else " \u25a1"
        return f"\U0001f680 Launch at Startup: {state}{color_hint}"

    def _toggle_startup(self) -> None:
        try:
            from startup import get_startup, set_startup
            current = get_startup()
            set_startup(not current)
            self._startup_btn.configure(text=self._startup_label())
            log(f"[UI] Launch at startup: {'ON' if not current else 'OFF'}.")
        except Exception as exc:
            log(f"[UI] Startup toggle error: {exc}")

    def _minimize_to_tray(self) -> None:
        """Hide window to tray — app keeps running."""
        self.root.withdraw()
        log("[UI] Window minimized to tray.")
        if self._show_balloon:
            try:
                self._show_balloon(
                    "PhoneBusted is still running in your tray.",
                    "PhoneBusted",
                )
            except Exception:
                pass

    def _quit(self) -> None:
        log("[UI] Quit PhoneBusted.")
        if self._on_quit:
            self._on_quit()
        else:
            self.root.quit()

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if self._detector:
            count = self._detector.get_detection_count()
            self._detections_label.configure(text=f"Detections today: {count}")

        self.root.after(500, self._poll)
