"""Run from repo root: python run_scanner.py"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    try:
        from sector_scanner.run_scanner import ensure_utf8_console, main
    except ImportError:
        from pathlib import Path

        _root = Path(__file__).resolve().parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from sector_scanner.run_scanner import ensure_utf8_console, main

    ensure_utf8_console()
    raise SystemExit(main())
