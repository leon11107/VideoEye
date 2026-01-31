"""Hex dump viewer for raw packet/NAL unit data."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout, QSpinBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCharFormat, QColor, QTextCursor


class HexViewer(QWidget):
    """Displays hex dump of binary data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = bytes()
        self._bytes_per_line = 16
        self._highlight_start = -1
        self._highlight_end = -1
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Header with controls
        header = QHBoxLayout()
        self._info_label = QLabel("No data")
        header.addWidget(self._info_label)
        header.addStretch()

        header.addWidget(QLabel("Bytes/line:"))
        self._bytes_spin = QSpinBox()
        self._bytes_spin.setRange(8, 32)
        self._bytes_spin.setValue(16)
        self._bytes_spin.valueChanged.connect(self._on_bytes_changed)
        header.addWidget(self._bytes_spin)

        layout.addLayout(header)

        # Hex display
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        # Use monospace font
        font = QFont("Consolas", 10)
        if not font.exactMatch():
            font = QFont("Courier New", 10)
        if not font.exactMatch():
            font = QFont("monospace", 10)
        self._text_edit.setFont(font)

        # Dark theme styling
        self._text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
            }
        """)

        layout.addWidget(self._text_edit)

    def set_data(self, data: bytes, highlight_start: int = -1, highlight_end: int = -1) -> None:
        """Set data to display."""
        self._data = data
        self._highlight_start = highlight_start
        self._highlight_end = highlight_end
        self._update_display()

    def _on_bytes_changed(self, value: int) -> None:
        """Handle bytes per line change."""
        self._bytes_per_line = value
        self._update_display()

    def _update_display(self) -> None:
        """Update the hex dump display."""
        if not self._data:
            self._text_edit.clear()
            self._info_label.setText("No data")
            return

        self._info_label.setText(f"{len(self._data):,} bytes")

        # Build hex dump text with HTML formatting
        lines = []
        offset = 0
        bpl = self._bytes_per_line

        while offset < len(self._data):
            chunk = self._data[offset:offset + bpl]

            # Offset column
            offset_str = f'<span style="color: #569cd6;">{offset:08X}</span>'

            # Hex bytes
            hex_parts = []
            for i, byte in enumerate(chunk):
                byte_offset = offset + i
                # Check if this byte should be highlighted
                if self._highlight_start <= byte_offset < self._highlight_end:
                    hex_parts.append(f'<span style="background-color: #264f78; color: #ffffff;">{byte:02X}</span>')
                else:
                    hex_parts.append(f'{byte:02X}')

            # Pad if necessary
            while len(hex_parts) < bpl:
                hex_parts.append('  ')

            # Group bytes (8 bytes per group)
            hex_groups = []
            for i in range(0, len(hex_parts), 8):
                hex_groups.append(' '.join(hex_parts[i:i+8]))
            hex_str = '  '.join(hex_groups)

            # ASCII column
            ascii_parts = []
            for i, byte in enumerate(chunk):
                byte_offset = offset + i
                char = chr(byte) if 32 <= byte < 127 else '.'
                # Escape HTML characters
                if char == '<':
                    char = '&lt;'
                elif char == '>':
                    char = '&gt;'
                elif char == '&':
                    char = '&amp;'

                if self._highlight_start <= byte_offset < self._highlight_end:
                    ascii_parts.append(f'<span style="background-color: #264f78;">{char}</span>')
                else:
                    ascii_parts.append(char)

            ascii_str = ''.join(ascii_parts)

            # Combine line
            line = f'{offset_str}  {hex_str}  <span style="color: #ce9178;">|{ascii_str}|</span>'
            lines.append(line)

            offset += bpl

        html = '<pre style="margin: 0;">' + '\n'.join(lines) + '</pre>'
        self._text_edit.setHtml(html)

    def set_highlight(self, start: int, end: int) -> None:
        """Set highlight range."""
        self._highlight_start = start
        self._highlight_end = end
        self._update_display()

    def clear_highlight(self) -> None:
        """Clear any highlight."""
        self._highlight_start = -1
        self._highlight_end = -1
        self._update_display()

    def scroll_to_offset(self, offset: int) -> None:
        """Scroll to make the given offset visible."""
        if not self._data or offset < 0:
            return

        # Calculate line number
        line = offset // self._bytes_per_line

        # Move cursor to that line
        cursor = self._text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        for _ in range(line):
            cursor.movePosition(QTextCursor.MoveOperation.Down)
        self._text_edit.setTextCursor(cursor)
        self._text_edit.ensureCursorVisible()

    def clear(self) -> None:
        """Clear the display."""
        self._data = bytes()
        self._highlight_start = -1
        self._highlight_end = -1
        self._text_edit.clear()
        self._info_label.setText("No data")
