"""Overlay rendering for block-level analysis (QP / MV / partition / types).

Each overlay is an independent render function painting onto the
native-resolution frame pixmap. Adding a new overlay (e.g. a future
codec tool like ALF) means adding one function here plus a toggle.
"""

import math

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

from ..analysis import FrameAnalysis, PredType
from ..analysis.schema import INTRA_DC, INTRA_PLANE, INTRA_ANGULAR

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
    """Outline each (x, y, w, h) rectangle in a structured array. Batched into a
    single drawRects() call -- per-rect drawRect() looped ~80 ms/frame for the
    ~18k partition rectangles of a 1440p frame."""
    if rects is None or len(rects) == 0:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(color, 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)  # outline only, never fill
    painter.drawRects([QRect(int(x), int(y), int(w), int(h))
                       for x, y, w, h in zip(rects["x"], rects["y"],
                                             rects["w"], rects["h"])])


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


def _fill_rects(painter: QPainter, sel, color: QColor) -> None:
    """Fill a BLOCK_DTYPE selection in one batched call. drawRects() with a
    brush and no pen fills each rect -- far cheaper than a fillRect() per rect
    at high resolution."""
    if len(sel) == 0:
        return
    painter.setBrush(color)
    painter.drawRects([QRect(int(x), int(y), int(w), int(h))
                       for x, y, w, h in zip(sel["x"], sel["y"],
                                             sel["w"], sel["h"])])


def _bitsize_heatmap(painter: QPainter, sel, values) -> None:
    """Opaque grayscale heatmap of a bit metric as bit *density* (bits per
    pixel), normalized to the frame's peak (bright = denser coding,
    Elecard-style). Dividing by block area makes the colour reflect how
    expensive a region is per unit area instead of just tracking block size."""
    if len(sel) == 0:
        return
    area = np.maximum(sel["w"].astype(np.float32) * sel["h"].astype(np.float32),
                      1.0)
    dens = values.astype(np.float32) / area        # bits per pixel
    maxv = float(dens.max())
    if maxv <= 0:
        return
    gray = np.clip(dens * (255.0 / maxv), 0, 255)
    gq = (gray.astype(np.int32) // 8) * 8       # quantize to limit fill groups
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    for lvl in np.unique(gq):
        _fill_rects(painter, sel[gq == lvl], QColor(int(lvl), int(lvl), int(lvl)))
    painter.setBrush(Qt.BrushStyle.NoBrush)


def render_bitsize(painter: QPainter, analysis: FrameAnalysis, flags: dict) -> None:
    """Bit Size group (HEVC): coded bit cost as a grayscale heatmap -- per CU
    (total / prediction / residual) or per CTU (whole-CTB total). The metrics
    are separate heatmaps; enabling more than one overdraws (pick one)."""
    if not flags.get("bits"):
        return
    if flags.get("bits_ctu"):
        cs = analysis.ctu_bit_sizes
        if cs is not None and len(cs):
            _bitsize_heatmap(painter, cs, cs["cu"])
    bs = analysis.bit_sizes
    if bs is None or len(bs) == 0:
        return
    if flags.get("bits_cu"):
        _bitsize_heatmap(painter, bs, bs["cu"])
    if flags.get("bits_pu"):
        _bitsize_heatmap(painter, bs, bs["pu"])
    if flags.get("bits_tu"):
        _bitsize_heatmap(painter, bs, bs["tu"])


def render_block_types(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Prediction-type coloring (intra/inter/skip). Needs block data."""
    blocks = analysis.blocks
    if blocks is None or len(blocks) == 0:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    for pred, color in _PRED_COLORS.items():
        _fill_rects(painter, blocks[blocks["pred"] == pred], color)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_lines(painter: QPainter, lines, color: QColor, width: int) -> None:
    """Draw [x1,y1,x2,y2] segments (an (N,4) array) as thick lines."""
    if lines is None or len(lines) == 0:
        return
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(color, width))
    painter.drawLines([QLineF(float(a), float(b), float(c), float(d))
                       for a, b, c, d in lines])


def render_slice_boundaries(painter: QPainter, analysis: FrameAnalysis) -> None:
    """HEVC slice boundaries (thick orange lines between CTBs of different
    slices). No-op for codecs/streams without slice-structure data."""
    _draw_lines(painter, analysis.slice_lines, QColor(255, 150, 30), 3)


def render_tile_boundaries(painter: QPainter, analysis: FrameAnalysis) -> None:
    """HEVC tile boundaries (thick cyan lines at tile column/row splits)."""
    _draw_lines(painter, analysis.tile_lines, QColor(40, 220, 230), 3)


# Intra-mode overlay: one Elecard-style violet, hollow line-art glyphs --
# angular = a line at the prediction angle, DC = a hollow circle, planar = a
# hollow square. Shape (not colour) distinguishes the categories.
_INTRA_COLOR = QColor(150, 115, 215)


def _hevc_intra_dirs() -> np.ndarray:
    """Unit direction (dx, dy) per HEVC intra mode 0..34 (0 for non-angular).
    Modes 2..17 are horizontal, 18..34 vertical; the angle parameter sets the
    tilt. y is screen-down."""
    angle = {2: 32, 3: 26, 4: 21, 5: 17, 6: 13, 7: 9, 8: 5, 9: 2, 10: 0,
             11: -2, 12: -5, 13: -9, 14: -13, 15: -17, 16: -21, 17: -26,
             18: -32, 19: -26, 20: -21, 21: -17, 22: -13, 23: -9, 24: -5,
             25: -2, 26: 0, 27: 2, 28: 5, 29: 9, 30: 13, 31: 17, 32: 21,
             33: 26, 34: 32}
    table = np.zeros((35, 2), dtype=np.float32)
    for m, a in angle.items():
        dx, dy = (-1.0, a / 32.0) if m < 18 else (a / 32.0, -1.0)
        n = math.hypot(dx, dy)
        table[m] = (dx / n, dy / n)
    return table


def _av1_intra_dirs() -> np.ndarray:
    """Unit direction (dx, dy) per AV1 PREDICTION_MODE 0..12 (0 for non-angular).
    Directional modes 1..8 map to base angles (deg); 90=up, 180=left."""
    deg = {1: 90, 2: 180, 3: 45, 4: 135, 5: 113, 6: 157, 7: 203, 8: 67}
    table = np.zeros((13, 2), dtype=np.float32)
    for m, d in deg.items():
        r = math.radians(d)
        table[m] = (math.cos(r), -math.sin(r))
    return table


_HEVC_INTRA_DIRS = _hevc_intra_dirs()
_AV1_INTRA_DIRS = _av1_intra_dirs()


def _intra_dir_table(codec: str) -> np.ndarray:
    if codec == "hevc":
        return _HEVC_INTRA_DIRS
    if codec == "av1":
        return _AV1_INTRA_DIRS
    return np.zeros((1, 2), dtype=np.float32)


# Elecard-style intra glyphs: thin violet line art, hollow, one per block.
def _intra_glyph_half(sel) -> np.ndarray:
    """Half-extent (px) of the centre glyph, scaled to block size, clamped."""
    return np.clip((np.minimum(sel["w"], sel["h"]) * 0.3).astype(np.int32), 3, 12)


def render_intra_angular(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Angular intra blocks: a line through the block centre at the prediction
    angle (orientation only, Elecard-style -- no arrowhead)."""
    intra = analysis.intra
    if intra is None or len(intra) == 0:
        return
    sel = intra[intra["cat"] == INTRA_ANGULAR]
    if len(sel) == 0:
        return
    dirs = _intra_dir_table(analysis.codec)
    d = dirs[np.clip(sel["mode"], 0, len(dirs) - 1)]
    cx = sel["x"] + sel["w"] / 2.0
    cy = sel["y"] + sel["h"] / 2.0
    ln = _intra_glyph_half(sel).astype(np.float32)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(_INTRA_COLOR, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLines([QLineF(float(a - dx * L), float(b - dy * L),
                              float(a + dx * L), float(b + dy * L))
                       for a, b, dx, dy, L in
                       zip(cx, cy, d[:, 0], d[:, 1], ln)])


def render_intra_dc(painter: QPainter, analysis: FrameAnalysis) -> None:
    """DC intra blocks: a hollow circle at the block centre."""
    intra = analysis.intra
    if intra is None or len(intra) == 0:
        return
    sel = intra[intra["cat"] == INTRA_DC]
    if len(sel) == 0:
        return
    r = _intra_glyph_half(sel)
    cx = sel["x"] + sel["w"] / 2.0
    cy = sel["y"] + sel["h"] / 2.0
    path = QPainterPath()
    for x, y, rr in zip(cx, cy, r):
        path.addEllipse(QPointF(float(x), float(y)), float(rr), float(rr))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(_INTRA_COLOR, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def render_intra_plane(painter: QPainter, analysis: FrameAnalysis) -> None:
    """Planar (and AV1 smooth/paeth) intra blocks: a hollow square."""
    intra = analysis.intra
    if intra is None or len(intra) == 0:
        return
    sel = intra[intra["cat"] == INTRA_PLANE]
    if len(sel) == 0:
        return
    half = _intra_glyph_half(sel)
    cx = sel["x"] + sel["w"] // 2
    cy = sel["y"] + sel["h"] // 2
    # Antialiased to match the DC circle / angular line stroke weight (a
    # non-AA outline renders visibly bolder than the AA circle at the same pen).
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(_INTRA_COLOR, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRects([QRect(int(x - h), int(y - h), int(2 * h), int(2 * h))
                       for x, y, h in zip(cx, cy, half)])


def render_mode(painter: QPainter, analysis: FrameAnalysis, flags: dict) -> None:
    """Prediction-mode group: inter motion vectors + intra direction markers.
    Drawn only when the 'mode' master is on; each sub-layer is independent."""
    if not flags.get("mode"):
        return
    if flags.get("mode_inter"):
        render_motion_vectors(painter, analysis)
    if flags.get("mode_intra_angular"):
        render_intra_angular(painter, analysis)
    if flags.get("mode_intra_plane"):
        render_intra_plane(painter, analysis)
    if flags.get("mode_intra_dc"):
        render_intra_dc(painter, analysis)


def render_boundary(painter: QPainter, analysis: FrameAnalysis, flags: dict) -> None:
    """Boundary group: HEVC slice and tile partition boundaries."""
    if not flags.get("boundary"):
        return
    if flags.get("bnd_slice"):
        render_slice_boundaries(painter, analysis)
    if flags.get("bnd_tile"):
        render_tile_boundaries(painter, analysis)


# Flat overlay registry: key -> (label, render function). Each is independent.
OVERLAYS = {
    "qp": ("QP Map", render_qp_map),
    "types": ("Block Types", render_block_types),
}

# Collapsible overlay groups (rendered like a menu, mirroring Partition):
#   master_key -> (label, ((sub_key, sub_label), ...), render_fn)
# render_fn(painter, analysis, flags) draws the group's enabled sub-layers and
# itself checks the master flag. Partition is special: its master implies the CU
# base; Mode/Boundary sub-layers are fully independent.
PARTITION_KEY = "partition"
PARTITION_LAYERS = (
    ("part_pu", "PU"),
    ("part_tu_luma", "TU (luma)"),
    ("part_tu_chroma", "TU (chroma)"),
)
OVERLAY_GROUPS = {
    PARTITION_KEY: ("Partition", PARTITION_LAYERS, render_partition),
    "mode": ("Intra/Inter Mode", (
        ("mode_inter", "Inter (MV)"),
        ("mode_intra_angular", "Intra Angular"),
        ("mode_intra_plane", "Intra Plane"),
        ("mode_intra_dc", "Intra DC"),
    ), render_mode),
    "boundary": ("Boundary", (
        ("bnd_slice", "Slice"),
        ("bnd_tile", "Tile"),
    ), render_boundary),
    "bits": ("Bit Size", (
        ("bits_ctu", "CTU"),
        ("bits_cu", "CU"),
        ("bits_pu", "PU"),
        ("bits_tu", "TU"),
    ), render_bitsize),
}

# Every overlay flag key (flat overlays + group masters + group sub-layers).
ALL_OVERLAY_KEYS = (
    tuple(OVERLAYS)
    + tuple(OVERLAY_GROUPS)
    + tuple(sk for _lbl, subs, _fn in OVERLAY_GROUPS.values() for sk, _sl in subs)
)
# Group masters start OFF; sub-layers start checked so enabling a group shows a
# complete view. (Partition keeps just PU pre-checked, its established default.)
DEFAULT_ON = (
    "part_pu",
    "mode_inter", "mode_intra_angular", "mode_intra_plane", "mode_intra_dc",
    "bnd_slice", "bnd_tile",
    "bits_cu",      # Bit Size group defaults to the CU-total heatmap
)


def needed_layers(flags: dict) -> set:
    """Sidecar analysis layers required to render the enabled overlays. Lets the
    playback path build only what is shown -- the per-cell layer builds (TU is
    ~40 ms/frame at 1080p) are skipped when their overlay is off."""
    need: set = set()
    if flags.get("qp"):
        need.add("qp")
    if flags.get("types"):
        need.add("blocks")
    if flags.get("bits"):
        if flags.get("bits_ctu"):
            need.add("bits_ctu")
        if flags.get("bits_cu") or flags.get("bits_pu") or flags.get("bits_tu"):
            need.add("bits")
    if flags.get(PARTITION_KEY):
        need.add("blocks")          # CU base is always drawn with partition
        if flags.get("part_pu"):
            need.add("pu")
        if flags.get("part_tu_luma"):
            need.add("tu_luma")
        if flags.get("part_tu_chroma"):
            need.add("tu_chroma")
    if flags.get("mode"):
        if flags.get("mode_inter"):
            need.add("mvs")
        if (flags.get("mode_intra_angular") or flags.get("mode_intra_plane")
                or flags.get("mode_intra_dc")):
            need.add("intra")
    if flags.get("boundary"):
        if flags.get("bnd_slice"):
            need.add("slice")
        if flags.get("bnd_tile"):
            need.add("tile")
    return need
