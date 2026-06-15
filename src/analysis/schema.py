"""Codec-agnostic per-frame block analysis data model.

All analyzer backends (stock FFmpeg side data, patched FFmpeg, etc.)
normalize their output into FrameAnalysis so views never see codec
specifics. Adding a codec or a new coding tool must not change this
module's consumers.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Normalized motion vector record. Positions are luma pixels in the
# decoded frame; mv_x/mv_y are in pixels (fractional pel resolved).
MV_DTYPE = np.dtype([
    ("x", np.int16),
    ("y", np.int16),
    ("w", np.uint8),
    ("h", np.uint8),
    ("list", np.uint8),     # 0 = L0 (past ref), 1 = L1 (future ref)
    ("mv_x", np.float32),
    ("mv_y", np.float32),
])

# Intra prediction record: one per intra-coded block. `cat` is the display
# category (DC / planar / angular); `mode` is the codec's raw intra mode
# (HEVC 0..34, AV1 PREDICTION_MODE) used to derive an angular direction.
INTRA_DC = 0
INTRA_PLANE = 1
INTRA_ANGULAR = 2
INTRA_DTYPE = np.dtype([
    ("x", np.int16),
    ("y", np.int16),
    ("w", np.uint8),
    ("h", np.uint8),
    ("mode", np.int16),
    ("cat", np.uint8),      # INTRA_DC / INTRA_PLANE / INTRA_ANGULAR
])

# Normalized coding block record (partition + prediction type).
BLOCK_DTYPE = np.dtype([
    ("x", np.int16),
    ("y", np.int16),
    ("w", np.uint8),
    ("h", np.uint8),
    ("depth", np.uint8),
    ("pred", np.uint8),     # PredType
    ("mode", np.int16),     # codec-specific mode id (label via extension)
])


class PredType:
    UNKNOWN = 0
    INTRA = 1
    INTER = 2       # uni-directional inter (L0 or L1)
    SKIP = 3
    IPCM = 4
    BI = 5          # bi-directional inter (L0 and L1)

    NAMES = {0: "?", 1: "Intra", 2: "Inter", 3: "Skip", 4: "IPCM", 5: "BI"}


@dataclass
class FrameAnalysis:
    """Per-frame block-level analysis in a codec-agnostic form."""

    frame_index: int
    codec: str
    width: int
    height: int
    pict_type: str = "?"

    # QP grid at qp_unit-pixel granularity; int16, -1 = unknown.
    qp_unit: int = 16
    qp_grid: Optional[np.ndarray] = None

    # QP value that maps to the hottest heatmap color. Default 63 leaves
    # H.264/HEVC (max QP 51) rendering unchanged; AV1 sets 255 because its
    # qp_grid holds current_qindex (0..255), not a 0..51 QP.
    qp_max: int = 63

    # Motion vectors (MV_DTYPE) and coding blocks (BLOCK_DTYPE).
    mvs: Optional[np.ndarray] = None
    blocks: Optional[np.ndarray] = None     # coding units (CU)
    # Partition sub-layers (BLOCK_DTYPE rectangles) for the partition overlay.
    pu: Optional[np.ndarray] = None         # prediction units
    tu_luma: Optional[np.ndarray] = None    # luma transform units (stage 2)
    tu_chroma: Optional[np.ndarray] = None  # chroma transform units (stage 2)

    # Picture-structure boundary line segments [x1, y1, x2, y2] in pixels
    # (HEVC). slice_lines: between CTBs of different slices; tile_lines: tile
    # column/row boundaries. Shape (N, 4) int32, or None.
    slice_lines: Optional[np.ndarray] = None
    tile_lines: Optional[np.ndarray] = None

    # Intra prediction records (INTRA_DTYPE) for the intra-mode overlays
    # (angular / planar / DC). None when not built or unavailable (H.264).
    intra: Optional[np.ndarray] = None

    # Future codec features attach here as named chunks (e.g. "sao",
    # "alf", "cdef") without touching this schema.
    extensions: dict = field(default_factory=dict)

    def qp_stats(self) -> Optional[tuple[int, int, float]]:
        """(min, max, mean) of known QP values, or None."""
        if self.qp_grid is None:
            return None
        valid = self.qp_grid[self.qp_grid >= 0]
        if valid.size == 0:
            return None
        return int(valid.min()), int(valid.max()), float(valid.mean())

    def qp_at(self, px: int, py: int) -> Optional[int]:
        """QP of the block covering pixel (px, py), or None."""
        if self.qp_grid is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        if row >= self.qp_grid.shape[0] or col >= self.qp_grid.shape[1]:
            return None
        qp = int(self.qp_grid[row, col])
        return qp if qp >= 0 else None

    def mvs_at(self, px: int, py: int) -> np.ndarray:
        """Motion vectors whose block covers pixel (px, py)."""
        if self.mvs is None:
            return np.empty(0, dtype=MV_DTYPE)
        m = self.mvs
        mask = (
            (m["x"] <= px) & (px < m["x"] + m["w"])
            & (m["y"] <= py) & (py < m["y"] + m["h"])
        )
        return m[mask]

    def block_at(self, px: int, py: int):
        """Coding block (BLOCK_DTYPE record) covering pixel (px, py), or None.

        When partitions nest, the smallest covering block wins so the most
        specific sub-partition is reported.
        """
        if self.blocks is None or len(self.blocks) == 0 or px < 0 or py < 0:
            return None
        b = self.blocks
        mask = (
            (b["x"] <= px) & (px < b["x"] + b["w"])
            & (b["y"] <= py) & (py < b["y"] + b["h"])
        )
        hits = b[mask]
        if len(hits) == 0:
            return None
        areas = hits["w"].astype(np.int32) * hits["h"].astype(np.int32)
        return hits[int(np.argmin(areas))]
