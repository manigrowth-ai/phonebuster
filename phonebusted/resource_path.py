"""
resource_path.py - Resolve asset paths for both dev and PyInstaller bundle.

In dev:          returns  <repo>/phonebusted/<rel>
In --onefile:    returns  sys._MEIPASS/<rel>   (the temp extraction dir)
"""

import os
import sys


def resource_path(rel: str) -> str:
    """Return the absolute path to a bundled resource."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)
