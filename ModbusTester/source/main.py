#!/usr/bin/env python3
"""
Modbus Tester — a lightweight utility for reading and writing
Modbus TCP registers with a modern PySide6 interface.
"""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from main_window import MainWindow
from styles import STYLESHEET


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    # Set a reasonable default font
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
