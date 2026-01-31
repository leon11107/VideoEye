"""Frame metadata container."""

from dataclasses import dataclass, field
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
    duration: Optional[int] = None  # Frame duration in time_base units
    size: int = 0  # Frame size in bytes
    is_keyframe: bool = False  # Is this a keyframe/IDR
    frame_type: FrameType = FrameType.UNKNOWN
    packet_data: bytes = field(default_factory=bytes, repr=False)  # Raw packet data

    # Timing in seconds (calculated from pts and time_base)
    time_seconds: float = 0.0

    # NAL unit info (populated after parsing)
    nalu_count: int = 0
    is_multi_slice: bool = False

    def __str__(self) -> str:
        return (f"Frame {self.index}: {self.frame_type.value}-frame, "
                f"{self.size} bytes, keyframe={self.is_keyframe}")
