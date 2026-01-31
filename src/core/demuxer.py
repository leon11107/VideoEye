"""FFmpeg-based video demuxer using PyAV."""

import av
from pathlib import Path
from fractions import Fraction
from typing import Optional, Iterator

from .frame_info import FrameInfo, FrameType
from .stream_info import StreamInfo


class Demuxer:
    """Demuxes video files and extracts frame/stream information."""

    def __init__(self):
        self._container: Optional[av.container.InputContainer] = None
        self._video_stream: Optional[av.video.stream.VideoStream] = None
        self._stream_info: Optional[StreamInfo] = None
        self._frames: list[FrameInfo] = []
        self._file_path: str = ""

    def open(self, file_path: str) -> bool:
        """Open a video file for demuxing."""
        try:
            self.close()
            self._file_path = file_path
            self._container = av.open(file_path)

            # Find first video stream
            for stream in self._container.streams:
                if stream.type == 'video':
                    self._video_stream = stream
                    break

            if not self._video_stream:
                raise ValueError("No video stream found")

            self._extract_stream_info()
            self._extract_frames()
            return True

        except Exception as e:
            print(f"Error opening file: {e}")
            self.close()
            return False

    def close(self) -> None:
        """Close the current file."""
        if self._container:
            self._container.close()
        self._container = None
        self._video_stream = None
        self._stream_info = None
        self._frames = []

    def _extract_stream_info(self) -> None:
        """Extract stream metadata."""
        if not self._container or not self._video_stream:
            return

        stream = self._video_stream
        codec_ctx = stream.codec_context
        file_path = Path(self._file_path)

        info = StreamInfo()
        info.file_path = self._file_path
        info.file_size = file_path.stat().st_size if file_path.exists() else 0

        # Container format
        info.container_format = self._container.format.name

        # Codec info
        info.codec_name = codec_ctx.name if codec_ctx else ""
        info.codec_long_name = codec_ctx.codec.long_name if codec_ctx and codec_ctx.codec else ""

        # Profile and level
        if codec_ctx:
            if hasattr(codec_ctx, 'profile') and codec_ctx.profile:
                info.profile = codec_ctx.profile
            # Level is typically in codec-specific extradata

        # Dimensions
        info.width = stream.width
        info.height = stream.height
        info.coded_width = stream.coded_width or stream.width
        info.coded_height = stream.coded_height or stream.height

        # Aspect ratios
        if stream.sample_aspect_ratio:
            info.sample_aspect_ratio = Fraction(
                stream.sample_aspect_ratio.numerator,
                stream.sample_aspect_ratio.denominator
            )
            # Calculate display aspect ratio
            dar_num = stream.width * stream.sample_aspect_ratio.numerator
            dar_den = stream.height * stream.sample_aspect_ratio.denominator
            from math import gcd
            g = gcd(dar_num, dar_den)
            if g > 0:
                info.display_aspect_ratio = Fraction(dar_num // g, dar_den // g)

        # Frame rate
        if stream.average_rate:
            info.avg_frame_rate = Fraction(
                stream.average_rate.numerator,
                stream.average_rate.denominator
            )
        if stream.base_rate:
            info.frame_rate = Fraction(
                stream.base_rate.numerator,
                stream.base_rate.denominator
            )

        # Time base
        if stream.time_base:
            info.time_base = Fraction(
                stream.time_base.numerator,
                stream.time_base.denominator
            )

        # Duration
        if stream.duration and stream.time_base:
            info.duration_seconds = float(stream.duration * stream.time_base)
        elif self._container.duration:
            info.duration_seconds = self._container.duration / av.time_base

        # Frames count - will be updated after extraction
        info.total_frames = stream.frames if stream.frames > 0 else 0

        # Bitrate
        if codec_ctx and codec_ctx.bit_rate:
            info.bit_rate = codec_ctx.bit_rate
        elif self._container.bit_rate:
            info.bit_rate = self._container.bit_rate

        # Pixel format
        if codec_ctx and codec_ctx.pix_fmt:
            info.pix_fmt = codec_ctx.pix_fmt

        # Color info
        if hasattr(stream, 'color_range'):
            info.color_range = str(stream.color_range) if stream.color_range else ""
        if hasattr(stream, 'color_space'):
            info.color_space = str(stream.color_space) if stream.color_space else ""

        # AVC/HEVC NAL format detection
        # Check if using length-prefixed NALUs (MP4-style) vs start codes
        info.is_avc = info.container_format in ('mov', 'mp4', 'm4v', 'mkv', 'webm')
        if codec_ctx and codec_ctx.extradata:
            # For H.264, first byte of extradata indicates AVC format
            extradata = bytes(codec_ctx.extradata)
            if info.codec_name in ('h264', 'hevc') and len(extradata) > 0:
                if extradata[0] == 1:  # AVCDecoderConfigurationRecord
                    info.is_avc = True
                    if len(extradata) > 4:
                        info.nal_length_size = (extradata[4] & 0x03) + 1

        self._stream_info = info

    def _extract_frames(self) -> None:
        """Extract frame information without decoding."""
        if not self._container or not self._video_stream:
            return

        self._frames = []
        stream = self._video_stream
        time_base = float(stream.time_base) if stream.time_base else 1.0

        # Reset to beginning
        self._container.seek(0)

        frame_index = 0
        keyframe_count = 0
        max_bitrate = 0

        for packet in self._container.demux(stream):
            if packet.dts is None and packet.pts is None:
                continue

            # Determine frame type from packet
            is_key = packet.is_keyframe
            if is_key:
                keyframe_count += 1
                frame_type = FrameType.I
            else:
                # Will be refined by NAL parsing later
                frame_type = FrameType.P

            frame = FrameInfo(
                index=frame_index,
                pts=packet.pts,
                dts=packet.dts,
                duration=packet.duration,
                size=packet.size,
                is_keyframe=is_key,
                frame_type=frame_type,
                packet_data=bytes(packet),
                time_seconds=packet.pts * time_base if packet.pts else 0.0
            )

            self._frames.append(frame)

            # Track max bitrate (instantaneous)
            if packet.duration and packet.duration > 0:
                instant_bitrate = int((packet.size * 8) / (packet.duration * time_base))
                max_bitrate = max(max_bitrate, instant_bitrate)

            frame_index += 1

        # Update stream info
        if self._stream_info:
            self._stream_info.total_frames = len(self._frames)
            self._stream_info.keyframe_count = keyframe_count
            self._stream_info.max_bit_rate = max_bitrate

            # Calculate average bitrate if not set
            if self._stream_info.bit_rate == 0 and self._stream_info.duration_seconds > 0:
                total_bytes = sum(f.size for f in self._frames)
                self._stream_info.bit_rate = int((total_bytes * 8) / self._stream_info.duration_seconds)

    @property
    def stream_info(self) -> Optional[StreamInfo]:
        """Get stream information."""
        return self._stream_info

    @property
    def frames(self) -> list[FrameInfo]:
        """Get list of frame information."""
        return self._frames

    def get_frame(self, index: int) -> Optional[FrameInfo]:
        """Get frame info by index."""
        if 0 <= index < len(self._frames):
            return self._frames[index]
        return None

    @property
    def is_open(self) -> bool:
        """Check if a file is currently open."""
        return self._container is not None

    @property
    def codec_name(self) -> str:
        """Get codec name (h264, hevc, etc.)."""
        return self._stream_info.codec_name if self._stream_info else ""

    def get_extradata(self) -> Optional[bytes]:
        """Get codec extradata (contains SPS/PPS for H.264/H.265)."""
        if self._video_stream and self._video_stream.codec_context:
            extradata = self._video_stream.codec_context.extradata
            return bytes(extradata) if extradata else None
        return None
