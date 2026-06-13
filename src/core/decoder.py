"""Video frame decoder using PyAV with smart seeking and caching."""

import bisect
from collections import OrderedDict
from typing import Optional

import av
import numpy as np

from .frame_info import FrameInfo
from ..analysis import FrameAnalysis, create_extractor


class Decoder:
    """Decodes video frames to RGB images with smart seeking and LRU cache.

    A single decode pass produces both the RGB image and the block-level
    FrameAnalysis (QP/MV/...) via the codec's registered extractor, so
    analysis adds no extra decoding cost.

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
        # LRU cache: frame_index -> (rgb array, FrameAnalysis or None)
        self._frame_cache: OrderedDict[
            int, tuple[np.ndarray, Optional[FrameAnalysis]]
        ] = OrderedDict()
        # Per-codec block analysis extractor (None if unsupported codec)
        self._extractor = None
        # Keyframe index for fast seek target lookup
        self._keyframe_indices: list[int] = []
        # frame_index -> seek timestamp (DTS preferred: av_seek_frame
        # operates on DTS for most demuxers, e.g. mpegts)
        self._frame_seek_ts: dict[int, int] = {}
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
                self._frame_seek_ts = self._build_seek_ts(frames)

            # Software decoding only: block-level side data (QP, MVs) is
            # produced by the software decoder, never by hwaccel paths.
            self._container = av.open(file_path)
            self._video_stream = next(
                (s for s in self._container.streams if s.type == "video"),
                None,
            )
            self._hw_accel = "software"

            if not self._video_stream:
                raise ValueError("No video stream found")

            # Configure block analysis extraction for this codec.
            # Codec options must be set before the decoder opens.
            codec_ctx = self._video_stream.codec_context
            self._extractor = create_extractor(codec_ctx.name)
            if self._extractor:
                try:
                    options = dict(codec_ctx.options or {})
                    options.update(self._extractor.decoder_options())
                    codec_ctx.options = options
                except Exception as e:
                    print(f"Failed to enable analysis side data: {e}")
                    self._extractor = None

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
        self._extractor = None
        self._decode_pos = -1
        self._frame_cache.clear()
        self._keyframe_indices.clear()
        self._frame_seek_ts.clear()

    @staticmethod
    def _build_seek_ts(frames: list[FrameInfo]) -> dict[int, int]:
        return {
            f.index: (f.dts if f.dts is not None else f.pts)
            for f in frames
            if f.dts is not None or f.pts is not None
        }

    def decode_frame(self, frame_index: int) -> Optional[np.ndarray]:
        """Decode a specific frame by index, returning RGB numpy array."""
        entry = self._get_entry(frame_index)
        return entry[0] if entry else None

    def get_analysis(self, frame_index: int) -> Optional[FrameAnalysis]:
        """Block-level analysis for a frame (decodes it if needed)."""
        entry = self._get_entry(frame_index)
        return entry[1] if entry else None

    def _get_entry(
        self, frame_index: int
    ) -> Optional[tuple[np.ndarray, Optional[FrameAnalysis]]]:
        """Decode a specific frame by index, returning (rgb, analysis).

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
            entry = self._decode_until(frame_index)
            if entry is None:
                # Retry once from a fresh seek. Handles iterators that
                # hit EOF (e.g. the target was flushed out in a batch
                # while serving an earlier frame).
                self._packet_iter = None
                self._seek_to_keyframe(frame_index)
                entry = self._decode_until(frame_index)
            if entry is None:
                # Last resort: seeking is unreliable for this container
                # (e.g. it landed past the only keyframe). Reopen and
                # decode linearly from the start of the file.
                self._reopen()
                self._decode_pos = -1
                self._packet_iter = self._container.demux(self._video_stream)
                entry = self._decode_until(frame_index)
            return entry

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

        # Perform the seek using the keyframe's DTS (backward seek lands
        # at the nearest packet with timestamp <= target)
        ts = self._frame_seek_ts.get(kf_index)
        try:
            if ts is not None:
                self._container.seek(ts, stream=self._video_stream)
            else:
                self._container.seek(0, stream=self._video_stream)
                kf_index = 0
        except Exception:
            # Unseekable input (e.g. raw Annex-B elementary stream):
            # reopen the container and decode from the start.
            self._reopen()
            kf_index = 0

        # Reset decode cursor to just before the keyframe
        self._decode_pos = kf_index - 1
        # Create a fresh demux iterator from the seek position
        self._packet_iter = self._container.demux(self._video_stream)

    def _reopen(self) -> None:
        """Reopen the container from scratch (fallback for unseekable input)."""
        try:
            self._container.close()
        except Exception:
            pass
        self._container = av.open(self._file_path)
        self._video_stream = next(
            (s for s in self._container.streams if s.type == "video"), None
        )
        if self._video_stream is None:
            raise ValueError("No video stream found on reopen")
        codec_ctx = self._video_stream.codec_context
        if self._extractor:
            codec_ctx.options = {
                **(codec_ctx.options or {}),
                **self._extractor.decoder_options(),
            }
        try:
            self._video_stream.thread_type = "AUTO"
        except Exception:
            pass

    def _decode_until(
        self, target_index: int
    ) -> Optional[tuple[np.ndarray, Optional[FrameAnalysis]]]:
        """Decode frames from current position until target_index.

        All intermediate frames are cached for potential future use.
        """
        if self._packet_iter is None:
            return None

        for packet in self._packet_iter:
            # Process the WHOLE batch before returning: a single packet
            # (especially the EOF flush packet with frame-threaded
            # decoders) can yield many frames at once, and dropping the
            # tail would lose frames that only exist in this batch.
            found = None
            for frame in packet.decode():
                self._decode_pos += 1
                rgb = frame.to_ndarray(format="rgb24")
                analysis = None
                if self._extractor:
                    try:
                        analysis = self._extractor.extract(
                            frame, self._decode_pos
                        )
                    except Exception:
                        analysis = None
                entry = (rgb, analysis)
                self._cache_put(self._decode_pos, entry)
                if self._decode_pos == target_index:
                    found = entry

            if found is not None:
                return found
            if self._decode_pos > target_index:
                # Overshot — return from cache if available
                return self._frame_cache.get(target_index)

        # Iterator exhausted (EOF reached): it must not be reused.
        self._packet_iter = None
        return self._frame_cache.get(target_index)

    def _cache_put(
        self,
        index: int,
        entry: tuple[np.ndarray, Optional[FrameAnalysis]],
    ) -> None:
        """Add a frame to the LRU cache, evicting oldest if full."""
        if index in self._frame_cache:
            self._frame_cache.move_to_end(index)
        else:
            self._frame_cache[index] = entry
            if len(self._frame_cache) > self.CACHE_MAX:
                self._frame_cache.popitem(last=False)

    def set_keyframe_index(self, frames: list[FrameInfo]) -> None:
        """Update keyframe index from a frame list (e.g. after loading)."""
        self._keyframe_indices = sorted(
            f.index for f in frames if f.is_keyframe
        )
        self._frame_seek_ts = self._build_seek_ts(frames)

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
