"""
conftest.py — pytest configuration for the radar coverage tool test suite.

Stubs out PyQt6 before any test module is collected so that control_panel.py
(and any other GUI module) can be imported in a headless / CI environment
without a Qt display or valid DLL path.
"""
import sys
from unittest.mock import MagicMock

# Only install the stubs when PyQt6 cannot be imported normally (e.g. headless CI).
# If a real Qt installation is present and functional, leave it untouched so that
# integration / GUI tests can still use real widgets.
try:
    import PyQt6.QtWidgets  # noqa: F401 — try to load the real thing
except Exception:
    # Real PyQt6 unavailable (headless, missing DLL, etc.) — install stubs.
    _qt_mods = [
        "PyQt6",
        "PyQt6.QtWidgets",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
    ]
    for _mod in _qt_mods:
        if _mod not in sys.modules:
            sys.modules[_mod] = MagicMock()
