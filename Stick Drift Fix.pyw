"""Double-click to start Stick Drift Fix (runs without a console window)."""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from stickfix.app import main
except ImportError as e:
    ctypes.windll.user32.MessageBoxW(
        None,
        f"A required package is missing ({e.name or e}).\n\n"
        "Open a terminal in this folder and run:\n    py -m pip install -r requirements.txt",
        "Stick Drift Fix", 0x10)
    sys.exit(1)

sys.exit(main())
