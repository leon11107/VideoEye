"""Decoded frame display view."""

import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea, QSizePolicy
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QImage, QPixmap, QPainter, QWheelEvent


class DecodedView(QWidget):
    """Displays decoded video frames."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
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
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._image_label.setStyleSheet("background-color: #1a1a1a;")

        self._scroll.setWidget(self._image_label)
        layout.addWidget(self._scroll)

    def display_frame(self, rgb_array: np.ndarray, frame_index: int = -1) -> None:
        """Display a decoded frame from RGB numpy array."""
        if rgb_array is None:
            self.clear()
            return

        height, width, channels = rgb_array.shape

        # Create QImage from numpy array
        bytes_per_line = channels * width
        image = QImage(
            rgb_array.data,
            width, height,
            bytes_per_line,
            QImage.Format.Format_RGB888
        )

        self._pixmap = QPixmap.fromImage(image)
        self._frame_index = frame_index
        self._update_display()

        # Update info label
        self._info_label.setText(
            f"Frame {frame_index} | {width}x{height} | "
            f"Zoom: {self._zoom_factor * 100:.0f}%"
        )

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
        self._frame_index = -1
        self._image_label.clear()
        self._info_label.setText("No frame loaded")
