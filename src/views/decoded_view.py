"""Decoded frame display view with block-analysis overlays."""

import copy

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QScrollArea, QSizePolicy,
    QFrame,
)
from PyQt6.QtCore import Qt, QSize, QPoint, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QWheelEvent,
)

from .overlay import (
    OVERLAYS, OVERLAY_GROUPS, DEFAULT_ON, ALL_OVERLAY_KEYS,
    render_partition, render_mode, render_block_types,
)
from ..theme import current_theme

# Always-on hover inspection overlay: reveal the region's CU/PU/TU partition,
# block type and MVs regardless of the overlay toggles. Intra-mode glyphs are
# intentionally excluded -- hover shows partition / type / MV only.
_HOVER_FLAGS = {
    "partition": True, "part_pu": True,
    "part_tu_luma": True, "part_tu_chroma": True,
    "mode": True, "mode_inter": True,
}


class _ImageLabel(QLabel):
    """Image label: reports mouse position for block inspection, and supports
    wheel-zoom and left-drag panning of the decoded view."""

    mouse_moved = pyqtSignal(QPoint)
    mouse_left = pyqtSignal()
    clicked = pyqtSignal(QPoint)             # left-click (no drag) at label pos
    panned = pyqtSignal(int, int)            # mouse movement dx, dy while dragging
    zoom_requested = pyqtSignal(int, QPoint)  # wheel delta, cursor pos in label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._panning = False
        self._pan_last = QPoint()
        self._press_pos = None    # label-space press position, to detect clicks
        self._hover_paint = None  # callable(painter): draws the hover overlay

    def paintEvent(self, event):
        super().paintEvent(event)  # draws the scaled frame pixmap
        if self._hover_paint is not None:
            painter = QPainter(self)
            try:
                self._hover_paint(painter)
            finally:
                painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._panning = True
            self._pan_last = event.globalPosition().toPoint()
            self._press_pos = event.position().toPoint()
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
            # A press+release with negligible movement is a click (not a pan):
            # use it to toggle the block-info lock.
            if self._press_pos is not None:
                d = event.position().toPoint() - self._press_pos
                if abs(d.x()) + abs(d.y()) <= 4:
                    self.clicked.emit(self._press_pos)
            self._press_pos = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        # Plain wheel zooms (toward the cursor); consume so the scroll area
        # does not also scroll.
        self.zoom_requested.emit(event.angleDelta().y(), event.position().toPoint())
        event.accept()

    def leaveEvent(self, event):
        self.mouse_left.emit()
        super().leaveEvent(event)


# Index ruler geometry (Elecard-style block index strips). Colours come from
# the active theme (see _paint_ruler / apply_theme).
_RULER_T = 18           # top ruler height
_RULER_W = 34           # left ruler width


class _Ruler(QWidget):
    """A top or left index strip showing block (MB/CTB) column/row numbers,
    aligned with the scrolled/zoomed image. Painting is delegated to the
    DecodedView which knows the image-to-viewport mapping."""

    def __init__(self, view, horizontal: bool):
        super().__init__(view)
        self._view = view
        self._horizontal = horizontal
        if horizontal:
            self.setFixedHeight(_RULER_T)
            self.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(_RULER_W)
            self.setSizePolicy(QSizePolicy.Policy.Fixed,
                               QSizePolicy.Policy.Ignored)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._view._paint_ruler(self, painter, self._horizontal)
        finally:
            painter.end()


class DecodedView(QWidget):
    """Displays decoded video frames with optional analysis overlays."""

    # Emits a dict describing the hovered block, or None when leaving.
    block_hovered = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None          # composed (frame + overlays), native res
        self._rgb = None             # raw frame for recomposition
        self._analysis = None
        self._overlay_flags = {key: (key in DEFAULT_ON) for key in ALL_OVERLAY_KEYS}
        self._zoom_factor = 1.0
        self._fit_to_window = True
        self._frame_index = -1
        self._hover_region = None  # QRect (native px) of the LCU/MB under cursor
        self._locked = False       # block-info display frozen to a clicked region
        self._locked_pos = None    # (px, py) of the locked region in native px
        self._setup_ui()
        self._image_label._hover_paint = self._draw_hover

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label at top
        self._info_label = QLabel("No frame loaded")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

        # Scroll area for the image. Not widget-resizable: we size the label to
        # the scaled pixmap ourselves so that zooming past the viewport produces
        # scrollbars (and thus something to pan). The label is centered when it
        # is smaller than the viewport (fit / zoomed-out).
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        # Image display label
        self._image_label = _ImageLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._image_label.mouse_moved.connect(self._on_mouse_moved)
        self._image_label.mouse_left.connect(self._on_mouse_left)
        self._image_label.clicked.connect(self._on_clicked)
        self._image_label.panned.connect(self._on_panned)
        self._image_label.zoom_requested.connect(self._on_zoom_requested)
        self._scroll.setWidget(self._image_label)

        # Block-index rulers (Elecard-style): a grid with an origin corner, a
        # top column-index strip, a left row-index strip, and the scroll area.
        self._ruler_visible = True
        self._top_ruler = _Ruler(self, horizontal=True)
        self._left_ruler = _Ruler(self, horizontal=False)
        self._corner = QWidget()
        self._corner.setFixedSize(_RULER_W, _RULER_T)
        self._corner.setAutoFillBackground(True)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self._corner, 0, 0)
        grid.addWidget(self._top_ruler, 0, 1)
        grid.addWidget(self._left_ruler, 1, 0)
        grid.addWidget(self._scroll, 1, 1)
        layout.addLayout(grid)

        # Repaint the rulers whenever the image is scrolled.
        self._scroll.horizontalScrollBar().valueChanged.connect(
            self._update_rulers)
        self._scroll.verticalScrollBar().valueChanged.connect(
            self._update_rulers)
        self.apply_theme()

    def apply_theme(self) -> None:
        """Re-apply theme-dependent chrome (info strip, image background, ruler
        origin corner) and repaint the custom-painted rulers."""
        t = current_theme()
        self._info_label.setStyleSheet(
            f"background-color: {t.hx(t.panel_bg)}; color: {t.hx(t.panel_fg)};"
            f" padding: 4px;")
        self._image_label.setStyleSheet(
            f"background-color: {t.hx(t.canvas_bg)};")
        self._corner.setStyleSheet(
            f"background-color: {t.hx(t.ruler_corner)};")
        self._update_rulers()

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

        # Keep the locked region pinned across frame changes: re-evaluate the
        # same pixel against the new frame's analysis so the frozen panel stays
        # meaningful (e.g. stepping frames to compare one region).
        if self._locked and self._locked_pos is not None and analysis is not None:
            px, py = self._locked_pos
            if px < self._pixmap.width() and py < self._pixmap.height():
                self._show_block_at(px, py, locked=True)
            else:
                self._locked = False
                self._locked_pos = None

    def has_overlays(self) -> bool:
        """True if any analysis overlay is currently enabled."""
        return any(self._overlay_flags.values())

    def needed_layers(self) -> set:
        """Sidecar analysis layers required by the currently enabled overlays."""
        from .overlay import needed_layers
        return needed_layers(self._overlay_flags)

    def set_overlays(self, flags: dict) -> None:
        """Enable/disable overlay layers, e.g. {'qp': True, 'mode': False}."""
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

        if self._analysis is None or not any(self._overlay_flags.values()):
            self._pixmap = QPixmap.fromImage(image)
            return

        # Composite overlays onto an ARGB32_Premultiplied canvas: Qt's raster
        # engine paints rectangles/lines onto it ~3-4x faster than onto a 24-bit
        # RGB888 surface, which dominated overlay cost at 1440p (e.g. partition
        # ~73 ms -> ~20 ms). convertToFormat also detaches from the numpy buffer.
        canvas = image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(canvas)
        try:
            for key, (_label, render) in OVERLAYS.items():
                if self._overlay_flags.get(key):
                    render(painter, self._analysis)
            for _master, (_label, _subs, render) in OVERLAY_GROUPS.items():
                render(painter, self._analysis, self._overlay_flags)
        except Exception as e:
            print(f"Overlay rendering failed: {e}")
        finally:
            painter.end()
        self._pixmap = QPixmap.fromImage(canvas)

    def _label_to_px(self, pos: QPoint):
        """Map a label-space position to native frame pixel (px, py), or None if
        unavailable / outside the frame."""
        if self._pixmap is None or self._analysis is None:
            return None
        shown = self._image_label.pixmap()
        if shown is None or shown.width() == 0 or shown.height() == 0:
            return None
        # Label is resized to exactly fit the scaled pixmap.
        px = int(pos.x() * self._pixmap.width() / shown.width())
        py = int(pos.y() * self._pixmap.height() / shown.height())
        if not (0 <= px < self._pixmap.width() and 0 <= py < self._pixmap.height()):
            return None
        return px, py

    def _info_at(self, px: int, py: int) -> dict:
        """Build the block-info dict for native pixel (px, py)."""
        a = self._analysis
        return {
            "codec": a.codec,
            "px": px,
            "py": py,
            "unit": a.qp_unit,
            "block_x": px // a.qp_unit,
            "block_y": py // a.qp_unit,
            "qp": a.qp_at(px, py),
            "mvs": a.mvs_at(px, py),
            "block": a.block_at(px, py),
            "bits": a.bits_at(px, py),
            "ctu_bits": a.ctu_bits_at(px, py),
            "ctu_origin": a.ctu_origin(px, py),
            "ctb_size": a.ctb_size,
            "slice_idx": a.slice_idx_at(px, py),
            "tile_idx": a.tile_idx_at(px, py),
            "h264_aux": a.h264_aux_at(px, py),
            "h264_intra": a.h264_intra_at(px, py),   # (mode, block_size) | None
        }

    def _show_block_at(self, px: int, py: int, locked: bool = False) -> None:
        """Emit block info for (px, py) and highlight its LCU/MB region."""
        info = self._info_at(px, py)
        info["locked"] = locked
        self.block_hovered.emit(info)
        region = self._lcu_region(px, py)
        if region != self._hover_region:
            self._hover_region = region
        self._image_label.update()

    def _on_mouse_moved(self, pos: QPoint) -> None:
        """Map a label-space mouse position to a block info dict (live hover).
        While locked, the display is frozen and moves are ignored."""
        if self._locked:
            return
        if self._pixmap is None or self._analysis is None:
            return
        coord = self._label_to_px(pos)
        if coord is None:
            self.block_hovered.emit(None)
            return
        self._show_block_at(*coord)

    def _on_clicked(self, pos: QPoint) -> None:
        """Toggle the block-info lock: a click freezes the display to the
        clicked region; clicking again (anywhere) releases it."""
        if self._pixmap is None or self._analysis is None:
            return
        if self._locked:
            self._locked = False
            self._locked_pos = None
            self._on_mouse_moved(pos)   # resume live hover at the click point
            return
        coord = self._label_to_px(pos)
        if coord is None:
            return
        self._locked = True
        self._locked_pos = coord
        self._show_block_at(*coord, locked=True)

    def _lcu_region(self, px: int, py: int) -> QRect:
        """The LCU/MB cell (native px) containing (px, py): 16 for H.264, 64
        for HEVC/AV1."""
        codec = (self._analysis.codec or "").lower()
        size = 16 if codec in ("h264", "avc") else 64
        rx = (px // size) * size
        ry = (py // size) * size
        w = min(size, self._pixmap.width() - rx)
        h = min(size, self._pixmap.height() - ry)
        return QRect(rx, ry, w, h)

    def _on_mouse_left(self) -> None:
        if self._locked:
            return                       # keep the locked region/info on screen
        self.block_hovered.emit(None)
        if self._hover_region is not None:
            self._hover_region = None
            self._image_label.update()

    def _draw_hover(self, painter: QPainter) -> None:
        """Paint partition (CU/PU/TU) + MV + type for the hovered LCU/MB,
        clipped to that region, on top of the displayed frame. Runs regardless
        of the overlay toggles so hovering always reveals the region's coding
        structure."""
        if (self._analysis is None or self._hover_region is None
                or self._pixmap is None):
            return
        shown = self._image_label.pixmap()
        if shown is None or shown.width() == 0 or self._pixmap.width() == 0:
            return
        sx = shown.width() / self._pixmap.width()
        sy = shown.height() / self._pixmap.height()
        r = self._hover_region

        # Restrict analysis arrays to the region so the renderers iterate only
        # a handful of blocks per hover (cheap), then clip for clean edges.
        region_an = self._region_analysis(r)

        painter.save()
        painter.scale(sx, sy)
        painter.setClipRect(r)
        render_block_types(painter, region_an)
        render_partition(painter, region_an, _HOVER_FLAGS)
        render_mode(painter, region_an, _HOVER_FLAGS)
        painter.setClipping(False)
        # Outline the inspected region: green when locked, yellow while hovering.
        if self._locked:
            painter.setPen(QPen(QColor(0, 230, 80, 230), 2.0))
        else:
            painter.setPen(QPen(QColor(255, 255, 0, 200), 1.0))
        painter.drawRect(r)
        painter.restore()

    def _region_analysis(self, r: QRect):
        """A shallow copy of the analysis with block/MV arrays filtered to the
        rectangle r (native px)."""
        a = self._analysis

        def clip(arr):
            if arr is None or len(arr) == 0:
                return arr
            m = ((arr["x"] < r.x() + r.width()) & (arr["x"] + arr["w"] > r.x())
                 & (arr["y"] < r.y() + r.height()) & (arr["y"] + arr["h"] > r.y()))
            return arr[m]

        ra = copy.copy(a)
        ra.blocks = clip(a.blocks)
        ra.pu = clip(a.pu)
        ra.tu_luma = clip(a.tu_luma)
        ra.tu_chroma = clip(a.tu_chroma)
        ra.mvs = clip(a.mvs)
        ra.intra = clip(a.intra)
        return ra

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
        self._update_rulers()

    def _ruler_unit(self) -> int:
        """Native px per index cell: the MB (16) for H.264, LCU/SB (64) for
        HEVC/AV1 -- so the index numbers a block's row/column."""
        codec = (self._analysis.codec or "").lower() if self._analysis else ""
        return 16 if codec in ("h264", "avc") else 64

    def _update_rulers(self) -> None:
        if getattr(self, "_top_ruler", None) is not None:
            self._top_ruler.update()
            self._left_ruler.update()

    def set_ruler_visible(self, visible: bool) -> None:
        """Show/hide the block-index rulers."""
        self._ruler_visible = visible
        for w in (self._top_ruler, self._left_ruler, self._corner):
            w.setVisible(visible)

    def _paint_ruler(self, ruler, painter: QPainter, horizontal: bool) -> None:
        """Draw block-index ticks/numbers on a ruler, aligned to the displayed
        image's position (accounts for zoom, scroll and centering)."""
        t = current_theme()
        painter.fillRect(ruler.rect(), t.ruler_bg)
        shown = self._image_label.pixmap()
        if self._pixmap is None or shown is None or shown.width() == 0:
            return
        scale = shown.width() / self._pixmap.width()
        unit = self._ruler_unit()
        # Image label origin in the scroll viewport's coordinates (negative when
        # scrolled, positive when the image is centered/smaller than viewport).
        off = self._image_label.mapTo(self._scroll.viewport(), QPoint(0, 0))
        off_main = off.x() if horizontal else off.y()
        span = self._pixmap.width() if horizontal else self._pixmap.height()
        length = ruler.width() if horizontal else ruler.height()
        step_px = unit * scale
        if step_px <= 0:
            return
        # Label every Nth block so numbers never crowd (>= ~30 px apart).
        label_step = max(1, int(np.ceil(30.0 / step_px)))
        n = span // unit
        f = painter.font()
        f.setPointSize(8)
        painter.setFont(f)
        fm = painter.fontMetrics()
        for k in range(n + 1):
            pos = off_main + k * unit * scale
            if pos < -1 or pos > length + 1:
                continue
            painter.setPen(QPen(t.ruler_line, 1))
            if horizontal:
                painter.drawLine(int(pos), _RULER_T - 5, int(pos), _RULER_T - 1)
            else:
                painter.drawLine(_RULER_W - 5, int(pos), _RULER_W - 1, int(pos))
            if k % label_step == 0 and k < n:
                painter.setPen(QPen(t.ruler_text, 1))
                s = str(k)
                if horizontal:
                    painter.drawText(int(pos) + 2, _RULER_T - 6, s)
                else:
                    w = fm.horizontalAdvance(s)
                    painter.drawText(_RULER_W - 7 - w, int(pos) + fm.ascent() + 1,
                                     s)

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
        self._update_rulers()

    def clear(self) -> None:
        """Clear the displayed frame."""
        self._pixmap = None
        self._rgb = None
        self._analysis = None
        self._frame_index = -1
        self._hover_region = None
        self._locked = False
        self._locked_pos = None
        self._image_label.clear()
        self._info_label.setText("No frame loaded")
