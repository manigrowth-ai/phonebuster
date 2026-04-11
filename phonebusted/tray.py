"""
tray.py - Windows system tray icon and menu for PhoneBusted

Runs in a BACKGROUND thread (not main thread).
The main thread is owned by tkinter (main window).

Green  = monitoring active
Red    = phone just detected  (resets after 3 s)
Orange = alerts muted
"""

import threading
import time
import tkinter as tk
from typing import Optional, Callable

from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item, Menu

from debug import log


# ── Icon factory ──────────────────────────────────────────────────────────────

def _make_icon(color: str = "green", size: int = 64) -> Image.Image:
    PALETTE = {
        "green":  (39,  174,  96),
        "red":    (192,  57,  43),
        "orange": (211,  84,   0),
    }
    fill = PALETTE.get(color, PALETTE["green"])

    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.ellipse([2, 2, size - 2, size - 2], fill=fill)

    pw, ph = 20, 32
    px = (size - pw) // 2
    py = (size - ph) // 2
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=5,
                           fill=(255, 255, 255, 230))
    draw.rectangle([px + 3, py + 5, px + pw - 3, py + ph - 9], fill=fill)

    cx = px + pw // 2
    draw.ellipse([cx - 2, py + ph - 7, cx + 2, py + ph - 3],
                 fill=(255, 255, 255, 180))

    return img


# ── TrayManager ───────────────────────────────────────────────────────────────

class TrayManager:
    def __init__(
        self,
        config:     dict,
        detector,
        live_view,
        root:       tk.Tk,
        on_quit,
        open_window: Optional[Callable] = None,
    ) -> None:
        self.config      = config
        self.detector    = detector
        self.live_view   = live_view
        self.root        = root
        self.on_quit     = on_quit
        self.open_window = open_window   # callback → shows the main window

        self._icon         : Optional[pystray.Icon] = None
        self._running      = False
        self._update_thread: Optional[threading.Thread] = None

    # ── Menu builder ──────────────────────────────────────────────────────────

    def _build_menu(self) -> Menu:
        count  = self.detector.get_detection_count() if self.detector else 0
        muted  = self.config.get("alerts_muted", False)
        status = "Active" if self.config.get("monitoring_enabled", True) else "Paused"

        lv_visible = self.live_view and self.live_view.is_visible()
        mute_label = "Unmute Alerts" if muted else "Mute Alerts"

        conf = self.config.get("confidence_threshold", 0.60)
        cool = self.config.get("cooldown_seconds", 8)

        def s(label, val):
            tick = " (current)" if abs(conf - val) < 0.01 else ""
            return item(f"  {label}{tick}", self._make_sens(val))

        def c(label, val):
            tick = " (current)" if cool == val else ""
            return item(f"  {label}{tick}", self._make_cool(val))

        return Menu(
            # ── Header ──────────────────────────────────────────────────────
            item(f"PhoneBusted  -  {status}", None, enabled=False),
            Menu.SEPARATOR,
            item(f"Detections today: {count}", None, enabled=False),
            Menu.SEPARATOR,
            # ── Window ──────────────────────────────────────────────────────
            item("Open PhoneBusted", self._open_window, default=True),
            Menu.SEPARATOR,
            # ── Primary actions ──────────────────────────────────────────────
            item("Show Live View" if not lv_visible else "Hide Live View",
                 self._toggle_live_view),
            item(mute_label, self._toggle_mute),
            Menu.SEPARATOR,
            # ── Settings ────────────────────────────────────────────────────
            item("Sensitivity", Menu(
                s("Low    (0.70)", 0.70),
                s("Medium (0.50)", 0.50),
                s("High   (0.35)", 0.35),
            )),
            item("Cooldown", Menu(
                c(" 8 seconds",  8),
                c("15 seconds", 15),
                c("30 seconds", 30),
            )),
            Menu.SEPARATOR,
            # ── Footer ──────────────────────────────────────────────────────
            item("Enter License Key", self._open_license_dialog),
            item("Quit", self._quit),
        )

    # ── Action handlers ───────────────────────────────────────────────────────

    def _make_sens(self, val: float):
        def h(icon, _):
            self.config["confidence_threshold"] = val
            self._save()
            self._refresh_menu()
            log(f"[Tray] Sensitivity -> {val}")
        return h

    def _make_cool(self, val: int):
        def h(icon, _):
            self.config["cooldown_seconds"] = val
            self._save()
            self._refresh_menu()
            log(f"[Tray] Cooldown -> {val}s")
        return h

    def _open_window(self, icon, _) -> None:
        """Restore and raise the main window — must run on main thread."""
        log("[Tray] Open PhoneBusted window.")
        if self.open_window:
            # open_window is debug_win.show(), schedule on main thread
            self.root.after(0, self.open_window)
        else:
            self.root.after(0, self._fallback_show)

    def _fallback_show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _toggle_live_view(self, icon, _) -> None:
        if self.live_view:
            self.live_view.toggle()
            threading.Timer(0.3, self._refresh_menu).start()
            log("[Tray] Live View toggled.")

    def _toggle_mute(self, icon, _) -> None:
        muted = self.config.get("alerts_muted", False)
        self.config["alerts_muted"] = not muted
        self._save()
        self._refresh_menu()
        state = "MUTED" if not muted else "UNMUTED"
        log(f"[Tray] Alerts {state}.")
        self._set_color("orange" if not muted else "green")

    def _open_license_dialog(self, icon, _) -> None:
        def _show():
            from license import _show_dialog
            _show_dialog()
        threading.Thread(target=_show, daemon=True).start()

    def _quit(self, icon, _) -> None:
        log("[Tray] Quit clicked.")
        self._running = False
        icon.stop()
        self.on_quit()

    # ── Balloon notification ──────────────────────────────────────────────────

    def show_balloon(self, message: str, title: str = "PhoneBusted") -> None:
        """Show a Windows tray balloon notification. Called from any thread."""
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception as exc:
                log(f"[Tray] Balloon notify error: {exc}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _save(self) -> None:
        from config import save_settings
        save_settings(self.config)

    def _refresh_menu(self) -> None:
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def _set_color(self, color: str) -> None:
        if self._icon:
            self._icon.icon = _make_icon(color)

    # ── Detection callback (called from detector thread) ──────────────────────

    def on_phone_detected(self, confidence: float, count: int) -> None:
        self._set_color("red")
        self._refresh_menu()

        if self.live_view:
            self.live_view.notify_detection(confidence)

        def _reset():
            time.sleep(3)
            if self._running:
                muted = self.config.get("alerts_muted", False)
                self._set_color("orange" if muted else "green")
                self._refresh_menu()

        threading.Thread(target=_reset, daemon=True).start()

    # ── Periodic menu refresh ─────────────────────────────────────────────────

    def _update_loop(self) -> None:
        while self._running:
            time.sleep(5)
            if self._icon and self._running:
                self._refresh_menu()

    # ── Run (blocking — call from a BACKGROUND thread) ────────────────────────

    def run(self) -> None:
        self._running = True

        self._icon = pystray.Icon(
            name  = "PhoneBusted",
            icon  = _make_icon("green"),
            title = "PhoneBusted - Monitoring",
            menu  = self._build_menu(),
        )

        self._update_thread = threading.Thread(
            target=self._update_loop, daemon=True, name="TrayUpdater",
        )
        self._update_thread.start()

        log("[Tray] Pystray icon starting (background thread)...")
        self._icon.run()
        log("[Tray] Pystray icon stopped.")
