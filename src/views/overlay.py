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

_PRED_COLORS = {
    PredType.INTRA: QColor(255, 64, 64, 90),
    PredType.INTER: QColor(64, 110, 255, 90),
    PredType.SKIP: QColor(110, 255, 110, 70),
    PredType.IPCM: QColor(255, 255, 64, 110),
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


def render_partition(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Partition boundaries.

    With full block data (patched backend): true coding-block tree.
    Fallback: coding-unit grid plus inter sub-partition rectangles
    derived from motion vector blocks.
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    if analysis.blocks is not None and len(analysis.blocks) > 0:
        painter.setPen(QPen(QColor(0, 0, 0), 1.0))  # Elecard-style black lines
        b = analysis.blocks
        for x, y, w, h in zip(b["x"], b["y"], b["w"], b["h"]):
            painter.drawRect(int(x), int(y), int(w), int(h))
        return

    # Fallback path (stock FFmpeg backend)
    unit = analysis.qp_unit
    if analysis.qp_grid is not None:
        rows, cols = analysis.qp_grid.shape
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        right, bottom = cols * unit, rows * unit
        lines = [QLineF(c * unit, 0, c * unit, bottom) for c in range(cols + 1)]
        lines += [QLineF(0, r * unit, right, r * unit) for r in range(rows + 1)]
        painter.drawLines(lines)

    if analysis.mvs is not None and len(analysis.mvs) > 0:
        painter.setPen(QPen(QColor(0, 0, 0), 1.0))
        m = analysis.mvs
        for x, y, w, h in zip(m["x"], m["y"], m["w"], m["h"]):
            painter.drawRect(int(x), int(y), int(w), int(h))


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


# Overlay registry: key -> (label, render function)
OVERLAYS = {
    "qp": ("QP Map", render_qp_map),
    "mv": ("Motion Vectors", render_motion_vectors),
    "partition": ("Partition", render_partition),
    "types": ("Block Types", render_block_types),
}
