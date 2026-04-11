"""
main.py - PhoneBusted entry point

Thread model:
  Main thread  →  tkinter  (main window with controls + log)
  Thread       →  pystray  (system tray icon)
  Thread       →  PhoneDetector  (YOLO + webcam)
  Thread       →  AudioManager workers (winsound / TTS)

Single-instance: socket IPC on localhost:47832
  - First instance: binds + listens; receives "SHOW" → restores window
  - Second instance: connects + sends "SHOW" + exits silently
"""

import sys
import os
import socket
import threading
import time
import traceback

# ── Fix Windows console encoding ─────────────────────────────────────────────
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def set_low_priority():
    try:
        import psutil, os
        p = psutil.Process(os.getpid())
        if sys.platform == "win32":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except Exception:
        pass


# ── Socket IPC for single-instance ───────────────────────────────────────────
_IPC_HOST = "127.0.0.1"
_IPC_PORT = 47832


def _signal_existing_instance() -> bool:
    """
    Try to connect to a running instance and send SHOW.
    Returns True if a running instance was found (caller should exit).
    """
    try:
        with socket.create_connection((_IPC_HOST, _IPC_PORT), timeout=1) as s:
            s.sendall(b"SHOW")
        return True
    except (ConnectionRefusedError, OSError):
        return False


def _start_ipc_server(on_show) -> None:
    """
    Bind the IPC socket and listen in a daemon thread.
    Calls on_show() whenever "SHOW" is received from a second instance.
    """
    def _serve():
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((_IPC_HOST, _IPC_PORT))
            srv.listen(5)
            srv.settimeout(1.0)
            while True:
                try:
                    conn, _ = srv.accept()
                    with conn:
                        data = conn.recv(64)
                    if data == b"SHOW":
                        on_show()
                except socket.timeout:
                    pass
                except Exception:
                    pass
        except Exception:
            pass   # port in use or other OS error — fail silently

    t = threading.Thread(target=_serve, daemon=True, name="IPCServer")
    t.start()


# ── tkinter must come before everything else on Windows ───────────────────────
import tkinter as tk


# ── Bootstrap the debug logger first so every module can use it ───────────────
from debug import log, DebugWindow


def _ensure_assets() -> None:
    from pathlib import Path
    from PIL import Image, ImageDraw

    assets = Path(__file__).parent / "assets"
    assets.mkdir(exist_ok=True)

    icon_path = assets / "icon.png"
    if not icon_path.exists():
        try:
            img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, 62, 62], fill=(39, 174, 96))
            draw.rounded_rectangle([22, 16, 42, 48], radius=4,
                                   fill=(255, 255, 255, 220))
            draw.rectangle([25, 21, 39, 39], fill=(39, 174, 96))
            img.save(str(icon_path))
            log("Created assets/icon.png")
        except Exception as exc:
            log(f"Could not create icon.png: {exc}")

    alert_path = assets / "alert.wav"
    if not alert_path.exists():
        log("assets/alert.wav not found - will use TTS voice fallback.")
        log("Drop any .wav file into phonebusted/assets/alert.wav for custom audio.")


def main() -> None:
    # ── 0. Single-instance check via socket IPC ───────────────────────────────
    if _signal_existing_instance():
        # A running instance was found — it will show its window. Exit silently.
        sys.exit(0)

    # ── 1. Create main tkinter root IMMEDIATELY (window visible fast) ─────────
    root = tk.Tk()
    debug_win = DebugWindow(root)

    log("=" * 50)
    log("PhoneBusted v1.0  -  Starting up")
    log("=" * 50)

    # ── 1b. Start IPC server so a second instance can signal us ──────────────
    def _on_show_requested():
        """Called from IPC daemon thread — must schedule on main thread."""
        root.after(0, debug_win.show)
        log("[IPC] SHOW received — restoring window.")

    _start_ipc_server(_on_show_requested)

    # ── 2. Assets (background — doesn't block window) ────────────────────────
    def _bg_assets():
        try:
            _ensure_assets()
        except Exception as exc:
            log(f"WARNING: asset setup error: {exc}")

    threading.Thread(target=_bg_assets, daemon=True, name="AssetSetup").start()

    # ── 3. Settings ───────────────────────────────────────────────────────────
    try:
        from config import load_settings
        config = load_settings()
        log(f"Settings loaded: {config}")
    except Exception as exc:
        log(f"ERROR loading settings: {exc}")
        log(traceback.format_exc())
        from config import DEFAULT_SETTINGS
        config = DEFAULT_SETTINGS.copy()
        log("Using default settings.")

    # ── 4. Audio ──────────────────────────────────────────────────────────────
    try:
        from audio import AudioManager
        audio = AudioManager()
        log("Audio manager ready.")
    except Exception as exc:
        log(f"ERROR init audio: {exc}")
        log(traceback.format_exc())
        audio = None

    # ── 5. License check (Toplevel on main thread, blocks via wait_window) ────
    debug_win.set_status("Checking license...")
    log("Running license check...")
    try:
        from license import run_license_check
        license_ok = run_license_check(root)
    except Exception as exc:
        log(f"ERROR in license check: {exc}")
        log(traceback.format_exc())
        license_ok = False

    if not license_ok:
        log("License not validated. App will exit in 4 seconds.")
        debug_win.set_status("License failed - exiting", "#ff5252")
        root.after(4000, root.quit)
        root.mainloop()
        return

    log("License OK - continuing boot sequence.")
    debug_win.set_status("Starting...", "#69ff47")

    # ── 5b. First-run setup (startup + desktop shortcut — background) ─────────
    def _run_first_run_setup():
        try:
            from startup import first_run_setup
            first_run_setup()
        except Exception as exc:
            log(f"WARNING: first-run setup error: {exc}")

    threading.Thread(target=_run_first_run_setup, daemon=True,
                     name="FirstRunSetup").start()

    # ── 6. Shared frame buffer ────────────────────────────────────────────────
    frame_buffer: dict = {
        "frame":      None,
        "detected":   False,
        "confidence": 0.0,
        "boxes":      [],
    }

    # ── 7. Live View window ───────────────────────────────────────────────────
    try:
        from live_view import LiveViewWindow
        live_view = LiveViewWindow(frame_buffer, root)
        log("Live View module loaded (off by default).")
    except Exception as exc:
        log(f"ERROR creating Live View: {exc}")
        log(traceback.format_exc())
        live_view = None

    # ── 8. Detector ───────────────────────────────────────────────────────────
    try:
        from detector import PhoneDetector
        detector = PhoneDetector(
            config        = config,
            audio_manager = audio,
            frame_buffer  = frame_buffer,
        )
        log("Detector created.")
    except Exception as exc:
        log(f"ERROR creating detector: {exc}")
        log(traceback.format_exc())
        detector = None

    # ── 9. Tray ───────────────────────────────────────────────────────────────
    try:
        from tray import TrayManager

        def on_quit():
            log("Quit requested - shutting down...")
            if detector:
                detector.stop()
            if live_view:
                live_view.hide()
            root.after(0, root.quit)

        tray = TrayManager(
            config       = config,
            detector     = detector,
            live_view    = live_view,
            root         = root,
            on_quit      = on_quit,
            open_window  = debug_win.show,
        )
        log("Tray manager created.")
    except Exception as exc:
        log(f"ERROR creating tray manager: {exc}")
        log(traceback.format_exc())
        tray = None
        on_quit = lambda: root.quit()

    # Wire the main window buttons now that everything exists
    debug_win.wire_up(
        detector     = detector,
        live_view    = live_view,
        on_quit      = on_quit,
        show_balloon = tray.show_balloon if tray else None,
    )

    # Wire on_detection callback now that tray exists
    if detector and tray:
        detector.on_detection = tray.on_phone_detected

    set_low_priority()

    # ── 10. Start detector thread (YOLO loads in background) ──────────────────
    if detector:
        try:
            detector.start()
            log("Detector thread launched — YOLO loading in background.")
        except Exception as exc:
            log(f"ERROR starting detector: {exc}")
            log(traceback.format_exc())
    else:
        log("WARNING: Detector not available - no phone detection.")

    # Webcam watchdog - warn if webcam fails to open
    def _webcam_watchdog():
        time.sleep(6)
        if detector and detector.has_webcam_error():
            log("ERROR: Webcam could not be opened!")
            log("  - Check no other app is using your camera")
            log("  - Try unplugging and replugging your webcam")
            root.after(0, lambda: debug_win.set_status("ERROR: Webcam not found!", "#ff5252"))
        elif detector:
            log("Webcam watchdog: webcam appears healthy.")
            root.after(0, lambda: debug_win.set_status("Running \u2713", "#00e676"))

    threading.Thread(target=_webcam_watchdog, daemon=True,
                     name="WebcamWatchdog").start()

    # ── 11. Start pystray in background thread ────────────────────────────────
    if tray:
        def _run_tray():
            try:
                log("Pystray starting in background thread...")
                tray.run()
            except Exception as exc:
                log(f"ERROR in pystray: {exc}")
                log(traceback.format_exc())

        tray_thread = threading.Thread(target=_run_tray, daemon=True,
                                       name="PystrayThread")
        tray_thread.start()
        log("Pystray thread launched.")
    else:
        log("WARNING: Tray not available.")

    # ── 12. Run tkinter main loop ─────────────────────────────────────────────
    debug_win.set_status("Loading YOLO model...", "#ffab40")
    log("Tkinter mainloop starting.")
    log("  -> Use the buttons above to control Live View and pausing")
    log("  -> Hold your phone to the webcam to test detection")
    log("-" * 50)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        log("KeyboardInterrupt - exiting.")

    log("PhoneBusted exited.")


if __name__ == "__main__":
    main()
