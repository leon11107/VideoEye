"""Stream metadata container."""

from dataclasses import dataclass, field
from typing import Optional
from fractions import Fraction


@dataclass
class StreamInfo:
    """Container for video stream metadata."""

    # Container info
    container_format: str = ""
    file_path: str = ""
    file_size: int = 0

    # Video codec info
    codec_name: str = ""  # e.g., "h264", "hevc"
    codec_long_name: str = ""  # e.g., "H.264 / AVC / MPEG-4 AVC"
    profile: str = ""  # e.g., "High", "Main"
    level: str = ""  # e.g., "4.0", "5.1"

    # Dimensions
    width: int = 0
    height: int = 0
    coded_width: int = 0
    coded_height: int = 0

    # Aspect ratio
    sample_aspect_ratio: Optional[Fraction] = None
    display_aspect_ratio: Optional[Fraction] = None

    # Timing
    frame_rate: Optional[Fraction] = None  # Frames per second
    avg_frame_rate: Optional[Fraction] = None
    time_base: Optional[Fraction] = None
    duration_seconds: float = 0.0

    # Frame counts
    total_frames: int = 0
    keyframe_count: int = 0

    # Bitrate
    bit_rate: int = 0  # bits per second
    max_bit_rate: int = 0

    # Color info
    pix_fmt: str = ""  # Pixel format, e.g., "yuv420p"
    color_range: str = ""
    color_space: str = ""
    color_primaries: str = ""
    color_trc: str = ""  # Transfer characteristics

    # H.264/H.265 specific
    is_avc: bool = False  # Using length-prefixed NALUs (MP4) vs start codes
    nal_length_size: int = 4  # Usually 4 for AVC

    def frame_rate_float(self) -> float:
        """Get frame rate as float."""
        if self.frame_rate:
            return float(self.frame_rate)
        if self.avg_frame_rate:
            return float(self.avg_frame_rate)
        return 0.0

    def aspect_ratio_str(self) -> str:
        """Get display aspect ratio as string."""
        if self.display_aspect_ratio:
            return f"{self.display_aspect_ratio.numerator}:{self.display_aspect_ratio.denominator}"
        if self.width and self.height:
            from math import gcd
            g = gcd(self.width, self.height)
            return f"{self.width // g}:{self.height // g}"
        return "N/A"

    def duration_str(self) -> str:
        """Get duration as HH:MM:SS.mmm string."""
        if self.duration_seconds <= 0:
            return "N/A"
        hours = int(self.duration_seconds // 3600)
        minutes = int((self.duration_seconds % 3600) // 60)
        seconds = self.duration_seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:06.3f}"
        return f"{minutes}:{seconds:06.3f}"

    def bitrate_str(self) -> str:
        """Get bitrate as human-readable string."""
        if self.bit_rate <= 0:
            return "N/A"
        if self.bit_rate >= 1_000_000:
            return f"{self.bit_rate / 1_000_000:.2f} Mbps"
        return f"{self.bit_rate / 1_000:.2f} Kbps"

    def resolution_str(self) -> str:
        """Get resolution as WxH string."""
        return f"{self.width}x{self.height}"
