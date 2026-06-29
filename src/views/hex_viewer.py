"""Hex dump viewer for raw packet/NAL unit data.

The dump is split across two panes inside a draggable QSplitter: the left
pane holds the address column and hex bytes, the right pane the matching
ASCII text. The two scroll together (one row per line in both), so dragging
the divider only trades horizontal width between hex and ASCII.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout, QSpinBox, QSplitter
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ..theme import current_theme


class HexViewer(QWidget):
    """Displays hex dump of binary data."""

    # Max bytes rendered at once. A 4K intra frame's packet can be 1-2 MB;
    # rendering it all as per-byte HTML spans froze the UI on every frame
    # select. We render a bounded window (with absolute offsets) and recenter
    # it on the selected NAL unit / scrolled-to offset instead. 16 KB (1024
    # lines) renders fast while showing plenty of context around the selection.
    WINDOW_BYTES = 16 * 1024

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
        self._syncing = False  # guard against scroll-sync recursion
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

        # Shared monospace font so both panes have identical line heights
        # (a prerequisite for pixel-accurate scroll syncing).
        font = QFont("Consolas", 10)
        if not font.exactMatch():
            font = QFont("Courier New", 10)
        if not font.exactMatch():
            font = QFont("monospace", 10)

        # Left pane: address column + hex bytes. Right pane: ASCII text.
        self._hex_edit = QTextEdit()
        self._ascii_edit = QTextEdit()
        for edit in (self._hex_edit, self._ascii_edit):
            edit.setReadOnly(True)
            edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            edit.setFont(font)
        self.apply_theme()

        # The draggable divider trades width between hex and ASCII.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._hex_edit)
        self._splitter.addWidget(self._ascii_edit)
        self._splitter.setStretchFactor(0, 3)  # hex pane gets most width
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([600, 220])
        layout.addWidget(self._splitter)

        # Keep the two panes scrolled in lock-step.
        self._hex_edit.verticalScrollBar().valueChanged.connect(
            lambda v: self._mirror_scroll(self._ascii_edit, v))
        self._ascii_edit.verticalScrollBar().valueChanged.connect(
            lambda v: self._mirror_scroll(self._hex_edit, v))

    def _mirror_scroll(self, target: QTextEdit, value: int) -> None:
        """Apply one pane's vertical scroll position to the other."""
        if self._syncing:
            return
        self._syncing = True
        target.verticalScrollBar().setValue(value)
        self._syncing = False

    def apply_theme(self) -> None:
        """Theme the two text panes (and re-render so default text colour and
        spans update)."""
        t = current_theme()
        style = (f"QTextEdit {{ background-color: {t.hx(t.base)};"
                 f" color: {t.hx(t.text)};"
                 f" border: 1px solid {t.hx(t.border)}; }}")
        self._hex_edit.setStyleSheet(style)
        self._ascii_edit.setStyleSheet(style)
        if getattr(self, "_data", None):
            self._update_display()

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
            self._hex_edit.clear()
            self._ascii_edit.clear()
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

        # Build the hex pane (address + bytes) and ASCII pane line by line,
        # for the current window only. The two lists stay row-aligned.
        hex_lines = []
        ascii_lines = []
        offset = win_start

        hl_s, hl_e = self._highlight_start, self._highlight_end
        while offset < win_end:
            chunk = self._data[offset:offset + bpl]
            line_end = offset + len(chunk)
            # Whole-line highlight state, so a fully-selected line (the common
            # case inside a large OBU) emits a single span instead of one per
            # byte -- the difference between ~1k and ~16k spans per window.
            full = hl_e > hl_s and hl_s <= offset and line_end <= hl_e
            empty = hl_s < 0 or hl_e <= offset or hl_s >= line_end

            # Offset column (absolute file address: base + window offset)
            offset_str = (f'<span style="color: #569cd6;">'
                          f'{self._base_addr + offset:08X}</span>')

            # Hex bytes
            if empty or full:
                hex_parts = [f'{b:02X}' for b in chunk]
            else:
                hex_parts = [
                    (f'<span style="background-color: #264f78; color: #ffffff;">'
                     f'{b:02X}</span>'
                     if hl_s <= offset + i < hl_e else f'{b:02X}')
                    for i, b in enumerate(chunk)]
            while len(hex_parts) < bpl:
                hex_parts.append('  ')
            hex_groups = [' '.join(hex_parts[i:i + 8])
                          for i in range(0, len(hex_parts), 8)]
            hex_str = '  '.join(hex_groups)
            if full:
                hex_str = (f'<span style="background-color: #264f78;'
                           f' color: #ffffff;">{hex_str}</span>')
            hex_lines.append(f'{offset_str}  {hex_str}')

            # ASCII column
            ascii_chars = []
            for b in chunk:
                ch = chr(b) if 32 <= b < 127 else '.'
                ascii_chars.append({'<': '&lt;', '>': '&gt;', '&': '&amp;'}.get(ch, ch))
            if empty or full:
                ascii_str = ''.join(ascii_chars)
                if full:
                    ascii_str = (f'<span style="background-color: #264f78;">'
                                 f'{ascii_str}</span>')
            else:
                ascii_str = ''.join(
                    (f'<span style="background-color: #264f78;">{c}</span>'
                     if hl_s <= offset + i < hl_e else c)
                    for i, c in enumerate(ascii_chars))
            ascii_lines.append(
                f'<span style="color: #ce9178;">{ascii_str}</span>')

            offset += bpl

        self._hex_edit.setHtml(
            '<pre style="margin: 0;">' + '\n'.join(hex_lines) + '</pre>')
        self._ascii_edit.setHtml(
            '<pre style="margin: 0;">' + '\n'.join(ascii_lines) + '</pre>')

    def set_highlight(self, start: int, end: int) -> None:
        """Set highlight range."""
        new_win = self._window_for(start) if start >= 0 else self._win_start
        # Re-rendering the window is the expensive step; skip it when neither the
        # highlight nor the window actually change (e.g. clicking different
        # fields of the same already-selected OBU).
        if (start == self._highlight_start and end == self._highlight_end
                and new_win == self._win_start):
            return
        self._highlight_start = start
        self._highlight_end = end
        self._win_start = new_win
        self._update_display()

    def clear_highlight(self) -> None:
        """Clear any highlight."""
        self._highlight_start = -1
        self._highlight_end = -1
        self._update_display()

    def scroll_to_offset(self, offset: int) -> None:
        """Scroll to make the given offset visible (both panes)."""
        if not self._data or offset < 0:
            return

        # Re-render the window around the offset if it falls outside it.
        if not (self._win_start <= offset < self._win_start + self.WINDOW_BYTES):
            self._win_start = self._window_for(offset)
            self._update_display()

        # Line within the current window; jump straight to that block (O(1))
        # rather than stepping the cursor down line by line. Scroll the hex
        # pane; the scroll-sync mirrors it onto the ASCII pane.
        line = (offset - self._win_start) // self._bytes_per_line
        block = self._hex_edit.document().findBlockByLineNumber(line)
        if block.isValid():
            cursor = self._hex_edit.textCursor()
            cursor.setPosition(block.position())
            self._hex_edit.setTextCursor(cursor)
            self._hex_edit.ensureCursorVisible()

    def clear(self) -> None:
        """Clear the display."""
        self._data = bytes()
        self._highlight_start = -1
        self._highlight_end = -1
        self._win_start = 0
        self._hex_edit.clear()
        self._ascii_edit.clear()
        self._info_label.setText("No data")
