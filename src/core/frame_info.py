"""Frame metadata container."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FrameType(Enum):
    """Video frame type."""
    I = "I"  # Intra-coded
    P = "P"  # Predictive
    B = "B"  # Bi-predictive
    UNKNOWN = "?"


@dataclass
class FrameInfo:
    """Container for frame metadata."""

    index: int  # Frame index in stream
    pts: Optional[int] = None  # Presentation timestamp
    dts: Optional[int] = None  # Decode timestamp
    pos: Optional[int] = None  # Byte offset of the packet in the file
    poc: Optional[int] = None  # Global display order key from POC (raw streams)
    duration: Optional[int] = None  # Frame duration in time_base units
    size: int = 0  # Frame size in bytes
    is_keyframe: bool = False  # Is this a keyframe/IDR
    frame_type: FrameType = FrameType.UNKNOWN

    # Timing in seconds (calculated from pts and time_base)
    time_seconds: float = 0.0

    # NAL unit info (populated after parsing)
    nalu_count: int = 0
    is_multi_slice: bool = False

    def __str__(self) -> str:
        return (f"Frame {self.index}: {self.frame_type.value}-frame, "
                f"{self.size} bytes, keyframe={self.is_keyframe}")
