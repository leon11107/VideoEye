#!/usr/bin/env python3
"""VideoEye - Video Analysis Tool

A video stream analyzer similar to Elecard StreamEye, supporting
H.264/AVC and H.265/HEVC analysis with multiple synchronized views.
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QSettings

from src.app import MainWindow
from src.theme import apply_theme_to_app, set_current_theme


def main():
    """Application entry point."""
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("VideoEye")
    app.setOrganizationName("VideoEye")

    # Apply the saved theme (dark by default).
    name = QSettings().value("theme", "dark")
    apply_theme_to_app(app, set_current_theme(name))

    # Create and show main window
    window = MainWindow()
    window.show()

    # Handle command line argument for file
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        window._load_file(file_path)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
