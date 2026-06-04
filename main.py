#!/usr/bin/env python3
"""
Radar Coverage Analysis Tool — Main entry point.

Launches the PyQt6 GUI application.
"""

import sys
from src.gui import MainWindow

if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
