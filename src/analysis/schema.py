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

# Per-CU coded bit cost (HEVC): one record per coding unit, carrying the total
# and the prediction / residual split (bits). Used by the Bit Size heatmap.
BITSIZE_DTYPE = np.dtype([
    ("x", np.int16),
    ("y", np.int16),
    ("w", np.uint8),
    ("h", np.uint8),
    ("cu", np.int32),       # total bits coding this CU
    ("pu", np.int32),       # prediction-syntax bits
    ("tu", np.int32),       # residual (transform) bits
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


# H.264 raster 4x4 position (y4*4+x4) -> block-scan index, inverse of the
# block-scan->position map used when exporting per-4x4 intra modes.
def _h264_raster_to_scan():
    inv = [0] * 16
    for i in range(16):
        blk8, sub = i >> 2, i & 3
        x4 = (blk8 & 1) * 2 + (sub & 1)
        y4 = (blk8 >> 1) * 2 + (sub >> 1)
        inv[y4 * 4 + x4] = i
    return tuple(inv)


_H264_RASTER_TO_SCAN = _h264_raster_to_scan()


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

    # Per-CU coded bit cost (BITSIZE_DTYPE) for the Bit Size heatmap (HEVC).
    bit_sizes: Optional[np.ndarray] = None
    # Per-CTU coded bit cost (BITSIZE_DTYPE, summed over the CTB's CUs) for the
    # CTU-level Bit Size heatmap and the CTU block-info section (HEVC).
    ctu_bit_sizes: Optional[np.ndarray] = None
    # CTU structure (HEVC): CTB size px, per-CTB slice id grid, tile column/row
    # pixel boundaries -- for the CTU block-info location / slice / tile fields.
    ctb_size: int = 0
    slice_grid: Optional[np.ndarray] = None
    tile_col_bd: tuple = ()
    tile_row_bd: tuple = ()

    # H.264 per-MB aux at qp_unit (16px) granularity: intra type (0 inter/skip,
    # 1 I_NxN, 2 I_16x16, 3 PCM), canonical luma intra mode (-1 if not intra),
    # slice id. None for other codecs.
    h264_intra_type: Optional[np.ndarray] = None
    h264_luma_mode: Optional[np.ndarray] = None
    h264_slice: Optional[np.ndarray] = None
    # H.264 exact per-4x4 luma intra mode grid (grid_h, grid_w, 16) in H.264
    # block-scan order, and per-MB intra block size (16/8/4/0). For the block
    # info panel's exact sub-block lookup (the overlay uses one mode per MB).
    h264_mode4: Optional[np.ndarray] = None
    h264_blocksize: Optional[np.ndarray] = None

    # AV1 luma palette size per MI cell (0 = none, 2..8 colors), at qp_unit
    # granularity. None for other codecs / when not requested.
    av1_palette: Optional[np.ndarray] = None
    # AV1 filter-intra mode per MI cell (0..4, -1 = not used), at qp_unit
    # granularity. None for other codecs / when not requested.
    av1_filter_intra: Optional[np.ndarray] = None
    # AV1 segment id per MI cell (0..7), at qp_unit granularity. None for other
    # codecs / when not requested.
    av1_segment_id: Optional[np.ndarray] = None
    # AV1 CDEF luma primary / secondary strength per MI cell (0 = no CDEF), at
    # qp_unit granularity. None for other codecs / when not requested.
    av1_cdef_level: Optional[np.ndarray] = None
    av1_cdef_strength: Optional[np.ndarray] = None
    av1_cdef_uv_level: Optional[np.ndarray] = None
    av1_cdef_uv_strength: Optional[np.ndarray] = None
    # AV1 per-plane (Y, U, V) loop-restoration frame type (0..3) and unit size
    # (px). Frame-level (same for every block of the frame).
    av1_lr_type: tuple = ()
    av1_lr_unit_size: tuple = ()

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

    def palette_at(self, px: int, py: int) -> Optional[int]:
        """AV1 luma palette size (0 = none, 2..8 colors) covering pixel
        (px, py), or None if no palette data. Indexed at qp_unit granularity
        (the AV1 MI grid, same as the QP grid)."""
        if self.av1_palette is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        if row >= self.av1_palette.shape[0] or col >= self.av1_palette.shape[1]:
            return None
        return int(self.av1_palette[row, col])

    def filter_intra_at(self, px: int, py: int) -> Optional[int]:
        """AV1 filter-intra mode (0..4) covering pixel (px, py), or None if no
        filter-intra data or the block does not use it (-1). Indexed at qp_unit
        granularity (the AV1 MI grid)."""
        if self.av1_filter_intra is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        if (row >= self.av1_filter_intra.shape[0]
                or col >= self.av1_filter_intra.shape[1]):
            return None
        fi = int(self.av1_filter_intra[row, col])
        return fi if fi >= 0 else None

    def segment_id_at(self, px: int, py: int) -> Optional[int]:
        """AV1 segment id (0..7) covering pixel (px, py), or None if no
        segmentation data. Indexed at qp_unit granularity (the AV1 MI grid)."""
        if self.av1_segment_id is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        if (row >= self.av1_segment_id.shape[0]
                or col >= self.av1_segment_id.shape[1]):
            return None
        return int(self.av1_segment_id[row, col])

    def cdef_at(self, px: int, py: int):
        """AV1 CDEF strengths covering pixel (px, py) as
        (y_pri, y_sec, uv_pri, uv_sec), or None if no CDEF data. All-zero means
        CDEF applies no filtering here. Indexed at qp_unit granularity."""
        if self.av1_cdef_level is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        if (row >= self.av1_cdef_level.shape[0]
                or col >= self.av1_cdef_level.shape[1]):
            return None
        uvl = (int(self.av1_cdef_uv_level[row, col])
               if self.av1_cdef_uv_level is not None else 0)
        uvs = (int(self.av1_cdef_uv_strength[row, col])
               if self.av1_cdef_uv_strength is not None else 0)
        return (int(self.av1_cdef_level[row, col]),
                int(self.av1_cdef_strength[row, col]), uvl, uvs)

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

    def bits_at(self, px: int, py: int):
        """Per-CU bit-cost record (BITSIZE_DTYPE) covering pixel (px, py), or
        None (e.g. codecs without bit data)."""
        if self.bit_sizes is None or len(self.bit_sizes) == 0 or px < 0 or py < 0:
            return None
        b = self.bit_sizes
        mask = (
            (b["x"] <= px) & (px < b["x"] + b["w"])
            & (b["y"] <= py) & (py < b["y"] + b["h"])
        )
        hits = b[mask]
        return hits[0] if len(hits) else None

    def ctu_bits_at(self, px: int, py: int):
        """Per-CTU bit-cost record (BITSIZE_DTYPE, summed over the CTB), or
        None."""
        if (self.ctu_bit_sizes is None or len(self.ctu_bit_sizes) == 0
                or px < 0 or py < 0):
            return None
        b = self.ctu_bit_sizes
        mask = (
            (b["x"] <= px) & (px < b["x"] + b["w"])
            & (b["y"] <= py) & (py < b["y"] + b["h"])
        )
        hits = b[mask]
        return hits[0] if len(hits) else None

    def ctu_origin(self, px: int, py: int):
        """Pixel origin (ox, oy) of the CTB covering (px, py), or None."""
        if self.ctb_size <= 0 or px < 0 or py < 0:
            return None
        return (px // self.ctb_size) * self.ctb_size, \
               (py // self.ctb_size) * self.ctb_size

    def slice_idx_at(self, px: int, py: int) -> Optional[int]:
        """Slice id of the CTB covering (px, py), or None."""
        if self.slice_grid is None or self.ctb_size <= 0 or px < 0 or py < 0:
            return None
        row, col = py // self.ctb_size, px // self.ctb_size
        if row >= self.slice_grid.shape[0] or col >= self.slice_grid.shape[1]:
            return None
        return int(self.slice_grid[row, col])

    def tile_idx_at(self, px: int, py: int) -> Optional[int]:
        """Tile index (raster order over the tile grid) at (px, py), or None.

        Tile column/row boundaries are pixel x/y of the left/top edge of each
        tile (first entry 0). The index is row * n_tile_cols + col.
        """
        if not self.tile_col_bd or not self.tile_row_bd or px < 0 or py < 0:
            return None
        col = sum(1 for b in self.tile_col_bd if b <= px) - 1
        row = sum(1 for b in self.tile_row_bd if b <= py) - 1
        if col < 0 or row < 0:
            return None
        return row * len(self.tile_col_bd) + col

    def intra_at(self, px: int, py: int):
        """Intra-prediction record (INTRA_DTYPE) covering pixel (px, py), or
        None. The smallest covering block wins (most specific sub-block)."""
        if self.intra is None or len(self.intra) == 0 or px < 0 or py < 0:
            return None
        a = self.intra
        mask = (
            (a["x"] <= px) & (px < a["x"] + a["w"])
            & (a["y"] <= py) & (py < a["y"] + a["h"])
        )
        hits = a[mask]
        if len(hits) == 0:
            return None
        areas = hits["w"].astype(np.int32) * hits["h"].astype(np.int32)
        return hits[int(np.argmin(areas))]

    def h264_intra_at(self, px: int, py: int):
        """Exact H.264 intra (mode, block_size) at pixel (px, py), or None when
        not intra. block_size is 16/8/4; mode is the canonical luma intra mode
        of the covering 4x4 sub-block."""
        if self.h264_blocksize is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        if (row >= self.h264_blocksize.shape[0]
                or col >= self.h264_blocksize.shape[1]):
            return None
        size = int(self.h264_blocksize[row, col])
        if size == 0:
            return None
        mode = None
        if self.h264_mode4 is not None:
            x4 = (px % self.qp_unit) // 4
            y4 = (py % self.qp_unit) // 4
            i = _H264_RASTER_TO_SCAN[y4 * 4 + x4]
            mode = int(self.h264_mode4[row, col, i])
        return mode, size

    def h264_aux_at(self, px: int, py: int):
        """H.264 (intra_type, luma_mode, slice_id) at pixel (px, py), or None."""
        if self.h264_intra_type is None or px < 0 or py < 0:
            return None
        row, col = py // self.qp_unit, px // self.qp_unit
        g = self.h264_intra_type
        if row >= g.shape[0] or col >= g.shape[1]:
            return None
        return (int(self.h264_intra_type[row, col]),
                int(self.h264_luma_mode[row, col]),
                int(self.h264_slice[row, col]))

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
