"""Decoded frame display view with block-analysis overlays."""

import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea, QSizePolicy
from PyQt6.QtCore import Qt, QSize, QPoint, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPainter, QWheelEvent

from .overlay import OVERLAYS


class _ImageLabel(QLabel):
    """Image label: reports mouse position for block inspection, and supports
    wheel-zoom and left-drag panning of the decoded view."""

    mouse_moved = pyqtSignal(QPoint)
    mouse_left = pyqtSignal()
    panned = pyqtSignal(int, int)            # mouse movement dx, dy while dragging
    zoom_requested = pyqtSignal(int, QPoint)  # wheel delta, cursor pos in label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._panning = False
        self._pan_last = QPoint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._panning = True
            self._pan_last = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            g = event.globalPosition().toPoint()
            move = g - self._pan_last
            self._pan_last = g
            self.panned.emit(move.x(), move.y())
        else:
            self.mouse_moved.emit(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._panning:
            self._panning = False
            self.unsetCursor()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        # Plain wheel zooms (toward the cursor); consume so the scroll area
        # does not also scroll.
        self.zoom_requested.emit(event.angleDelta().y(), event.position().toPoint())
        event.accept()

    def leaveEvent(self, event):
        self.mouse_left.emit()
        super().leaveEvent(event)


class DecodedView(QWidget):
    """Displays decoded video frames with optional analysis overlays."""

    # Emits a dict describing the hovered block, or None when leaving.
    block_hovered = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None          # composed (frame + overlays), native res
        self._rgb = None             # raw frame for recomposition
        self._analysis = None
        self._overlay_flags = {key: False for key in OVERLAYS}
        self._zoom_factor = 1.0
        self._fit_to_window = True
        self._frame_index = -1
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label at top
        self._info_label = QLabel("No frame loaded")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_label.setStyleSheet("background-color: #333; color: #ccc; padding: 4px;")
        layout.addWidget(self._info_label)

        # Scroll area for the image. Not widget-resizable: we size the label to
        # the scaled pixmap ourselves so that zooming past the viewport produces
        # scrollbars (and thus something to pan). The label is centered when it
        # is smaller than the viewport (fit / zoomed-out).
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Image display label
        self._image_label = _ImageLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._image_label.setStyleSheet("background-color: #1a1a1a;")
        self._image_label.mouse_moved.connect(self._on_mouse_moved)
        self._image_label.mouse_left.connect(
            lambda: self.block_hovered.emit(None)
        )
        self._image_label.panned.connect(self._on_panned)
        self._image_label.zoom_requested.connect(self._on_zoom_requested)

        self._scroll.setWidget(self._image_label)
        layout.addWidget(self._scroll)

    def display_frame(self, rgb_array: np.ndarray, frame_index: int = -1,
                      analysis=None) -> None:
        """Display a decoded frame with optional block analysis."""
        if rgb_array is None:
            self.clear()
            return

        self._rgb = rgb_array
        self._analysis = analysis
        self._frame_index = frame_index
        self._compose()
        self._update_display()
        self._update_info()

    def has_overlays(self) -> bool:
        """True if any analysis overlay is currently enabled."""
        return any(self._overlay_flags.values())

    def set_overlays(self, flags: dict) -> None:
        """Enable/disable overlay layers, e.g. {'qp': True, 'mv': False}."""
        for key, value in flags.items():
            if key in self._overlay_flags:
                self._overlay_flags[key] = bool(value)
        if self._rgb is not None:
            self._compose()
            self._update_display()

    def refresh_overlays(self, analysis=None) -> None:
        """Recompose with (possibly newly filled) analysis, e.g. when the
        block-analysis backend streams in data for the current frame."""
        if analysis is not None:
            self._analysis = analysis
        if self._rgb is not None and any(self._overlay_flags.values()):
            self._compose()
            self._update_display()

    def _compose(self) -> None:
        """Build the native-resolution pixmap: frame + active overlays."""
        height, width, channels = self._rgb.shape
        image = QImage(
            self._rgb.data,
            width, height,
            channels * width,
            QImage.Format.Format_RGB888
        )
        self._pixmap = QPixmap.fromImage(image)

        if self._analysis is None or not any(self._overlay_flags.values()):
            return

        painter = QPainter(self._pixmap)
        try:
            for key, (_label, render) in OVERLAYS.items():
                if self._overlay_flags.get(key):
                    render(painter, self._analysis)
        except Exception as e:
            print(f"Overlay rendering failed: {e}")
        finally:
            painter.end()

    def _on_mouse_moved(self, pos: QPoint) -> None:
        """Map a label-space mouse position to a block info dict."""
        if self._pixmap is None or self._analysis is None:
            return
        shown = self._image_label.pixmap()
        if shown is None or shown.width() == 0 or shown.height() == 0:
            return

        # Label is resized to exactly fit the scaled pixmap.
        px = int(pos.x() * self._pixmap.width() / shown.width())
        py = int(pos.y() * self._pixmap.height() / shown.height())
        if not (0 <= px < self._pixmap.width() and 0 <= py < self._pixmap.height()):
            self.block_hovered.emit(None)
            return

        a = self._analysis
        info = {
            "codec": a.codec,
            "px": px,
            "py": py,
            "unit": a.qp_unit,
            "block_x": px // a.qp_unit,
            "block_y": py // a.qp_unit,
            "qp": a.qp_at(px, py),
            "mvs": a.mvs_at(px, py),
            "block": a.block_at(px, py),
        }
        self.block_hovered.emit(info)

    def _update_display(self) -> None:
        """Update the displayed image based on current zoom/fit settings."""
        if self._pixmap is None:
            return

        if self._fit_to_window:
            # Scale to fit the scroll area while maintaining aspect ratio
            scaled = self._pixmap.scaled(
                self._scroll.viewport().size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self._image_label.setPixmap(scaled)
            self._image_label.resize(scaled.size())
        else:
            # Apply zoom factor
            base_size = self._pixmap.size()
            new_size = QSize(
                int(base_size.width() * self._zoom_factor),
                int(base_size.height() * self._zoom_factor)
            )
            scaled = self._pixmap.scaled(
                new_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self._image_label.setPixmap(scaled)
            self._image_label.resize(scaled.size())

    def set_fit_to_window(self, fit: bool) -> None:
        """Set whether to fit image to window."""
        self._fit_to_window = fit
        if fit:
            self._zoom_factor = 1.0
        self._update_display()
        self._update_info()

    def zoom_in(self) -> None:
        """Zoom in on the image."""
        self._fit_to_window = False
        self._zoom_factor = min(5.0, self._zoom_factor * 1.25)
        self._update_display()
        self._update_info()

    def zoom_out(self) -> None:
        """Zoom out on the image."""
        self._fit_to_window = False
        self._zoom_factor = max(0.1, self._zoom_factor / 1.25)
        self._update_display()
        self._update_info()

    def zoom_100(self) -> None:
        """Reset zoom to 100%."""
        self._fit_to_window = False
        self._zoom_factor = 1.0
        self._update_display()
        self._update_info()

    def _update_info(self) -> None:
        """Update the info label."""
        if self._pixmap:
            width = self._pixmap.width()
            height = self._pixmap.height()
            mode = "Fit" if self._fit_to_window else f"{self._zoom_factor * 100:.0f}%"
            self._info_label.setText(
                f"Frame {self._frame_index} | {width}x{height} | Zoom: {mode}"
            )

    def _on_panned(self, dx: int, dy: int) -> None:
        """Pan the view by dragging: move the scrollbars opposite to the drag
        so the grabbed point follows the cursor."""
        h = self._scroll.horizontalScrollBar()
        v = self._scroll.verticalScrollBar()
        h.setValue(h.value() - dx)
        v.setValue(v.value() - dy)

    def _on_zoom_requested(self, delta: int, label_pos: QPoint) -> None:
        """Wheel zoom keeping the content point under the cursor fixed."""
        if self._pixmap is None:
            return
        shown = self._image_label.pixmap()
        if shown is None or shown.width() == 0 or shown.height() == 0:
            (self.zoom_in if delta > 0 else self.zoom_out)()
            return

        h = self._scroll.horizontalScrollBar()
        v = self._scroll.verticalScrollBar()
        # Cursor position within the viewport, and the content fraction under it.
        vx = label_pos.x() - h.value()
        vy = label_pos.y() - v.value()
        fx = label_pos.x() / shown.width()
        fy = label_pos.y() / shown.height()

        # Leaving fit mode: start from the current on-screen scale so the first
        # wheel step is continuous rather than jumping to 125% of native.
        if self._fit_to_window:
            self._zoom_factor = shown.width() / self._pixmap.width()
            self._fit_to_window = False

        (self.zoom_in if delta > 0 else self.zoom_out)()

        new_shown = self._image_label.pixmap()
        if new_shown is None or new_shown.width() == 0:
            return
        # Re-pin the same content fraction to the same viewport position.
        h.setValue(int(fx * new_shown.width() - vx))
        v.setValue(int(fy * new_shown.height() - vy))

    def wheelEvent(self, event: QWheelEvent):
        """Wheel over the surrounding area (not the image) also zooms."""
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        event.accept()

    def resizeEvent(self, event):
        """Handle resize to update fit-to-window display."""
        super().resizeEvent(event)
        if self._fit_to_window and self._pixmap:
            self._update_display()

    def clear(self) -> None:
        """Clear the displayed frame."""
        self._pixmap = None
        self._rgb = None
        self._analysis = None
        self._frame_index = -1
        self._image_label.clear()
        self._info_label.setText("No frame loaded")
