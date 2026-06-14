"""Overlay rendering for block-level analysis (QP / MV / partition / types).

Each overlay is an independent render function painting onto the
native-resolution frame pixmap. Adding a new overlay (e.g. a future
codec tool like ALF) means adding one function here plus a toggle.
"""

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen

from ..analysis import FrameAnalysis, PredType

QP_MAX = 63  # covers H.264/HEVC (51) and leaves headroom for AV1 mapping

# Block-type fill colors. SKIP is intentionally omitted (not coloured).
_PRED_COLORS = {
    PredType.INTRA: QColor(255, 64, 64, 90),    # red
    PredType.INTER: QColor(64, 110, 255, 90),   # blue (uni-directional)
    PredType.BI: QColor(80, 200, 80, 90),       # green (bi-directional)
    PredType.IPCM: QColor(255, 255, 64, 110),   # yellow
}


def render_qp_map(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Elecard-style opaque grayscale QP map: each block a solid gray shade
    by its QP (low QP bright/white, high QP dark). Covers the picture like
    Elecard's QP map view rather than tinting it. Unknown blocks stay
    transparent."""
    grid = analysis.qp_grid
    if grid is None:
        return
    rows, cols = grid.shape
    # Normalize the codec's QP range to 0..255 grey, inverted so low QP (high
    # quality) is white and high QP is black, matching Elecard. AV1 carries
    # qindex (0..255), H.264/HEVC a 0..51 QP.
    qp_max = analysis.qp_max or QP_MAX
    norm = np.clip(grid.astype(np.float32) * (255.0 / qp_max), 0, 255)
    gray = (255.0 - norm).astype(np.uint8)
    rgba = np.empty((rows, cols, 4), dtype=np.uint8)
    rgba[..., 0] = gray
    rgba[..., 1] = gray
    rgba[..., 2] = gray
    rgba[..., 3] = 255  # opaque, like Elecard's QP map view
    rgba[grid < 0] = 0  # unknown blocks fully transparent

    rgba = np.ascontiguousarray(rgba)
    img = QImage(rgba.data, cols, rows, cols * 4,
                 QImage.Format.Format_RGBA8888)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    unit = analysis.qp_unit
    painter.drawImage(QRect(0, 0, cols * unit, rows * unit), img)


def render_motion_vectors(painter: QPainter, analysis: FrameAnalysis) -> None:
    """MV arrows from block center toward the referenced position.

    L0 (past reference) red, L1 (future reference) cyan-blue.
    """
    mvs = analysis.mvs
    if mvs is None or len(mvs) == 0:
        return

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for list_idx, color in ((0, QColor(255, 70, 70)), (1, QColor(80, 190, 255))):
        sel = mvs[mvs["list"] == list_idx]
        if len(sel) == 0:
            continue
        nonzero = (sel["mv_x"] != 0) | (sel["mv_y"] != 0)
        sel = sel[nonzero]
        if len(sel) == 0:
            continue
        cx = sel["x"] + sel["w"] / 2.0
        cy = sel["y"] + sel["h"] / 2.0
        ex = cx + sel["mv_x"]
        ey = cy + sel["mv_y"]

        lines = [QLineF(float(a), float(b), float(c), float(d))
                 for a, b, c, d in zip(cx, cy, ex, ey)]
        painter.setPen(QPen(color, 1.0))
        painter.drawLines(lines)

        # Mark the block-center origin of each vector.
        painter.setPen(QPen(color, 3.0))
        painter.drawPoints([QPointF(float(a), float(b)) for a, b in zip(cx, cy)])


def _draw_rects(painter: QPainter, rects, color: QColor) -> None:
    """Outline each (x, y, w, h) rectangle in a structured array."""
    if rects is None or len(rects) == 0:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(color, 1.0))
    for x, y, w, h in zip(rects["x"], rects["y"], rects["w"], rects["h"]):
        painter.drawRect(int(x), int(y), int(w), int(h))


def _draw_cu(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Coding-unit boundaries (black). Falls back to the CU grid + MV blocks
    when only the stock FFmpeg backend is available."""
    if analysis.blocks is not None and len(analysis.blocks) > 0:
        _draw_rects(painter, analysis.blocks, QColor(0, 0, 0))
        return

    # Fallback path (stock FFmpeg backend): CU grid + inter sub-partitions.
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    unit = analysis.qp_unit
    if analysis.qp_grid is not None:
        rows, cols = analysis.qp_grid.shape
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        right, bottom = cols * unit, rows * unit
        lines = [QLineF(c * unit, 0, c * unit, bottom) for c in range(cols + 1)]
        lines += [QLineF(0, r * unit, right, r * unit) for r in range(rows + 1)]
        painter.drawLines(lines)
    _draw_rects(painter, analysis.mvs, QColor(0, 0, 0))


def render_partition(painter: QPainter, analysis: FrameAnalysis, flags: dict) -> None:
    """Partition overlay. Enabling partition always draws CU (black) as the
    base; PU (blue) and TU (red) are optional refinements layered on top.
    PU/TU are drawn first and CU last, so CU edges stay black while only the
    finer PU/TU splits show in their own colour.
    """
    if not flags.get(PARTITION_KEY):
        return
    if flags.get("part_pu"):
        _draw_rects(painter, analysis.pu, QColor(40, 120, 255))
    if flags.get("part_tu_luma"):
        _draw_rects(painter, analysis.tu_luma, QColor(230, 40, 40))
    if flags.get("part_tu_chroma"):
        _draw_rects(painter, analysis.tu_chroma, QColor(230, 40, 40))
    _draw_cu(painter, analysis)  # CU base, on top so its edges read as black


def render_block_types(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Prediction-type coloring (intra/inter/skip). Needs block data."""
    blocks = analysis.blocks
    if blocks is None or len(blocks) == 0:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    for pred, color in _PRED_COLORS.items():
        sel = blocks[blocks["pred"] == pred]
        for x, y, w, h in zip(sel["x"], sel["y"], sel["w"], sel["h"]):
            painter.fillRect(int(x), int(y), int(w), int(h), color)


# Flat overlay registry: key -> (label, render function). Each is rendered
# independently. The partition layers are handled separately (render_partition)
# because they compose (CU base + PU/TU refinements) rather than stack.
OVERLAYS = {
    "qp": ("QP Map", render_qp_map),
    "mv": ("Motion Vectors", render_motion_vectors),
    "types": ("Block Types", render_block_types),
}

# Master flag: enabling partition always draws CU. PARTITION_LAYERS are the
# optional refinements revealed when the partition menu is expanded (CU is not
# listed -- it is implied by the master being on). key -> checkbox label.
PARTITION_KEY = "partition"
PARTITION_LAYERS = (
    ("part_pu", "PU"),
    ("part_tu_luma", "TU (luma)"),
    ("part_tu_chroma", "TU (chroma)"),
)

# Every overlay flag key (flat + partition master + layers). Partition is OFF
# by default; when the user enables it, PU is pre-selected (CU is implied), so
# part_pu starts checked even though the master starts unchecked.
ALL_OVERLAY_KEYS = (
    tuple(OVERLAYS) + (PARTITION_KEY,) + tuple(k for k, _ in PARTITION_LAYERS)
)
DEFAULT_ON = ("part_pu",)
