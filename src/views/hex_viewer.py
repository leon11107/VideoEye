"""Hex dump viewer for raw packet/NAL unit data."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout, QSpinBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCharFormat, QColor, QTextCursor


class HexViewer(QWidget):
    """Displays hex dump of binary data."""

    # Max bytes rendered at once. A 4K intra frame's packet can be 1-2 MB;
    # rendering it all as per-byte HTML spans froze the UI on every frame
    # select. We render a bounded window (with absolute offsets) and recenter
    # it on the selected NAL unit / scrolled-to offset instead.
    WINDOW_BYTES = 64 * 1024

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = bytes()
        self._bytes_per_line = 16
        self._highlight_start = -1
        self._highlight_end = -1
        self._win_start = 0  # byte offset of the first rendered line
        # Absolute byte offset of self._data[0] in the source file, added to
        # the rendered address column so it reads as the packet's position in
        # the bitstream rather than 0-based within this packet. Highlight and
        # scroll offsets stay relative to self._data.
        self._base_addr = 0
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

    def set_data(self, data: bytes, highlight_start: int = -1,
                 highlight_end: int = -1, base_addr: int = 0) -> None:
        """Set data to display.

        `base_addr` is the absolute file offset of `data[0]`; it is added to
        the displayed address column only. `highlight_*` stay relative to
        `data` (0-based within the packet).
        """
        self._data = data
        self._base_addr = base_addr if base_addr and base_addr > 0 else 0
        self._highlight_start = highlight_start
        self._highlight_end = highlight_end
        self._win_start = self._window_for(highlight_start if highlight_start >= 0 else 0)
        self._update_display()

    def _window_for(self, center: int) -> int:
        """Byte offset (line-aligned) where a window centred on `center` starts."""
        bpl = self._bytes_per_line
        max_start = max(0, len(self._data) - self.WINDOW_BYTES)
        start = max(0, center - self.WINDOW_BYTES // 2)
        start = min(start, max_start)
        return (start // bpl) * bpl  # align to a line boundary

    def _on_bytes_changed(self, value: int) -> None:
        """Handle bytes per line change."""
        self._bytes_per_line = value
        self._win_start = self._window_for(self._win_start)  # re-align to bpl
        self._update_display()

    def _update_display(self) -> None:
        """Update the hex dump display."""
        if not self._data:
            self._text_edit.clear()
            self._info_label.setText("No data")
            return

        bpl = self._bytes_per_line
        total = len(self._data)
        win_start = self._win_start
        win_end = min(total, win_start + self.WINDOW_BYTES)
        addr = f"@ 0x{self._base_addr:08X}  " if self._base_addr else ""
        if win_start > 0 or win_end < total:
            self._info_label.setText(
                f"{addr}{total:,} bytes (showing {win_start:,}-{win_end:,})"
            )
        else:
            self._info_label.setText(f"{addr}{total:,} bytes")

        # Build hex dump text with HTML formatting, for the window only.
        lines = []
        offset = win_start

        while offset < win_end:
            chunk = self._data[offset:offset + bpl]

            # Offset column (absolute file address: base + window offset)
            offset_str = (f'<span style="color: #569cd6;">'
                          f'{self._base_addr + offset:08X}</span>')

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
        if start >= 0:
            self._win_start = self._window_for(start)
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

        # Re-render the window around the offset if it falls outside it.
        if not (self._win_start <= offset < self._win_start + self.WINDOW_BYTES):
            self._win_start = self._window_for(offset)
            self._update_display()

        # Line within the current window; jump straight to that block (O(1))
        # rather than stepping the cursor down line by line.
        line = (offset - self._win_start) // self._bytes_per_line
        block = self._text_edit.document().findBlockByLineNumber(line)
        if block.isValid():
            cursor = self._text_edit.textCursor()
            cursor.setPosition(block.position())
            self._text_edit.setTextCursor(cursor)
            self._text_edit.ensureCursorVisible()

    def clear(self) -> None:
        """Clear the display."""
        self._data = bytes()
        self._highlight_start = -1
        self._highlight_end = -1
        self._win_start = 0
        self._text_edit.clear()
        self._info_label.setText("No data")
