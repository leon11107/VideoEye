"""Block-level analysis: codec-agnostic schema + per-codec extractors."""

from .schema import FrameAnalysis, PredType, MV_DTYPE, BLOCK_DTYPE
from .extractor import CodecExtractor, create_extractor, register

# Import codec modules so their @register decorators run.
from . import h264  # noqa: F401
from . import hevc  # noqa: F401
from . import av1  # noqa: F401

__all__ = [
    "FrameAnalysis", "PredType", "MV_DTYPE", "BLOCK_DTYPE",
    "CodecExtractor", "create_extractor", "register",
]
