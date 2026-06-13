"""HEVC analysis extractor.

Mainline FFmpeg exposes no per-block side data for HEVC (only SEI), so
every block-level field -- partition/type/size, QP and motion vectors --
comes from the patched-FFmpeg sidecar. This extractor therefore only
captures per-frame metadata; the decoder fills mvs/qp_grid/blocks from the
sidecar afterward (see Decoder.get_analysis).
"""

from typing import Optional

from .extractor import CodecExtractor, register
from .schema import FrameAnalysis

_PICT_TYPES = {1: "I", 2: "P", 3: "B", 4: "S", 5: "SI", 6: "SP", 7: "BI"}


@register
class HEVCExtractor(CodecExtractor):
    codec_names = ("hevc", "h265")

    # min coding block; the sidecar reports the actual unit per frame and
    # the decoder overrides qp_unit when it fills the grid.
    QP_UNIT = 8

    def extract(self, frame, frame_index: int) -> Optional[FrameAnalysis]:
        return FrameAnalysis(
            frame_index=frame_index,
            codec="hevc",
            width=frame.width,
            height=frame.height,
            pict_type=_PICT_TYPES.get(int(frame.pict_type), "?"),
            qp_unit=self.QP_UNIT,
        )
