"""H.264 analysis extractor using stock FFmpeg frame side data.

Data sources (no custom FFmpeg build required):
    - AV_FRAME_DATA_VIDEO_ENC_PARAMS (export_side_data=venc_params):
      per-macroblock QP
    - AV_FRAME_DATA_MOTION_VECTORS (flags2=+export_mvs):
      per-partition motion vectors

mb_type / partition / intra modes are not exported by mainline FFmpeg;
those arrive with the patched-FFmpeg backend (Phase 2) and will populate
FrameAnalysis.blocks through this same interface.
"""

import struct
import sys
from typing import Optional

import numpy as np

from .extractor import CodecExtractor, register
from .schema import MV_DTYPE, FrameAnalysis

_PICT_TYPES = {1: "I", 2: "P", 3: "B", 4: "S", 5: "SI", 6: "SP", 7: "BI"}

# AVVideoEncParams header (public FFmpeg API, 64-bit layout):
# u32 nb_blocks, pad, u64 blocks_offset, u64 block_size, i32 type, i32 qp
_VENC_HEADER = struct.Struct("<I4xQQii")


def _parse_venc_qp(raw: bytes, width: int, height: int,
                   unit: int) -> Optional[np.ndarray]:
    """Parse AVVideoEncParams into a QP grid at unit-pixel granularity."""
    if len(raw) < _VENC_HEADER.size:
        return None
    nb_blocks, blocks_offset, block_size, _type, base_qp = \
        _VENC_HEADER.unpack_from(raw, 0)
    if nb_blocks == 0 or block_size < 20:
        return None
    if blocks_offset + nb_blocks * block_size > len(raw):
        return None

    # AVVideoEncParamsBlock: i32 src_x, src_y, w, h, delta_qp
    block_dtype = np.dtype({
        "names": ["src_x", "src_y", "w", "h", "delta_qp"],
        "formats": ["<i4"] * 5,
        "offsets": [0, 4, 8, 12, 16],
        "itemsize": block_size,
    })
    blocks = np.frombuffer(raw, dtype=block_dtype, count=nb_blocks,
                           offset=blocks_offset)

    rows = (height + unit - 1) // unit
    cols = (width + unit - 1) // unit
    grid = np.full((rows, cols), -1, dtype=np.int16)

    r = blocks["src_y"] // unit
    c = blocks["src_x"] // unit
    ok = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
    grid[r[ok], c[ok]] = (base_qp + blocks["delta_qp"][ok]).astype(np.int16)
    return grid


def _normalize_mvs(arr: np.ndarray) -> np.ndarray:
    """Convert AVMotionVector records to the normalized MV_DTYPE."""
    out = np.empty(len(arr), dtype=MV_DTYPE)
    w = arr["w"].astype(np.int32)
    h = arr["h"].astype(np.int32)
    # dst_x/dst_y are block centers; store top-left corner.
    out["x"] = (arr["dst_x"].astype(np.int32) - w // 2).astype(np.int16)
    out["y"] = (arr["dst_y"].astype(np.int32) - h // 2).astype(np.int16)
    out["w"] = arr["w"]
    out["h"] = arr["h"]
    out["list"] = (arr["source"] > 0).astype(np.uint8)
    scale = arr["motion_scale"].astype(np.float32)
    scale[scale == 0] = 1.0
    out["mv_x"] = arr["motion_x"].astype(np.float32) / scale
    out["mv_y"] = arr["motion_y"].astype(np.float32) / scale
    return out


@register
class H264Extractor(CodecExtractor):
    codec_names = ("h264",)

    QP_UNIT = 16  # macroblock

    def decoder_options(self) -> dict[str, str]:
        return {
            "flags2": "+export_mvs",
            "export_side_data": "+venc_params",
        }

    def extract(self, frame, frame_index: int) -> Optional[FrameAnalysis]:
        analysis = FrameAnalysis(
            frame_index=frame_index,
            codec="h264",
            width=frame.width,
            height=frame.height,
            pict_type=_PICT_TYPES.get(int(frame.pict_type), "?"),
            qp_unit=self.QP_UNIT,
        )
        try:
            for sd in frame.side_data:
                try:
                    type_name = str(sd.type)
                except Exception:
                    continue
                if "VIDEO_ENC_PARAMS" in type_name:
                    analysis.qp_grid = _parse_venc_qp(
                        bytes(sd), frame.width, frame.height, self.QP_UNIT
                    )
                elif "MOTION_VECTORS" in type_name:
                    analysis.mvs = _normalize_mvs(sd.to_ndarray())
        except Exception as e:
            print(f"H264 analysis extraction failed (frame {frame_index}): {e}",
                  file=sys.stderr)
        return analysis
