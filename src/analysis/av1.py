"""AV1 analysis extractor.

Mainline FFmpeg exposes no per-block side data for AV1, so every
block-level field -- partition/size, prediction type, QP and motion
vectors -- comes from the patched-FFmpeg sidecar (libaom CONFIG_INSPECTION).
This extractor only captures per-frame metadata; the decoder fills
mvs/qp_grid/blocks from the sidecar afterward (see Decoder.get_analysis).
"""

from typing import Optional

from .extractor import CodecExtractor, register
from .schema import FrameAnalysis

_PICT_TYPES = {1: "I", 2: "P", 3: "B", 4: "S", 5: "SI", 6: "SP", 7: "BI"}


@register
class AV1Extractor(CodecExtractor):
    # PyAV reports the active decoder's name, which for AV1 may be the
    # native "av1", dav1d ("libdav1d"), or libaom ("libaom-av1") depending
    # on the FFmpeg build PyAV bundles.
    codec_names = ("av1", "libdav1d", "libaom-av1")

    # AV1 mode-info granularity is 4x4 (MI_SIZE); the sidecar reports the
    # actual unit per frame and the decoder overrides qp_unit when it fills
    # the grid.
    QP_UNIT = 4

    # AV1 qp_grid holds current_qindex (0..255), not a 0..51 QP.
    QP_MAX = 255

    def extract(self, frame, frame_index: int) -> Optional[FrameAnalysis]:
        return FrameAnalysis(
            frame_index=frame_index,
            codec="av1",
            width=frame.width,
            height=frame.height,
            pict_type=_PICT_TYPES.get(int(frame.pict_type), "?"),
            qp_unit=self.QP_UNIT,
            qp_max=self.QP_MAX,
        )
