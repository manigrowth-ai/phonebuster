"""
detector.py - YOLO11n webcam phone detection for PhoneBusted

Runs in a background daemon thread.
The webcam feed is NEVER shown to the user by default.
Writes frames + detection state into a shared frame_buffer dict
so the optional Live View window can read them on the main thread.
"""

import time
import threading
from typing import Callable, Optional

from debug import log

PHONE_CLASS_ID = 67   # COCO dataset: "cell phone"


class PhoneDetector:
    def __init__(
        self,
        config:        dict,
        audio_manager,
        frame_buffer:  dict,                                    # shared with LiveViewWindow
        on_detection:  Optional[Callable[[float, int], None]] = None,
    ) -> None:
        self.config        = config
        self.audio         = audio_manager
        self.frame_buffer  = frame_buffer
        self.on_detection  = on_detection

        self._thread          : Optional[threading.Thread] = None
        self._running         = False
        self._detection_count = 0
        self._last_alert_time = 0.0
        self._model           = None
        self._webcam_error    = False
        self._lock            = threading.Lock()
        self._pause_until     = 0.0   # epoch time; 0 = not paused

    # ── Model ─────────────────────────────────────────────────────────────────

    def _load_model(self) -> bool:
        try:
            log("Loading YOLO11n model (auto-downloads ~6 MB on first run)...")
            from ultralytics import YOLO
            from resource_path import resource_path
            self._model = YOLO(resource_path("yolo11n.pt"))
            log("YOLO11n model loaded OK.")
            return True
        except Exception as exc:
            log(f"ERROR loading YOLO model: {exc}")
            return False

    # ── Detection loop ────────────────────────────────────────────────────────

    def _detection_loop(self) -> None:
        import cv2

        if not self._load_model():
            log("Detector stopping - model load failed.")
            return

        log("Opening webcam (index 0)...")
        cap = cv2.VideoCapture(0)

        if not cap.isOpened():
            log("ERROR: Cannot open webcam. Is it plugged in / in use by another app?")
            self._webcam_error = True
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        log("Webcam opened successfully. Detection loop running.")

        frame_count = 0

        while self._running:

            # ── Pause / monitoring toggle ─────────────────────────────────────
            if time.time() < self._pause_until or not self.config.get("monitoring_enabled", True):
                time.sleep(0.5)
                continue

            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            frame_count += 1

            # Push every frame into the shared buffer (for Live View)
            self.frame_buffer["frame"] = frame   # no copy – live view reads fast

            # Log heartbeat every 100 frames — also show best phone confidence seen
            if frame_count % 100 == 0:
                log(f"Detector alive - {frame_count} frames processed, "
                    f"{self._detection_count} detections so far.")

            # ── YOLO inference ───────────────────────────────────────────────
            try:
                results = self._model(frame, imgsz=320, verbose=False)
            except Exception as exc:
                log(f"Inference error: {exc}")
                time.sleep(0.5)
                continue

            # ── Parse boxes ──────────────────────────────────────────────────
            threshold = float(self.config.get("confidence_threshold", 0.50))
            cooldown  = float(self.config.get("cooldown_seconds",     15))
            now       = time.time()

            detected_this_frame = False

            # Diagnostic: log best phone confidence every 50 frames so
            # app.log shows whether YOLO is seeing phones below threshold
            best_phone_conf = 0.0
            for result in results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    if int(box.cls[0]) == PHONE_CLASS_ID:
                        best_phone_conf = max(best_phone_conf, float(box.conf[0]))
            if frame_count % 50 == 0 and best_phone_conf > 0:
                log(f"[Diag] Frame {frame_count}: best phone conf={best_phone_conf:.2f} "
                    f"(threshold={threshold:.2f}, "
                    f"{'WOULD TRIGGER' if best_phone_conf >= threshold else 'below threshold'})")

            for result in results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    cls_id     = int(box.cls[0])
                    confidence = float(box.conf[0])

                    if cls_id != PHONE_CLASS_ID:
                        continue
                    if confidence < threshold:
                        continue

                    # Grab pixel coords for the Live View bounding box
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                    self.frame_buffer["boxes"]      = [(x1, y1, x2, y2)]
                    self.frame_buffer["confidence"] = confidence
                    detected_this_frame = True

                    # ── Cooldown gate ────────────────────────────────────────
                    with self._lock:
                        elapsed = now - self._last_alert_time
                        if elapsed < cooldown:
                            break
                        # Cooldown has elapsed - reset and fire
                        if self._last_alert_time > 0:
                            log("[Audio] Cooldown reset - ready to fire again")
                        self._last_alert_time  = now
                        self._detection_count += 1
                        count = self._detection_count

                    log(f"*** PHONE DETECTED! conf={confidence:.2f}  total={count}")

                    if self.config.get("alerts_muted", False):
                        log("[Audio] Alerts muted - skipping sound")
                    elif self.audio:
                        self.audio.play_alert()

                    if self.on_detection:
                        self.on_detection(confidence, count)

                    break   # one alert per frame

            if not detected_this_frame:
                # Clear boxes when phone not in frame
                self.frame_buffer["boxes"] = []

            # ~2 fps inference rate (keeps CPU usage low)
            time.sleep(0.5)

        cap.release()
        log("Detector loop stopped - webcam released.")

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._detection_loop, daemon=True, name="PhoneDetector",
        )
        self._thread.start()
        log("Detector thread started.")

    def stop(self) -> None:
        log("Stopping detector...")
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_detection_count(self) -> int:
        with self._lock:
            return self._detection_count

    def pause(self, seconds: float) -> None:
        self._pause_until = time.time() + seconds
        log(f"Detector paused for {seconds:.0f}s.")

    def resume(self) -> None:
        self._pause_until = 0.0
        log("Detector resumed.")

    def has_webcam_error(self) -> bool:
        return self._webcam_error
