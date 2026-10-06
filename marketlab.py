"""
MarketLab — start the app:   python3 marketlab.py
Then open http://127.0.0.1:8050 (it opens automatically).

The code lives in app/; this launcher just puts it on the import path and runs app/server.py.
"""

import runpy
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP_DIR))

if __name__ == "__main__":
    runpy.run_path(str(APP_DIR / "server.py"), run_name="__main__")
