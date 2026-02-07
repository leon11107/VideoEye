"""Video frame decoder using PyAV with smart seeking and caching."""

import bisect
from collections import OrderedDict
from typing import Optional

import av
import numpy as np

from .frame_info import FrameInfo


class Decoder:
    """Decodes video frames to RGB images with smart seeking and LRU cache.

    Performance optimizations over naive decode:
    1. Smart seeking: seeks to nearest keyframe before target, not to frame 0
    2. Sequential fast path: when stepping forward, continues from current
       position without any seeking
    3. LRU frame cache: recently decoded frames are returned instantly
    4. Multi-threaded decoding: uses FFmpeg's thread-based parallelism
    """

    # Max frames to decode forward without re-seeking
    FORWARD_THRESHOLD = 50
    # LRU cache capacity (number of decoded RGB frames)
    # 16 frames @ 1080p ≈ 96 MB; keeps memory bounded while covering
    # a typical GOP for smooth back/forward navigation.
    CACHE_MAX = 16

    def __init__(self):
        self._container: Optional[av.container.InputContainer] = None
        self._video_stream: Optional[av.video.stream.VideoStream] = None
        self._file_path: str = ""
        # Decode cursor: index of the last successfully decoded frame
        self._decode_pos: int = -1
        # Active demux iterator (kept alive for sequential access)
        self._packet_iter = None
        # LRU cache: frame_index -> rgb numpy array
        self._frame_cache: OrderedDict[int, np.ndarray] = OrderedDict()
        # Keyframe index for fast seek target lookup
        self._keyframe_indices: list[int] = []
        # frame_index -> PTS mapping for seeking
        self._frame_pts: dict[int, int] = {}
        # Hardware accel info
        self._hw_accel: str = "software"

    def open(self, file_path: str, frames: Optional[list[FrameInfo]] = None) -> bool:
        """Open a video file for decoding.

        Args:
            file_path: Path to the video file.
            frames: Optional frame list from Demuxer, used to build keyframe
                    index for smart seeking.
        """
        try:
            self.close()
            self._file_path = file_path

            # Build keyframe index from demuxer frame list
            if frames:
                self._keyframe_indices = sorted(
                    f.index for f in frames if f.is_keyframe
                )
                self._frame_pts = {
                    f.index: f.pts for f in frames if f.pts is not None
                }

            # Try to open with hardware acceleration, fall back to software
            if not self._try_open_hw(file_path):
                self._open_sw(file_path)

            if not self._video_stream:
                raise ValueError("No video stream found")

            # Enable multi-threaded decoding
            try:
                self._video_stream.thread_type = "AUTO"
            except Exception:
                pass

            return True

        except Exception as e:
            print(f"Error opening file for decoding: {e}")
            self.close()
            return False

    def _try_open_hw(self, file_path: str) -> bool:
        """Try to open with hardware-accelerated decoding."""
        # Try D3D11VA (Windows), then CUDA, then VAAPI (Linux)
        hw_options = ["d3d11va", "cuda", "vaapi"]
        for hw in hw_options:
            try:
                self._container = av.open(file_path, options={"hwaccel": hw})
                self._video_stream = next(
                    (s for s in self._container.streams if s.type == "video"),
                    None,
                )
                if self._video_stream:
                    self._hw_accel = hw
                    # Verify it actually works by decoding a test frame
                    self._container.seek(0, stream=self._video_stream)
                    for packet in self._container.demux(self._video_stream):
                        for frame in packet.decode():
                            _ = frame.to_ndarray(format="rgb24")
                            # Success - re-seek to beginning for clean state
                            self._container.seek(0, stream=self._video_stream)
                            self._decode_pos = -1
                            self._packet_iter = None
                            return True
                self._container.close()
            except Exception:
                if self._container:
                    try:
                        self._container.close()
                    except Exception:
                        pass
                self._container = None
                self._video_stream = None
        return False

    def _open_sw(self, file_path: str) -> None:
        """Open with software decoding."""
        self._container = av.open(file_path)
        self._video_stream = next(
            (s for s in self._container.streams if s.type == "video"), None
        )
        self._hw_accel = "software"

    def close(self) -> None:
        """Close the decoder and release resources."""
        self._packet_iter = None
        if self._container:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._video_stream = None
        self._decode_pos = -1
        self._frame_cache.clear()
        self._keyframe_indices.clear()
        self._frame_pts.clear()

    def decode_frame(self, frame_index: int) -> Optional[np.ndarray]:
        """Decode a specific frame by index, returning RGB numpy array.

        Uses smart seeking and caching for performance:
        - Cached frames are returned immediately.
        - Sequential forward access continues without seeking.
        - Random access seeks to nearest preceding keyframe.
        """
        if not self._container or not self._video_stream:
            return None

        # 1. Check LRU cache
        if frame_index in self._frame_cache:
            self._frame_cache.move_to_end(frame_index)
            return self._frame_cache[frame_index]

        try:
            # 2. Determine if we can continue from current position
            can_continue = (
                self._packet_iter is not None
                and self._decode_pos >= 0
                and frame_index > self._decode_pos
                and frame_index - self._decode_pos <= self.FORWARD_THRESHOLD
            )

            if not can_continue:
                # 3. Seek to nearest keyframe before target
                self._seek_to_keyframe(frame_index)

            # 4. Decode forward to the target frame
            return self._decode_until(frame_index)

        except Exception as e:
            print(f"Error decoding frame {frame_index}: {e}")
            self._packet_iter = None
            return None

    def _seek_to_keyframe(self, target_index: int) -> None:
        """Seek to the nearest keyframe at or before target_index."""
        # Find nearest preceding keyframe using binary search
        if self._keyframe_indices:
            pos = bisect.bisect_right(self._keyframe_indices, target_index) - 1
            kf_index = self._keyframe_indices[max(0, pos)]
        else:
            kf_index = 0

        # If we're already past this keyframe and before target, no need to seek
        if (
            self._packet_iter is not None
            and self._decode_pos >= kf_index
            and self._decode_pos < target_index
            and target_index - self._decode_pos <= self.FORWARD_THRESHOLD
        ):
            return

        # Perform the seek using the keyframe's PTS
        pts = self._frame_pts.get(kf_index)
        if pts is not None:
            self._container.seek(pts, stream=self._video_stream)
        else:
            self._container.seek(0, stream=self._video_stream)
            kf_index = 0

        # Reset decode cursor to just before the keyframe
        self._decode_pos = kf_index - 1
        # Create a fresh demux iterator from the seek position
        self._packet_iter = self._container.demux(self._video_stream)

    def _decode_until(self, target_index: int) -> Optional[np.ndarray]:
        """Decode frames from current position until target_index.

        All intermediate frames are cached for potential future use.
        """
        if self._packet_iter is None:
            return None

        for packet in self._packet_iter:
            for frame in packet.decode():
                self._decode_pos += 1
                rgb = frame.to_ndarray(format="rgb24")
                self._cache_put(self._decode_pos, rgb)

                if self._decode_pos == target_index:
                    return rgb
                if self._decode_pos > target_index:
                    # Overshot — return from cache if available
                    return self._frame_cache.get(target_index)

        return None

    def _cache_put(self, index: int, rgb: np.ndarray) -> None:
        """Add a frame to the LRU cache, evicting oldest if full."""
        if index in self._frame_cache:
            self._frame_cache.move_to_end(index)
        else:
            self._frame_cache[index] = rgb
            if len(self._frame_cache) > self.CACHE_MAX:
                self._frame_cache.popitem(last=False)

    def set_keyframe_index(self, frames: list[FrameInfo]) -> None:
        """Update keyframe index from a frame list (e.g. after loading)."""
        self._keyframe_indices = sorted(
            f.index for f in frames if f.is_keyframe
        )
        self._frame_pts = {
            f.index: f.pts for f in frames if f.pts is not None
        }

    @property
    def hw_accel(self) -> str:
        """The active hardware acceleration backend (or 'software')."""
        return self._hw_accel

    @property
    def width(self) -> int:
        if self._video_stream:
            return self._video_stream.width
        return 0

    @property
    def height(self) -> int:
        if self._video_stream:
            return self._video_stream.height
        return 0

    @property
    def is_open(self) -> bool:
        return self._container is not None
