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

    # Instantaneous bitrate in bits/second: frame bits over the frame's
    # display duration (falls back to frame bits x stream fps when the
    # container reports no per-packet duration).
    instant_bitrate: int = 0

    # NAL unit info (populated after parsing)
    nalu_count: int = 0
    is_multi_slice: bool = False

    # AV1 decode-order model: an MP4 sample (one packet) bundles several coded
    # frames, so for AV1 each FrameInfo is one *coded* frame in decode order.
    # parent_packet is the owning MP4 sample index, packet_byte_off the frame's
    # byte offset within that sample (for on-demand byte reads). order_hint maps
    # a real coded frame to its block analysis; display_index is the output
    # ordinal for display events (shown / show_existing), None for hidden frames.
    # For non-AV1, each frame is its own packet (parent_packet == index).
    parent_packet: int = -1
    packet_byte_off: int = 0
    order_hint: Optional[int] = None
    show_frame: Optional[bool] = None
    show_existing: bool = False
    display_index: Optional[int] = None
    # AV1 references resolved from the bitstream (ref_frame_idx + DPB) as decode
    # indices, forward group (l0) and backward group (l1). None when not
    # resolved (non-AV1, show_existing, or unparsed) -> caller falls back.
    av1_ref_l0: Optional[list] = None
    av1_ref_l1: Optional[list] = None
    # AV1 superblock size in px (64 or 128); stream-constant. None for non-AV1.
    av1_sb_size: Optional[int] = None
    # AV1 tile column / row pixel boundaries [0, ..., frame_edge], from the
    # frame's tile_info (the boundary overlay). None if not parsed / no tiles.
    av1_tile_cols: Optional[tuple] = None
    av1_tile_rows: Optional[tuple] = None

    def __str__(self) -> str:
        return (f"Frame {self.index}: {self.frame_type.value}-frame, "
                f"{self.size} bytes, keyframe={self.is_keyframe}")
