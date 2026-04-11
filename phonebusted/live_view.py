"""
live_view.py - Live webcam feed with phone detection overlay

Runs cv2.imshow() in its own daemon thread - does not block
the main tkinter thread or the tray.

Frame buffer schema (written by PhoneDetector):
    {
        'frame':      np.ndarray | None,  # raw BGR frame from webcam
        'detected':   bool,               # True for 3 s after detection
        'confidence': float,              # confidence of last detection
        'boxes':      list[tuple],        # [(x1,y1,x2,y2), ...]  pixel coords
    }

Controls:
    Q  - close the Live View window
"""

import threading
import time

from debug import log

WINDOW_NAME = "PhoneBusted - Live View"
REFRESH_MS  = 33   # ~30 fps


class LiveViewWindow:
    """
    Manages the optional live-view window via cv2.imshow in a daemon thread.
    Call show() / hide() / toggle() from any thread safely.
    """

    def __init__(self, frame_buffer: dict, root=None) -> None:
        # root kept as optional param so main.py call signature is unchanged
        self.frame_buffer    = frame_buffer
        self._thread         : threading.Thread | None = None
        self._stop_event     = threading.Event()
        self._visible        = False
        self._detected_until = 0.0   # timestamp until which border stays red

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self) -> None:
        """Open the live-view window in a background thread."""
        if self._visible and self._thread and self._thread.is_alive():
            log("[LiveView] Already visible - skipping show()")
            return
        log("[LiveView] Opening Live View window...")
        self._stop_event.clear()
        self._visible = True
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name="LiveViewThread",
        )
        self._thread.start()

    def hide(self) -> None:
        """Signal the live-view thread to close."""
        log("[LiveView] Closing Live View window...")
        self._stop_event.set()
        self._visible = False

    def toggle(self) -> None:
        """Toggle live-view on/off."""
        if self._visible and self._thread and self._thread.is_alive():
            self.hide()
        else:
            self.show()

    def is_visible(self) -> bool:
        return self._visible and self._thread is not None and self._thread.is_alive()

    def notify_detection(self, confidence: float) -> None:
        """
        Called by the detector callback to extend the red-flash period.
        Thread-safe — just writes a float.
        """
        self._detected_until = time.time() + 3.0
        log(f"[LiveView] Detection notified - flashing red for 3s (conf={confidence:.2f})")

    # ── Main loop (runs in daemon thread) ─────────────────────────────────────

    def _run(self) -> None:
        import cv2

        try:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WINDOW_NAME, 660, 520)
            log("[LiveView] cv2 window created. Press Q to close.")
        except Exception as exc:
            log(f"[LiveView] ERROR creating window: {exc}")
            self._visible = False
            return

        while not self._stop_event.is_set():
            frame = self.frame_buffer.get("frame")

            if frame is None:
                # Show blank placeholder until webcam delivers first frame
                import numpy as np
                placeholder = np.zeros((480, 640, 3), dtype="uint8")
                cv2.putText(
                    placeholder, "Waiting for webcam...",
                    (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 0), 2,
                )
                cv2.imshow(WINDOW_NAME, placeholder)
                key = cv2.waitKey(REFRESH_MS) & 0xFF
                if key == ord('q') or key == ord('Q'):
                    break
                continue

            # Copy so we don't mutate the shared buffer
            display = frame.copy()
            h, w    = display.shape[:2]

            detected   = time.time() < self._detected_until
            boxes      = self.frame_buffer.get("boxes", [])
            confidence = self.frame_buffer.get("confidence", 0.0)

            if detected:
                # ── RED state ────────────────────────────────────────────────
                border_color = (0, 0, 255)   # BGR red

                # Thick red border
                cv2.rectangle(display, (0, 0), (w - 1, h - 1),
                              border_color, 16)

                # Big bold "PHONEBUSTED!" text
                cv2.putText(
                    display, "PHONEBUSTED!",
                    (10, 55), cv2.FONT_HERSHEY_DUPLEX, 1.8,
                    (0, 0, 0), 6,   # black outline
                )
                cv2.putText(
                    display, "PHONEBUSTED!",
                    (10, 55), cv2.FONT_HERSHEY_DUPLEX, 1.8,
                    (0, 0, 255), 3,  # red fill
                )

                # Red bounding boxes around detected phone
                for (x1, y1, x2, y2) in boxes:
                    cv2.rectangle(
                        display,
                        (int(x1), int(y1)), (int(x2), int(y2)),
                        (0, 0, 255), 4,
                    )
                    label = f"PHONE {confidence:.0%}"
                    lx, ly = int(x1), max(int(y1) - 8, 14)
                    cv2.putText(display, label, (lx, ly),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (0, 0, 255), 2)

            else:
                # ── GREEN state ──────────────────────────────────────────────
                border_color = (0, 255, 0)   # BGR green

                # Thick green border
                cv2.rectangle(display, (0, 0), (w - 1, h - 1),
                              border_color, 12)

                # "Monitoring..." text at top
                cv2.putText(
                    display, "Monitoring... Stay Focused",
                    (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                    (0, 0, 0), 4,   # black outline
                )
                cv2.putText(
                    display, "Monitoring... Stay Focused",
                    (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                    (0, 255, 0), 2,  # green fill
                )

            cv2.imshow(WINDOW_NAME, display)

            key = cv2.waitKey(REFRESH_MS) & 0xFF
            if key == ord('q') or key == ord('Q'):
                log("[LiveView] Q pressed - closing window.")
                break

            # Detect if user closed the window via the X button
            try:
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    log("[LiveView] Window closed by user.")
                    break
            except Exception:
                break

        try:
            cv2.destroyWindow(WINDOW_NAME)
        except Exception:
            pass

        self._visible = False
        log("[LiveView] Live View thread exited.")
