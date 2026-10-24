"""Helpers for background calls of Windows command-line programs."""

from __future__ import annotations

import subprocess
import sys

# Without this flag Windows briefly opens a black window for every console program
# (wevtutil, ARP, PING), because the backend itself has no console.
NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
