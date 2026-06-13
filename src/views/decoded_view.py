"""Decoded frame display view with block-analysis overlays."""

import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea, QSizePolicy
from PyQt6.QtCore import Qt, QSize, QPoint, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap, QPainter, QWheelEvent

from .overlay import OVERLAYS


class _ImageLabel(QLabel):
    """Image label reporting mouse position for block inspection."""

    mouse_moved = pyqtSignal(QPoint)
    mouse_left = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        self.mouse_moved.emit(event.position().toPoint())
        super().mouseMoveEvent(event)

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

        # Scroll area for the image
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
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

    def set_overlays(self, flags: dict) -> None:
        """Enable/disable overlay layers, e.g. {'qp': True, 'mv': False}."""
        for key, value in flags.items():
            if key in self._overlay_flags:
                self._overlay_flags[key] = bool(value)
        if self._rgb is not None:
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
            "px": px,
            "py": py,
            "unit": a.qp_unit,
            "block_x": px // a.qp_unit,
            "block_y": py // a.qp_unit,
            "qp": a.qp_at(px, py),
            "mvs": a.mvs_at(px, py),
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

    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zooming."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

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
