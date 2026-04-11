"""
license.py - Polar.sh license key validation for PhoneBusted

Flow on every launch:
  1. Look for ~/.phonebusted/license.key
  2. If NOT found → show activation dialog (always, no exceptions)
  3. If found → validate against Polar.sh API:
       200 OK         → stamp timestamp → launch
       offline        → check 24-hr grace → launch or show dialog
       401/403/404    → key revoked → show dialog (pre-filled + error msg)
  4. Activation dialog: "Activate" + "Quit" only. No trial mode.
"""

import json
import time
import tkinter as tk
from pathlib import Path
from typing import Optional, Tuple

import requests

from config import LICENSE_FILE, ensure_app_dir
from debug import log

# ── Polar.sh config ───────────────────────────────────────────────────────────
POLAR_ORG_ID       = "42e88d21-6742-4c0d-b439-6838554f19a2"
POLAR_VALIDATE_URL = "https://api.polar.sh/v1/customer-portal/license-keys/validate"

OFFLINE_GRACE_HOURS = 24
LAST_VALIDATED_FILE = Path.home() / ".phonebusted" / "last_validated.txt"


# ── Key persistence ───────────────────────────────────────────────────────────

def _save_key(key: str) -> None:
    ensure_app_dir()
    LICENSE_FILE.write_text(key.strip(), encoding="utf-8")
    log("[License] Key saved to disk.")


def _load_key() -> Optional[str]:
    """
    Returns the saved license key, or None if no key file exists or it's empty.
    A missing license.key always forces the activation dialog.
    """
    if not LICENSE_FILE.exists():
        return None
    k = LICENSE_FILE.read_text(encoding="utf-8").strip()
    return k if k else None


# ── Offline grace ─────────────────────────────────────────────────────────────

def _stamp_validated() -> None:
    """Record the current time as the last successful online validation."""
    ensure_app_dir()
    LAST_VALIDATED_FILE.write_text(str(time.time()), encoding="utf-8")


def _within_grace_period() -> bool:
    """True if last successful validation was within OFFLINE_GRACE_HOURS."""
    if not LAST_VALIDATED_FILE.exists():
        return False
    try:
        last = float(LAST_VALIDATED_FILE.read_text(encoding="utf-8").strip())
        hours_since = (time.time() - last) / 3600
        log(f"[License] Offline — last validated {hours_since:.1f}h ago "
            f"(grace = {OFFLINE_GRACE_HOURS}h).")
        return hours_since < OFFLINE_GRACE_HOURS
    except Exception:
        return False


# ── Polar.sh API ──────────────────────────────────────────────────────────────

def _validate_online(key: str) -> Tuple[Optional[bool], str]:
    """
    Returns:
        (True,  "")    – API says valid
        (False, msg)   – API says invalid (401 / 403 / 404 / 422)
        (None,  msg)   – network error / timeout (treat as offline)
    """
    try:
        resp = requests.post(
            POLAR_VALIDATE_URL,
            json={"key": key.strip(), "organization_id": POLAR_ORG_ID},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        log(f"[License] Polar.sh responded: HTTP {resp.status_code}")

        if resp.status_code == 200:
            return True, ""

        try:
            detail = resp.json().get("detail", f"HTTP {resp.status_code}")
        except Exception:
            detail = f"HTTP {resp.status_code}"

        if resp.status_code in (401, 403, 404, 422):
            return False, detail

        # 5xx or unexpected → treat as transient/offline
        return None, f"Server error {resp.status_code}"

    except requests.exceptions.ConnectionError:
        log("[License] No internet connection.")
        return None, "No internet connection"
    except requests.exceptions.Timeout:
        log("[License] Polar.sh request timed out.")
        return None, "Request timed out"
    except Exception as exc:
        log(f"[License] Unexpected error: {exc}")
        return None, str(exc)


# ── Silent startup check ──────────────────────────────────────────────────────

def _check_saved_key() -> Tuple[bool, str]:
    """
    Validate the saved key (called only when license.key exists).
    Returns (ok, reason) — never shows a dialog.
    """
    key = _load_key()
    if not key:
        return False, "no_key"

    ok, msg = _validate_online(key)

    if ok is True:
        _stamp_validated()
        log("[License] Saved key validated online. OK.")
        return True, "online_ok"

    if ok is None:
        # Offline — fall back to grace period
        if _within_grace_period():
            log("[License] Offline — grace period active. Allowing launch.")
            return True, "offline_grace"
        log("[License] Offline and grace period expired.")
        return False, f"offline_expired: {msg}"

    # ok is False — key explicitly rejected by Polar.sh
    log(f"[License] Saved key rejected by Polar.sh: {msg}")
    return False, f"rejected: {msg}"


# ── Activation dialog ─────────────────────────────────────────────────────────

def _show_dialog(root: tk.Tk = None, prefill_key: str = "",
                 error_msg: str = "") -> bool:
    """
    Show the activation dialog.
    - If root is given: shown as Toplevel (non-blocking to mainloop).
    - If root is None: creates a temporary Tk root (for standalone calls).
    Blocks until user activates or quits.
    Returns True → proceed, False → exit app.
    """
    result = {"ok": False}
    _standalone = root is None
    if _standalone:
        root = tk.Tk()
        root.withdraw()

    dlg = tk.Toplevel(root)
    dlg.title("PhoneBusted — Activate")
    dlg.geometry("480x300")
    dlg.resizable(False, False)
    dlg.configure(bg="#1a1a2e")
    dlg.attributes("-topmost", True)

    # Centre on screen
    dlg.update_idletasks()
    sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
    dlg.geometry(f"480x300+{(sw - 480) // 2}+{(sh - 300) // 2}")

    # ── Header ────────────────────────────────────────────────────────────────
    tk.Label(dlg, text="\U0001f6ab  PhoneBusted",
             font=("Segoe UI", 17, "bold"), bg="#1a1a2e", fg="#e94560",
             ).pack(pady=(22, 2))
    tk.Label(dlg, text="Enter your license key to activate.",
             font=("Segoe UI", 9), bg="#1a1a2e", fg="#8080a0",
             ).pack(pady=(0, 16))

    # ── Key entry ─────────────────────────────────────────────────────────────
    tk.Label(dlg, text="License Key:", font=("Segoe UI", 9, "bold"),
             bg="#1a1a2e", fg="#d0d0e8").pack(anchor="w", padx=36)
    key_var = tk.StringVar(value=prefill_key)
    entry = tk.Entry(dlg, textvariable=key_var, width=50,
                     font=("Consolas", 10), bg="#16213e", fg="#e0e0f0",
                     insertbackground="white", relief="flat")
    entry.pack(padx=36, pady=(3, 6), ipady=6)
    entry.focus()
    if prefill_key:
        entry.icursor(tk.END)

    # ── Status label ──────────────────────────────────────────────────────────
    status_var = tk.StringVar(value=error_msg)
    status_lbl = tk.Label(dlg, textvariable=status_var,
                          font=("Segoe UI", 8), bg="#1a1a2e", fg="#e94560")
    status_lbl.pack(pady=(0, 6))

    # ── Buttons ───────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(dlg, bg="#1a1a2e")
    btn_frame.pack()

    def do_activate():
        key = key_var.get().strip()
        if not key:
            status_var.set("Please enter your license key.")
            return

        status_var.set("Validating with Polar.sh\u2026")
        status_lbl.configure(fg="#ffab40")
        dlg.update()

        ok, msg = _validate_online(key)

        if ok is True:
            _save_key(key)
            _stamp_validated()
            log("[License] Activation successful.")
            result["ok"] = True
            dlg.destroy()

        elif ok is None:
            # Offline — allow if this is already the saved key within grace
            saved = _load_key()
            if saved and saved.strip() == key.strip() and _within_grace_period():
                log("[License] Offline activation — grace period OK.")
                result["ok"] = True
                dlg.destroy()
            else:
                status_var.set(
                    f"Can't reach Polar.sh ({msg}).\n"
                    "Connect to the internet to activate."
                )
                status_lbl.configure(fg="#e94560")

        else:
            status_var.set("Invalid license key. Purchase at phonebusted.com")
            status_lbl.configure(fg="#e94560")
            log(f"[License] Invalid key entered: {msg}")

    def do_quit():
        result["ok"] = False
        dlg.destroy()

    tk.Button(btn_frame, text="  Activate  ", command=do_activate,
              bg="#e94560", fg="white", relief="flat",
              font=("Segoe UI", 9, "bold"), padx=6, pady=8,
              cursor="hand2").pack(side=tk.LEFT, padx=8)

    tk.Button(btn_frame, text="  Quit  ", command=do_quit,
              bg="#2a2a4a", fg="#707080", relief="flat",
              font=("Segoe UI", 9), padx=6, pady=8,
              cursor="hand2").pack(side=tk.LEFT, padx=8)

    tk.Label(dlg,
             text="Purchase a key at phonebusted.com  \u2014  $9 one-time payment",
             font=("Segoe UI", 7), bg="#1a1a2e", fg="#404060",
             ).pack(pady=(14, 0))

    dlg.protocol("WM_DELETE_WINDOW", do_quit)
    root.wait_window(dlg)

    if _standalone:
        root.destroy()

    return result["ok"]


# ── Public entry point ────────────────────────────────────────────────────────

def run_license_check(root: tk.Tk) -> bool:
    """
    Full license gate. Must be called on the main tkinter thread.
    Returns True → launch the app, False → exit.

    Rules:
      - No license.key file  →  always show activation dialog
      - license.key exists   →  validate online every launch
          online valid        →  launch
          online invalid      →  show dialog (key revoked)
          offline + grace OK  →  launch
          offline + expired   →  show dialog
    """
    saved_key = _load_key()

    # ── No saved key → first-time user, always show activation ───────────────
    if not saved_key:
        log("[License] No license key found — showing activation dialog.")
        return _show_dialog(root)

    # ── Saved key exists → validate it ───────────────────────────────────────
    log(f"[License] Found saved key — validating online...")
    ok, reason = _check_saved_key()

    if ok:
        log(f"[License] Access granted ({reason}).")
        return True

    if reason.startswith("rejected"):
        log("[License] Saved key revoked by Polar.sh — re-activation required.")
        return _show_dialog(
            root,
            prefill_key=saved_key,
            error_msg="Your license key was rejected. Please check phonebusted.com",
        )

    if reason.startswith("offline_expired"):
        log("[License] Offline grace period expired — re-validation required.")
        return _show_dialog(
            root,
            prefill_key=saved_key,
            error_msg="No internet for 24h+. Connect to validate your license.",
        )

    # Catch-all (e.g. unexpected error) → show fresh dialog
    log(f"[License] Unexpected check failure ({reason}) — showing dialog.")
    return _show_dialog(root)
