#!/usr/bin/env python3
"""VideoEye - Video Analysis Tool

A video stream analyzer similar to Elecard StreamEye, supporting
H.264/AVC and H.265/HEVC analysis with multiple synchronized views.
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor

from src.app import MainWindow


def setup_dark_theme(app: QApplication) -> None:
    """Apply dark theme to application."""
    app.setStyle("Fusion")

    palette = QPalette()

    # Base colors
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Text, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    # Disabled colors
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor(127, 127, 127))

    app.setPalette(palette)

    # Additional stylesheet
    app.setStyleSheet("""
        QToolTip {
            background-color: #2d2d2d;
            color: #d4d4d4;
            border: 1px solid #3c3c3c;
            padding: 4px;
        }
        QMenuBar {
            background-color: #2d2d2d;
            border-bottom: 1px solid #3c3c3c;
        }
        QMenuBar::item:selected {
            background-color: #3c3c3c;
        }
        QMenu {
            background-color: #2d2d2d;
            border: 1px solid #3c3c3c;
        }
        QMenu::item:selected {
            background-color: #094771;
        }
        QToolBar {
            background-color: #2d2d2d;
            border: none;
            spacing: 4px;
            padding: 4px;
        }
        QToolBar::separator {
            background-color: #3c3c3c;
            width: 1px;
            margin: 4px 8px;
        }
        QStatusBar {
            background-color: #007acc;
            color: white;
        }
        QDockWidget {
            titlebar-close-icon: none;
            titlebar-normal-icon: none;
        }
        QDockWidget::title {
            background-color: #2d2d2d;
            padding: 6px;
            border-bottom: 1px solid #3c3c3c;
        }
        QScrollBar:vertical {
            background-color: #1e1e1e;
            width: 12px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background-color: #5a5a5a;
            min-height: 20px;
            border-radius: 4px;
            margin: 2px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #787878;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar:horizontal {
            background-color: #1e1e1e;
            height: 12px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background-color: #5a5a5a;
            min-width: 20px;
            border-radius: 4px;
            margin: 2px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #787878;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QGroupBox {
            border: 1px solid #3c3c3c;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 4px;
            color: #d4d4d4;
        }
    """)


def main():
    """Application entry point."""
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("VideoEye")
    app.setOrganizationName("VideoEye")

    # Apply dark theme
    setup_dark_theme(app)

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
