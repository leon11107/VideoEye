"""Frame bar chart visualization."""

import math

from PyQt6.QtWidgets import QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QRectF, QPointF
from PyQt6.QtGui import (QPainter, QColor, QPen, QMouseEvent, QWheelEvent,
                         QPolygonF)

from ..core.frame_info import FrameInfo, FrameType
from ..theme import current_theme


class BarChartWidget(QWidget):
    """Widget that draws the actual bar chart."""

    frame_selected = pyqtSignal(int)  # Emits frame index
    hover_changed = pyqtSignal(int)   # Emits hovered frame index (-1 = none)

    # Colors for frame types
    COLORS = {
        FrameType.I: QColor(220, 50, 50),    # Red for I-frames
        FrameType.P: QColor(50, 100, 220),   # Blue for P-frames
        FrameType.B: QColor(50, 180, 50),    # Green for B-frames
        FrameType.UNKNOWN: QColor(150, 150, 150),  # Gray for unknown
    }
    # AV1 hidden (no-show) frames -- decoded but never displayed on their own
    # (show_frame == 0). Drawn gray so they read as "not shown" rather than as
    # an ordinary inter (P) frame.
    NOSHOW_COLOR = QColor(115, 115, 120)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: list[FrameInfo] = []
        self._bar_width = 8  # default bar width (2x the original 4)
        self._bar_spacing = 1
        self._max_frame_size = 1
        self._max_bitrate = 1  # peak instantaneous bitrate (bps), for the line
        self._show_bitrate = False  # off by default; toggled via legend
        self._selected_index = -1
        self._hover_index = -1
        # Reference frames of the selected frame (decode-order indices), per
        # list; marked on the chart with circled ref-index numbers.
        self._ref_l0: list[int] = []
        self._ref_l1: list[int] = []

        self.setMouseTracking(True)
        self.setMinimumHeight(100)

    def set_ref_markers(self, l0: list[int], l1: list[int]) -> None:
        """Mark the selected frame's L0 (blue) / L1 (green) reference frames."""
        if l0 == self._ref_l0 and l1 == self._ref_l1:
            return
        self._ref_l0 = list(l0)
        self._ref_l1 = list(l1)
        self.update()

    def set_frames(self, frames: list[FrameInfo]) -> None:
        """Set frame data for visualization."""
        self._frames = frames
        self._selected_index = -1
        self._hover_index = -1
        self._ref_l0 = []
        self._ref_l1 = []

        if frames:
            self._max_frame_size = max(f.size for f in frames)
            self._max_bitrate = max((f.instant_bitrate for f in frames),
                                    default=0) or 1
        else:
            self._max_frame_size = 1
            self._max_bitrate = 1

        self._update_size()
        self.update()

    @property
    def max_frame_size(self) -> int:
        return self._max_frame_size

    @property
    def max_bitrate(self) -> int:
        return self._max_bitrate

    def set_bar_width(self, width: int) -> None:
        """Set bar width in pixels."""
        self._bar_width = max(1, min(20, width))
        self._update_size()
        self.update()

    # ---- device-pixel-snapped bar grid --------------------------------- #
    # On fractional device-pixel ratios (Windows 125%/150% scaling) an integer
    # logical step like 9px maps to e.g. 13.5 device px, so per-bar rounding
    # makes odd/even bars render 1px wider/narrower. Snap a *constant* device
    # step + width and route every position through these helpers so all bars
    # and gaps render identically and stay mutually aligned.

    def _dpr(self) -> float:
        r = self.devicePixelRatioF()
        return r if r > 0 else 1.0

    def _metrics(self):
        """(left, step, bar_width) in logical px, snapped so each x*dpr is an
        integer device pixel."""
        dpr = self._dpr()
        step_dev = max(2, round((self._bar_width + self._bar_spacing) * dpr))
        bw_dev = min(max(1, round(self._bar_width * dpr)), step_dev - 1)
        left_dev = round(5 * dpr)
        return left_dev / dpr, step_dev / dpr, bw_dev / dpr

    def _bar_x(self, i: int) -> float:
        left, step, _ = self._metrics()
        return left + i * step

    def _bar_w(self) -> float:
        return self._metrics()[2]

    def _bar_cx(self, i: int) -> float:
        left, step, bw = self._metrics()
        return left + i * step + bw / 2.0

    def _index_at(self, xpix: float) -> int:
        left, step, _ = self._metrics()
        if xpix < left:
            return -1
        return int((xpix - left) / step)

    def _update_size(self) -> None:
        """Update widget size based on frame count."""
        left, step, _ = self._metrics()
        total_width = int(left + len(self._frames) * step + 5)
        self.setMinimumWidth(max(100, total_width))

    def paintEvent(self, event):
        """Paint the bar chart."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = current_theme()
        rect = self.rect()
        height = rect.height()
        available_height = height - 20  # Leave space for bottom margin

        # Background
        painter.fillRect(rect, t.chart_bg)

        if not self._frames:
            painter.setPen(t.chart_text_dim)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No frames loaded")
            return

        # Draw only the bars intersecting the dirty region. At 100k frames the
        # widget is far wider than the viewport, so iterating every bar each
        # repaint (and on every hover) is the dominant cost; clip to the range
        # that actually needs painting.
        dirty = event.rect()
        first = max(0, self._index_at(dirty.left()))
        last_idx = self._index_at(dirty.right())
        last = (len(self._frames) - 1 if last_idx < 0
                else min(len(self._frames) - 1, last_idx))

        bw = self._bar_w()
        # Bars: aliased fills on the device-snapped grid (uniform widths/gaps).
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for i in range(first, last + 1):
            frame = self._frames[i]
            x = self._bar_x(i)

            bar_height = int((frame.size / self._max_frame_size) * available_height)
            bar_height = max(2, bar_height)  # Minimum visible height

            color = self.COLORS.get(frame.frame_type, self.COLORS[FrameType.UNKNOWN])
            # AV1 hidden (no-show) frames render gray to stand apart from P
            # frames; show_frame is None for codecs without the concept.
            if frame.show_frame is False:
                color = self.NOSHOW_COLOR
            # Subtle bar lightening; the precise position is marked by the
            # vertical cursor lines drawn on top (see _draw_cursors).
            if i == self._selected_index:
                color = color.lighter(140)
            elif i == self._hover_index:
                color = color.lighter(120)

            painter.fillRect(
                QRectF(x, height - bar_height - 10, bw, bar_height), color)

            # Keyframe indicator (small triangle at top); antialiased.
            if frame.is_keyframe:
                cx = self._bar_cx(i)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 0))
                ts = min(bw, 6)
                painter.drawPolygon(QPolygonF([
                    QPointF(cx, height - bar_height - 15),
                    QPointF(cx - ts / 2, height - bar_height - 10),
                    QPointF(cx + ts / 2, height - bar_height - 10),
                ]))
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Instantaneous-bitrate polyline overlaid on the size bars.
        if self._show_bitrate:
            self._draw_bitrate_line(painter, first, last, height,
                                    available_height)

        # Reference-frame markers/arrows for the selected frame.
        self._draw_ref_markers(painter, first, last, height, available_height)

        # Elecard-style position cursors, drawn on top of everything.
        self._draw_cursors(painter, height)

        # Draw legend
        self._draw_legend(painter, rect)

    def _draw_cursors(self, painter: QPainter, height: int) -> None:
        """Vertical position cursors (Elecard-style): a single black line marks
        the current decoded/selected frame, a double black line marks the frame
        under the mouse. A light halo keeps the black lines crisp on the dark
        chart background."""
        t = current_theme()

        def vline(i: int, double: bool) -> None:
            cx = self._bar_cx(i)
            # Hover = two thin lines; current frame = one slightly bolder line.
            offsets = (-1.5, 1.5) if double else (0.0,)
            halo_w = 2 if double else 3
            core_w = 1 if double else 2
            for o in offsets:
                x = int(round(cx + o))
                painter.setPen(QPen(t.cursor_halo, halo_w))
                painter.drawLine(x, 0, x, height)
                painter.setPen(QPen(t.cursor_core, core_w))
                painter.drawLine(x, 0, x, height)

        if 0 <= self._selected_index < len(self._frames):
            vline(self._selected_index, False)
        if (0 <= self._hover_index < len(self._frames)
                and self._hover_index != self._selected_index):
            vline(self._hover_index, True)

    def _bar_top(self, i: int, height: int, available_height: int) -> int:
        """Y of the top of frame i's bar (the marker anchors just above it)."""
        bh = max(2, int(self._frames[i].size / self._max_frame_size * available_height))
        return height - bh - 10

    def _bitrate_y(self, i: int, height: int, available_height: int) -> int:
        """Y of the bitrate point for frame i (shares the bars' plot area)."""
        norm = self._frames[i].instant_bitrate / self._max_bitrate
        norm = max(0.0, min(1.0, norm))
        return height - 10 - int(norm * available_height)

    def _draw_bitrate_line(self, painter: QPainter, first: int,
                           last: int, height: int, available_height: int) -> None:
        """Polyline of each frame's instantaneous bitrate (bps), normalized to
        the stream peak. Drawn one segment past the dirty range each side so the
        line stays continuous under clipped (per-bar) repaints."""
        if self._max_bitrate <= 0 or len(self._frames) < 2:
            return
        painter.setPen(QPen(QColor(255, 200, 40), 1))  # amber, over the bars
        i0 = max(0, first - 1)
        i1 = min(len(self._frames) - 1, last + 1)
        prev = None
        for i in range(i0, i1 + 1):
            x = self._bar_cx(i)
            y = self._bitrate_y(i, height, available_height)
            if prev is not None:
                painter.drawLine(QPointF(prev[0], prev[1]), QPointF(x, y))
            prev = (x, y)

    def _draw_ref_markers(self, painter: QPainter, first: int,
                          last: int, height: int, available_height: int) -> None:
        """Circled ref-index numbers in a fixed top row for the selected
        frame's references (L0 red, L1 green), each joined to its frame's size
        bar by a dashed vertical guide line so the target is unambiguous."""
        if (not self._ref_l0 and not self._ref_l1) or self._selected_index < 0:
            return
        d = 13  # circle diameter
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        for refs, color, row_y in ((self._ref_l0, QColor(220, 40, 40), 3),
                                   (self._ref_l1, QColor(40, 170, 60), 3 + d + 2)):
            for ref_idx, fidx in enumerate(refs):
                if not (first <= fidx <= last):
                    continue
                rx = int(round(self._bar_cx(fidx)))
                btop = self._bar_top(fidx, height, available_height)
                # Dashed guide from the circle down to the referenced bar.
                if btop > row_y + d:
                    painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                    painter.drawLine(rx, row_y + d, rx, btop)
                # Circled ref index.
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 235))
                painter.setPen(QPen(QColor(255, 255, 255), 1))
                painter.drawEllipse(rx - d // 2, row_y, d, d)
                painter.drawText(QRect(rx - d // 2, row_y, d, d),
                                 Qt.AlignmentFlag.AlignCenter, str(ref_idx))

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
            self.hover_changed.emit(index)
            # Repaint only the two affected bars, not the whole (100k-wide)
            # widget, so hover stays cheap while scrubbing the chart.
            for i in (old, index):
                if i >= 0:
                    self.update(self._bar_rect(i))
            # Reference arcs span across columns; a partial bar repaint would
            # erase segments crossing it. Repaint the (local) arc region too.
            rb = self._ref_bounds()
            if rb is not None:
                self.update(rb)

            # Show tooltip with frame info
            if index >= 0 and index < len(self._frames):
                frame = self._frames[index]
                tooltip = (f"Frame {index}\n"
                          f"Type: {frame.frame_type.value}\n"
                          f"Size: {frame.size:,} bytes\n"
                          f"Bitrate: {frame.instant_bitrate:,} bps"
                          f" ({frame.instant_bitrate / 1e6:.2f} Mbps)\n"
                          f"Keyframe: {'Yes' if frame.is_keyframe else 'No'}")
                self.setToolTip(tooltip)
            else:
                self.setToolTip("")

    def set_show_bitrate(self, on: bool) -> None:
        if on != self._show_bitrate:
            self._show_bitrate = on
            self.update()

    def set_hover(self, index: int) -> None:
        """Set the hovered frame from an external source (e.g. the hierarchy)
        without re-emitting hover_changed, so the two widgets stay in sync
        without a signal loop."""
        if index == self._hover_index:
            return
        old = self._hover_index
        self._hover_index = index
        for i in (old, index):
            if i >= 0:
                self.update(self._bar_rect(i))
        rb = self._ref_bounds()
        if rb is not None:
            self.update(rb)

    def leaveEvent(self, event):
        """Handle mouse leaving widget."""
        if self._hover_index != -1:
            self._hover_index = -1
            self.hover_changed.emit(-1)
        self.update()

    def _ref_bounds(self) -> QRect:
        """Bounding rect (full height) covering the selected frame and its
        reference markers/arcs, or None when there are no markers."""
        if (not self._ref_l0 and not self._ref_l1) or self._selected_index < 0:
            return None
        idxs = [self._selected_index, *self._ref_l0, *self._ref_l1]
        xs = [self._bar_x(i) for i in idxs if i >= 0]
        x0 = int(min(xs)) - 8
        x1 = int(max(xs) + self._bar_w()) + 8
        return QRect(x0, 0, x1 - x0, self.height())

    def _bar_rect(self, index: int) -> QRect:
        """Full-height repaint rect for one bar (covers its keyframe triangle and
        the position cursor lines, incl. the offset double hover line + halo)."""
        x = self._bar_x(index)
        return QRect(int(x) - 6, 0, int(self._bar_w()) + 12, self.height())

    def _get_frame_at_pos(self, x: int) -> int:
        """Get frame index at x position."""
        index = self._index_at(x)
        return index if 0 <= index < len(self._frames) else -1

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
    """Fixed legend panel: frame-type colors, the bitrate-line key, and a
    clickable toggle for the reference-hierarchy graph (off by default)."""

    BITRATE_COLOR = QColor(255, 200, 40)
    HIERARCHY_COLOR = QColor(40, 160, 60)  # matches HierarchyWidget._EDGE

    bitrate_toggled = pyqtSignal(bool)
    hierarchy_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(92)
        # Four frame-type rows (I/P/B/No-show) + the two toggles need ~112px;
        # keep a minimum so the bottom "Refs" toggle is never clipped.
        self.setMinimumHeight(116)
        self._bitrate_visible = False   # both overlays off by default
        self._hierarchy_visible = False
        self._bitrate_hit = QRect()     # clickable areas for the toggles
        self._hier_hit = QRect()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _draw_toggle(self, painter, t, y, color, label, on) -> QRect:
        """A checkbox + colored line key + label; returns its clickable rect."""
        box = QRect(6, y, 10, 10)
        painter.setPen(QPen(t.chart_text_dim, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box)
        if on:
            painter.setPen(QPen(color, 2))
            painter.drawLine(box.left() + 1, box.center().y() + 1,
                             box.right() - 1, box.center().y() + 1)
        painter.setPen(QPen(color, 2))
        painter.drawLine(20, y + 5, 30, y + 5)
        painter.setPen(t.chart_text if on else t.chart_text_dim)
        painter.drawText(34, y + 9, label)
        return QRect(2, y - 2, self.width() - 4, 18)

    def paintEvent(self, event):
        t = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), t.chart_bg)

        painter.setPen(t.chart_text)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        items = [
            (BarChartWidget.COLORS[FrameType.I], "I-frame"),
            (BarChartWidget.COLORS[FrameType.P], "P-frame"),
            (BarChartWidget.COLORS[FrameType.B], "B-frame"),
            (BarChartWidget.NOSHOW_COLOR, "No-show"),
        ]

        y = 8
        for color, label in items:
            painter.fillRect(6, y, 10, 10, color)
            painter.drawText(20, y + 9, label)
            y += 18

        y += 2
        self._bitrate_hit = self._draw_toggle(
            painter, t, y, self.BITRATE_COLOR, "Bitrate", self._bitrate_visible)
        y += 20
        self._hier_hit = self._draw_toggle(
            painter, t, y, self.HIERARCHY_COLOR, "Refs", self._hierarchy_visible)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        p = event.position().toPoint()
        if self._bitrate_hit.contains(p):
            self._bitrate_visible = not self._bitrate_visible
            self.update()
            self.bitrate_toggled.emit(self._bitrate_visible)
        elif self._hier_hit.contains(p):
            self._hierarchy_visible = not self._hierarchy_visible
            self.update()
            self.hierarchy_toggled.emit(self._hierarchy_visible)


class HierarchyWidget(QWidget):
    """Reference-frame hierarchy graph drawn under the size bars (Elecard-style).

    Each frame is a node placed at its bar's x and a y given by its temporal
    layer (anchors on the top row, hierarchical-B frames hang lower -- matching
    Elecard); green edges link a frame to its reference frames. The temporal
    layer is derived from the reference structure (no temporal_id needed):
    level = log2(base GOP span / nearest reference distance in display order),
    so an I/P anchor lands on the top row and each B-pyramid split drops a row.
    """

    HEIGHT = 104
    _EDGE = QColor(40, 160, 60)
    _SEL = QColor(220, 40, 40)

    frame_clicked = pyqtSignal(int)
    hover_changed = pyqtSignal(int)   # hovered frame index (-1 = none)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames: list[FrameInfo] = []
        self._refs: list[tuple] = []        # per index: (l0_list, l1_list)
        self._levels: list[int] = []
        self._max_level = 0
        # Keep these identical to BarChartWidget so nodes sit exactly under
        # their bars; BarChartView syncs them whenever the chart's change.
        self._bar_width = 8
        self._bar_spacing = 1
        self._selected = -1
        self._hover = -1
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)

    def set_frames(self, frames: list[FrameInfo]) -> None:
        self._frames = frames
        self._refs = []
        self._compute_levels()
        self._update_size()
        self.update()

    def set_refs(self, refs: list[tuple]) -> None:
        """refs[i] = (l0_indices, l1_indices) for frame i (chart index space)."""
        self._refs = refs
        self._compute_levels()
        self.update()

    def set_bar_width(self, width: int) -> None:
        self._bar_width = max(1, width)
        self._update_size()
        self.update()

    def set_selected(self, index: int) -> None:
        if index != self._selected:
            self._selected = index
            self.update()

    def set_hover(self, index: int) -> None:
        if index != self._hover:
            self._hover = index
            self.update()

    # Same device-pixel-snapped grid as BarChartWidget so nodes/cursors land
    # exactly under the bars (see BarChartWidget._metrics).
    def _dpr(self) -> float:
        r = self.devicePixelRatioF()
        return r if r > 0 else 1.0

    def _metrics(self):
        dpr = self._dpr()
        step_dev = max(2, round((self._bar_width + self._bar_spacing) * dpr))
        bw_dev = min(max(1, round(self._bar_width * dpr)), step_dev - 1)
        left_dev = round(5 * dpr)
        return left_dev / dpr, step_dev / dpr, bw_dev / dpr

    def _bar_cx(self, i: int) -> float:
        left, step, bw = self._metrics()
        return left + i * step + bw / 2.0

    def _update_size(self) -> None:
        left, step, _ = self._metrics()
        self.setMinimumWidth(max(100, int(left + len(self._frames) * step + 5)))

    def _disp_key(self, i: int) -> int:
        f = self._frames[i]
        if f.poc is not None:
            return f.poc
        if f.pts is not None:
            return f.pts
        return f.index

    def _compute_levels(self) -> None:
        n = len(self._frames)
        self._levels = [0] * n
        self._max_level = 0
        if not self._refs or len(self._refs) != n:
            return
        keys = [self._disp_key(i) for i in range(n)]
        nearest = [0] * n
        for i in range(n):
            l0, l1 = self._refs[i]
            rs = [r for r in (*l0, *l1) if 0 <= r < n]
            if rs:
                nearest[i] = min(abs(keys[i] - keys[r]) for r in rs) or 1
        gap = max(nearest) or 1
        for i in range(n):
            if nearest[i] <= 0:
                self._levels[i] = 0
            else:
                self._levels[i] = max(0, round(math.log2(gap / nearest[i])))
        self._max_level = max(self._levels) if self._levels else 0

    def _node_xy(self, i: int):
        cx = self._bar_cx(i)
        rows = max(1, self._max_level)
        row_h = (self.HEIGHT - 20) / rows
        # Level 0 (I/P anchors) on the top row; deeper B layers hang lower.
        cy = 10 + self._levels[i] * row_h
        return cx, cy

    def _index_at(self, x: float) -> int:
        left, step, _ = self._metrics()
        if x < left:
            return -1
        i = int((x - left) / step)
        return i if 0 <= i < len(self._frames) else -1

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            i = self._index_at(int(event.position().x()))
            if i >= 0:
                self.frame_clicked.emit(i)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Hovering the hierarchy drives the cursor too (not just the bars)."""
        i = self._index_at(int(event.position().x()))
        if i != self._hover:
            self._hover = i
            self.update()
            self.hover_changed.emit(i)

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.update()
            self.hover_changed.emit(-1)

    def paintEvent(self, event):
        t = current_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), t.chart_bg)
        n = len(self._frames)
        if n == 0 or len(self._levels) != n:
            return
        dirty = event.rect()
        first = max(0, self._index_at(dirty.left()) - 2)
        rd = self._index_at(dirty.right())
        last = min(n - 1, (n - 1 if rd < 0 else rd) + 2)

        # Edges first (under the nodes). Only for visible source frames.
        for i in range(first, last + 1):
            if not self._refs or len(self._refs) != n:
                break
            sx, sy = self._node_xy(i)
            sel = (i == self._selected)
            painter.setPen(QPen(self._SEL if sel else self._EDGE,
                                1.4 if sel else 0.8))
            l0, l1 = self._refs[i]
            for r in (*l0, *l1):
                if 0 <= r < n:
                    rx, ry = self._node_xy(r)
                    painter.drawLine(int(sx), int(sy), int(rx), int(ry))

        # Nodes on top.
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(first, last + 1):
            cx, cy = self._node_xy(i)
            sel = (i == self._selected)
            painter.setBrush(self._SEL if sel else self._EDGE)
            rad = 4.0 if sel else 2.6
            painter.drawEllipse(QPointF(cx, cy), rad, rad)

        # Position cursors: extend the chart's lines down through the hierarchy
        # (Elecard-style) so each spans both views as one. Current/locked frame
        # = single line; hovered frame = double line. Matches _draw_cursors.
        def vline(i: int, double: bool) -> None:
            cx = self._bar_cx(i)
            offsets = (-1.5, 1.5) if double else (0.0,)
            halo_w = 2 if double else 3
            core_w = 1 if double else 2
            for o in offsets:
                x = int(round(cx + o))
                painter.setPen(QPen(t.cursor_halo, halo_w))
                painter.drawLine(x, 0, x, self.HEIGHT)
                painter.setPen(QPen(t.cursor_core, core_w))
                painter.drawLine(x, 0, x, self.HEIGHT)

        if 0 <= self._selected < n:
            vline(self._selected, False)
        if 0 <= self._hover < n and self._hover != self._selected:
            vline(self._hover, True)


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
        # Show the horizontal scrollbar only when the chart is wider than the
        # viewport; hide it when every bar already fits.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Chart + reference-hierarchy stacked in one scrolled container so they
        # share horizontal scroll and stay x-aligned.
        self._chart = BarChartWidget()
        self._chart.frame_selected.connect(self.frame_selected)
        self._hierarchy = HierarchyWidget()
        self._hierarchy.frame_clicked.connect(self.frame_selected)
        self._hierarchy.setVisible(False)  # off by default; toggled via legend
        self._legend.hierarchy_toggled.connect(self._hierarchy.setVisible)
        self._legend.bitrate_toggled.connect(self._chart.set_show_bitrate)
        # Mirror hover both ways so the cursor tracks the mouse over either the
        # bars or the hierarchy. The set_hover setters don't re-emit, so there's
        # no signal loop.
        self._chart.hover_changed.connect(self._hierarchy.set_hover)
        self._hierarchy.hover_changed.connect(self._chart.set_hover)

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._chart, 1)
        cl.addWidget(self._hierarchy)
        self._scroll.setWidget(container)
        layout.addWidget(self._scroll)

    def set_frames(self, frames: list[FrameInfo]) -> None:
        """Set frame data for visualization."""
        self._chart.set_frames(frames)
        self._hierarchy.set_frames(frames)

    def set_ref_markers(self, l0: list[int], l1: list[int]) -> None:
        """Mark the selected frame's L0/L1 reference frames on the chart."""
        self._chart.set_ref_markers(l0, l1)

    def set_all_refs(self, refs: list[tuple]) -> None:
        """Per-frame (l0, l1) reference indices for the hierarchy graph."""
        self._hierarchy.set_refs(refs)

    def select_frame(self, index: int) -> None:
        """Select a frame and scroll to make it visible."""
        self._chart.select_frame(index)
        self._hierarchy.set_selected(index)

        # Scroll to make selected frame visible
        if index >= 0:
            x_pos = int(self._chart._bar_cx(index))
            self._scroll.horizontalScrollBar().setValue(
                max(0, x_pos - self._scroll.viewport().width() // 2)
            )

    def set_bar_width(self, width: int) -> None:
        """Set bar width."""
        self._chart.set_bar_width(width)
        self._hierarchy._bar_spacing = self._chart._bar_spacing
        self._hierarchy.set_bar_width(self._chart._bar_width)

    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zooming."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom with Ctrl+wheel
            delta = event.angleDelta().y()
            current_width = self._chart._bar_width
            self._chart.set_bar_width(current_width + (1 if delta > 0 else -1))
            self._hierarchy._bar_spacing = self._chart._bar_spacing
            self._hierarchy.set_bar_width(self._chart._bar_width)
            event.accept()
        else:
            # Normal horizontal scroll
            super().wheelEvent(event)

    def clear(self) -> None:
        """Clear the chart."""
        self._chart.set_frames([])
        self._hierarchy.set_frames([])

    @property
    def selected_index(self) -> int:
        """Get selected frame index."""
        return self._chart.selected_index
