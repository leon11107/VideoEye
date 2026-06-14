"""Frame bar chart visualization."""

from PyQt6.QtWidgets import QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QMouseEvent, QWheelEvent, QPolygon

from ..core.frame_info import FrameInfo, FrameType


class BarChartWidget(QWidget):
    """Widget that draws the actual bar chart."""

    frame_selected = pyqtSignal(int)  # Emits frame index

    # Colors for frame types
    COLORS = {
        FrameType.I: QColor(220, 50, 50),    # Red for I-frames
        FrameType.P: QColor(50, 100, 220),   # Blue for P-frames
        FrameType.B: QColor(50, 180, 50),    # Green for B-frames
        FrameType.UNKNOWN: QColor(150, 150, 150),  # Gray for unknown
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: list[FrameInfo] = []
        self._bar_width = 4
        self._bar_spacing = 1
        self._max_frame_size = 1
        self._selected_index = -1
        self._hover_index = -1

        self.setMouseTracking(True)
        self.setMinimumHeight(100)

    def set_frames(self, frames: list[FrameInfo]) -> None:
        """Set frame data for visualization."""
        self._frames = frames
        self._selected_index = -1
        self._hover_index = -1

        if frames:
            self._max_frame_size = max(f.size for f in frames)
        else:
            self._max_frame_size = 1

        self._update_size()
        self.update()

    def set_bar_width(self, width: int) -> None:
        """Set bar width in pixels."""
        self._bar_width = max(1, min(20, width))
        self._update_size()
        self.update()

    def _update_size(self) -> None:
        """Update widget size based on frame count."""
        total_width = len(self._frames) * (self._bar_width + self._bar_spacing)
        self.setMinimumWidth(max(100, total_width))

    def paintEvent(self, event):
        """Paint the bar chart."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        height = rect.height()
        available_height = height - 20  # Leave space for bottom margin

        # Background
        painter.fillRect(rect, QColor(30, 30, 30))

        if not self._frames:
            painter.setPen(QColor(100, 100, 100))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No frames loaded")
            return

        # Draw only the bars intersecting the dirty region. At 100k frames the
        # widget is far wider than the viewport, so iterating every bar each
        # repaint (and on every hover) is the dominant cost; clip to the range
        # that actually needs painting.
        step = self._bar_width + self._bar_spacing
        dirty = event.rect()
        first = max(0, (dirty.left() - 5) // step)
        last = min(len(self._frames) - 1, (dirty.right() - 5) // step)

        for i in range(first, last + 1):
            frame = self._frames[i]
            x = 5 + i * step

            # Calculate bar height proportional to frame size
            bar_height = int((frame.size / self._max_frame_size) * available_height)
            bar_height = max(2, bar_height)  # Minimum visible height

            # Get color for frame type
            color = self.COLORS.get(frame.frame_type, self.COLORS[FrameType.UNKNOWN])

            # Highlight selected frame
            if i == self._selected_index:
                color = color.lighter(140)
                # Draw selection indicator
                painter.setPen(QPen(QColor(255, 255, 255), 2))
                painter.drawRect(x - 1, height - bar_height - 11, self._bar_width + 2, bar_height + 2)

            # Highlight hovered frame
            elif i == self._hover_index:
                color = color.lighter(120)

            # Draw the bar
            painter.fillRect(
                x, height - bar_height - 10,
                self._bar_width, bar_height,
                color
            )

            # Draw keyframe indicator (small triangle at top)
            if frame.is_keyframe:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 0))
                triangle_size = min(self._bar_width, 6)
                triangle = QPolygon([
                    QPoint(x + self._bar_width // 2, height - bar_height - 15),
                    QPoint(x + self._bar_width // 2 - triangle_size // 2, height - bar_height - 10),
                    QPoint(x + self._bar_width // 2 + triangle_size // 2, height - bar_height - 10),
                ])
                painter.drawPolygon(triangle)

        # Draw legend
        self._draw_legend(painter, rect)

    def _draw_legend(self, painter: QPainter, rect: QRect) -> None:
        """Draw frame type legend (no-op, legend is a separate widget now)."""
        pass

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse click to select frame."""
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._get_frame_at_pos(event.pos().x())
            if index >= 0:
                old = self._selected_index
                self._selected_index = index
                for i in (old, index):
                    if i >= 0:
                        self.update(self._bar_rect(i))
                self.frame_selected.emit(index)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move for hover effect."""
        index = self._get_frame_at_pos(event.pos().x())
        if index != self._hover_index:
            old = self._hover_index
            self._hover_index = index
            # Repaint only the two affected bars, not the whole (100k-wide)
            # widget, so hover stays cheap while scrubbing the chart.
            for i in (old, index):
                if i >= 0:
                    self.update(self._bar_rect(i))

            # Show tooltip with frame info
            if index >= 0 and index < len(self._frames):
                frame = self._frames[index]
                tooltip = (f"Frame {index}\n"
                          f"Type: {frame.frame_type.value}\n"
                          f"Size: {frame.size:,} bytes\n"
                          f"Keyframe: {'Yes' if frame.is_keyframe else 'No'}")
                self.setToolTip(tooltip)
            else:
                self.setToolTip("")

    def leaveEvent(self, event):
        """Handle mouse leaving widget."""
        self._hover_index = -1
        self.update()

    def _bar_rect(self, index: int) -> QRect:
        """Full-height repaint rect for one bar (covers its selection border
        and keyframe triangle)."""
        step = self._bar_width + self._bar_spacing
        x = 5 + index * step
        return QRect(x - 2, 0, self._bar_width + 4, self.height())

    def _get_frame_at_pos(self, x: int) -> int:
        """Get frame index at x position."""
        x -= 5  # Account for left margin
        if x < 0:
            return -1
        index = x // (self._bar_width + self._bar_spacing)
        if 0 <= index < len(self._frames):
            return index
        return -1

    def select_frame(self, index: int) -> None:
        """Programmatically select a frame."""
        if 0 <= index < len(self._frames):
            old = self._selected_index
            self._selected_index = index
            for i in (old, index):
                if i >= 0:
                    self.update(self._bar_rect(i))

    @property
    def selected_index(self) -> int:
        """Get currently selected frame index."""
        return self._selected_index


class LegendWidget(QWidget):
    """Fixed legend panel showing frame type colors."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(70)
        self.setMinimumHeight(60)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        painter.setPen(QColor(200, 200, 200))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        items = [
            (BarChartWidget.COLORS[FrameType.I], "I-frame"),
            (BarChartWidget.COLORS[FrameType.P], "P-frame"),
            (BarChartWidget.COLORS[FrameType.B], "B-frame"),
        ]

        y = 8
        for color, label in items:
            painter.fillRect(6, y, 10, 10, color)
            painter.drawText(20, y + 9, label)
            y += 18


class BarChartView(QWidget):
    """Scrollable bar chart view with a fixed legend on the left."""

    frame_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Fixed legend on the left
        self._legend = LegendWidget()
        layout.addWidget(self._legend)

        # Scroll area for the chart
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        # Chart widget
        self._chart = BarChartWidget()
        self._chart.frame_selected.connect(self.frame_selected)

        self._scroll.setWidget(self._chart)
        layout.addWidget(self._scroll)

    def set_frames(self, frames: list[FrameInfo]) -> None:
        """Set frame data for visualization."""
        self._chart.set_frames(frames)

    def select_frame(self, index: int) -> None:
        """Select a frame and scroll to make it visible."""
        self._chart.select_frame(index)

        # Scroll to make selected frame visible
        if index >= 0:
            bar_width = self._chart._bar_width + self._chart._bar_spacing
            x_pos = index * bar_width
            self._scroll.horizontalScrollBar().setValue(
                max(0, x_pos - self._scroll.viewport().width() // 2)
            )

    def set_bar_width(self, width: int) -> None:
        """Set bar width."""
        self._chart.set_bar_width(width)

    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zooming."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom with Ctrl+wheel
            delta = event.angleDelta().y()
            current_width = self._chart._bar_width
            if delta > 0:
                self._chart.set_bar_width(current_width + 1)
            else:
                self._chart.set_bar_width(current_width - 1)
            event.accept()
        else:
            # Normal horizontal scroll
            super().wheelEvent(event)

    def clear(self) -> None:
        """Clear the chart."""
        self._chart.set_frames([])

    @property
    def selected_index(self) -> int:
        """Get selected frame index."""
        return self._chart.selected_index
