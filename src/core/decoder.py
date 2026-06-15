"""Video frame decoder using PyAV with smart seeking and caching."""

import bisect
from collections import OrderedDict
from typing import Optional

import av
import numpy as np

from .frame_info import FrameInfo
from ..analysis import FrameAnalysis, create_extractor
from ..analysis.block_sidecar import BlockSidecar

# PyAV format names for raw elementary streams (no container index, repeated
# parameter sets at each IDR) that support byte-offset random seek.
_RAW_FORMATS = frozenset(
    {"h264", "hevc", "av1", "obu", "ivf", "m4v", "mpegvideo", "vc1", "h261", "h263"}
)


class _SlicedFile:
    """Read-only file view that starts at a byte offset and reports it as 0.

    Feeding this to ``av.open`` makes a raw elementary stream appear to begin
    at a chosen keyframe: FFmpeg's probing and seeking stay within the slice
    instead of roaming the whole file, so the packet sequence is exactly the
    keyframe's GOP onward. Reaching frame 0 of the slice = the keyframe's
    first byte, which is the only well-defined entry point for a clean decode.
    """

    def __init__(self, path: str, start: int):
        self._f = open(path, "rb")
        self._start = start
        self._f.seek(0, 2)
        self._end = self._f.tell()
        self._f.seek(start)

    def read(self, n: int = -1) -> bytes:
        return self._f.read(n)

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            pos = self._start + offset
        elif whence == 1:
            pos = self._f.tell() + offset
        elif whence == 2:
            pos = self._end + offset
        else:
            pos = self._f.tell()
        pos = max(self._start, min(pos, self._end))
        self._f.seek(pos, 0)
        return self._f.tell() - self._start

    def tell(self) -> int:
        return self._f.tell() - self._start

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


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

    # LRU cache capacity (number of decoded RGB frames)
    # 16 frames @ 1080p ≈ 96 MB; keeps memory bounded while covering
    # a typical GOP for smooth back/forward navigation.
    CACHE_MAX = 16

    def __init__(self):
        self._container: Optional[av.container.InputContainer] = None
        self._video_stream: Optional[av.video.stream.VideoStream] = None
        self._file_path: str = ""
        # File handle backing a byte-offset open (None when opened by path).
        # Owned by us and closed alongside the container.
        self._fileobj = None
        # Frame indexing is decode/bitstream order throughout (same as the
        # demuxer's frame list and the bar chart). The decoder, however,
        # *emits* frames in presentation order, so each emitted frame is
        # mapped back to its decode-order index via its pts.
        #   _decode_pos : decode index of the last packet fed to the decoder
        #                 (the forward frontier; monotonic within a run)
        #   _run_kf     : keyframe (decode index) the current run started at
        #   _emit_next  : decode index to assign to the next emitted frame
        #                 when it carries no pts (raw streams: emission order
        #                 equals decode order for non-reordered content)
        self._decode_pos: int = -1
        self._run_kf: int = -1
        self._emit_next: int = 0
        # pts -> decode-order index, and decode-order index -> presentation
        # rank. Built at open from the demuxer frame list.
        self._pts_to_index: dict[int, int] = {}
        self._index_to_display: dict[int, int] = {}
        self._display_to_index: Optional[dict[int, int]] = None  # lazy inverse
        # Raw streams have no pts: _emit_order lists decode indices in the
        # decoder's output (display) order, derived from POC. _emit_ptr walks
        # it as frames are emitted. None when POC is unavailable (then
        # emission order is assumed to equal decode order).
        self._emit_order: Optional[list[int]] = None
        self._emit_ptr: int = 0
        # Sequential (display-order) playback state: a buffer of frames already
        # pulled from a packet, and the display rank to start emitting from.
        self._seq_buffer: list = []
        self._seq_skip_until_display: int = 0
        # Active demux iterator (kept alive for sequential access)
        self._packet_iter = None
        # LRU cache: frame_index -> (rgb array, FrameAnalysis or None)
        self._frame_cache: OrderedDict[
            int, tuple[np.ndarray, Optional[FrameAnalysis]]
        ] = OrderedDict()
        # Per-codec block analysis extractor (None if unsupported codec)
        self._extractor = None
        # Patched-FFmpeg block-partition sidecar (None if helper unavailable)
        self._block_sidecar: Optional[BlockSidecar] = None
        # Keyframe index (decode order) for nearest-keyframe lookup, plus each
        # keyframe's byte offset for O(1) random seek. Built by parsing the
        # stream at open; never relies on container timestamps.
        self._keyframe_indices: list[int] = []
        self._keyframe_pos: dict[int, int] = {}
        # Raw elementary stream (Annex-B / OBU)? These have no container index
        # and cannot be seeked, so we reach keyframes by byte offset instead
        # of re-parsing from the start on every jump.
        self._raw_stream: bool = False
        # Demuxer format name, used as the format hint when reopening a raw
        # stream at a byte offset (probing a mid-stream slice is ambiguous).
        self._format_name: str = ""
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

            # Build keyframe index (+ byte offsets) from demuxer frame list
            if frames:
                self._build_keyframe_index(frames)

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

            # Raw elementary streams repeat parameter sets at each IDR, so we
            # can open mid-file by byte offset; seekable containers must not be
            # opened that way (FFmpeg would re-probe from the start).
            self._format_name = (self._container.format.name or "").lower()
            self._raw_stream = self._format_name in _RAW_FORMATS

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

            # Block-partition sidecar from patched FFmpeg (best-effort).
            # Decodes the whole stream once at open; cached on disk so
            # repeat opens are instant. Falls back silently if the helper
            # binary is missing or the codec is unsupported.
            try:
                sidecar = BlockSidecar()
                total = len(frames) if frames else None
                if sidecar.generate(file_path, total_frames=total):
                    self._block_sidecar = sidecar
            except Exception as e:
                print(f"Block sidecar unavailable: {e}")

            return True

        except Exception as e:
            print(f"Error opening file for decoding: {e}")
            self.close()
            return False

    def close(self) -> None:
        """Close the decoder and release resources."""
        self._packet_iter = None
        self._close_container()
        self._extractor = None
        # Stop the background block-analysis helper and join its thread so a
        # closed file never leaves an orphaned probe process running.
        if self._block_sidecar is not None:
            try:
                self._block_sidecar.close()
            except Exception:
                pass
        self._block_sidecar = None
        self._decode_pos = -1
        self._frame_cache.clear()
        self._keyframe_indices.clear()
        self._keyframe_pos.clear()
        self._pts_to_index.clear()
        self._index_to_display.clear()
        self._display_to_index = None
        self._emit_order = None
        self._emit_ptr = 0
        self._seq_buffer = []
        self._seq_skip_until_display = 0
        self._decode_pos = -1
        self._run_kf = -1
        self._emit_next = 0
        self._raw_stream = False
        self._format_name = ""

    def _close_container(self) -> None:
        """Close the active container and any byte-offset file handle."""
        if self._container:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._video_stream = None
        if self._fileobj is not None:
            try:
                self._fileobj.close()
            except Exception:
                pass
        self._fileobj = None

    def decode_frame(self, frame_index: int) -> Optional[np.ndarray]:
        """Decode a specific frame by index, returning RGB numpy array."""
        entry = self._get_entry(frame_index)
        return entry[0] if entry else None

    def get_analysis(self, frame_index: int) -> Optional[FrameAnalysis]:
        """Block-level analysis for a frame (decodes it if needed)."""
        entry = self._get_entry(frame_index)
        if not entry:
            return None
        analysis = entry[1]
        # Fill block-level fields from the sidecar on first access, only
        # where the extractor left them unset (so mainline H.264 mv/qp are
        # not overwritten). The analysis object is shared with the cache, so
        # this populates the cached entry too (no re-fetch on later reads).
        if analysis is not None and self._block_sidecar is not None:
            # The sidecar (patched-FFmpeg probe) keys frames in presentation
            # order; map the decode-order index to that rank so QP/MV overlay
            # data lines up with the decoded picture.
            sc_index = self._index_to_display.get(frame_index, frame_index)
            if analysis.blocks is None:
                analysis.blocks = self._block_sidecar.blocks_for(sc_index)
            if analysis.pu is None:
                analysis.pu = self._block_sidecar.pus_for(sc_index)
            if analysis.tu_luma is None:
                analysis.tu_luma = self._block_sidecar.tu_luma_for(sc_index)
            if analysis.tu_chroma is None:
                analysis.tu_chroma = self._block_sidecar.tu_chroma_for(sc_index)
            if analysis.slice_lines is None:
                analysis.slice_lines = self._block_sidecar.slice_lines_for(sc_index)
            if analysis.tile_lines is None:
                analysis.tile_lines = self._block_sidecar.tile_lines_for(sc_index)
            if analysis.mvs is None:
                analysis.mvs = self._block_sidecar.mvs_for(sc_index)
            if analysis.qp_grid is None:
                grid = self._block_sidecar.qp_grid_for(sc_index)
                if grid is not None:
                    analysis.qp_grid = grid
                    unit = self._block_sidecar.block_unit_for(sc_index)
                    if unit:
                        analysis.qp_unit = unit
        return analysis

    def refs_for(self, frame_index: int):
        """Reference frames of frame_index (decode order), as
        (l0_indices, l1_indices) in decode order, or None if unavailable."""
        if self._block_sidecar is None:
            return None
        sc_index = self._index_to_display.get(frame_index, frame_index)
        r = self._block_sidecar.refs_for(sc_index)
        if r is None:
            return None
        # sidecar indices are display order; map back to decode order.
        if not self._index_to_display:
            return list(r[0]), list(r[1])
        if self._display_to_index is None:
            self._display_to_index = {d: i for i, d in self._index_to_display.items()}
        inv = self._display_to_index
        conv = lambda lst: [inv.get(d, d) for d in lst]
        return conv(r[0]), conv(r[1])

    def analysis_progress(self) -> tuple[int, Optional[int], str]:
        """Block-analysis generation progress: (ready, total, status).

        status is one of 'unavailable', 'running', 'done', 'failed'.
        """
        if self._block_sidecar is None:
            return (0, None, "unavailable")
        return self._block_sidecar.progress()

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
            kf_index, _ = self._nearest_keyframe(frame_index)

            # 2. Continue the current run only if it began at this frame's
            # keyframe (same GOP) and has not yet fed this frame's packet to
            # the decoder. Otherwise seek. (Random access into an inter GOP
            # inherently costs a decode from its keyframe -- same as a
            # reference decoder; sequential playback uses decode_next instead.)
            can_continue = (
                self._packet_iter is not None
                and self._run_kf == kf_index
                and self._decode_pos < frame_index
            )
            if not can_continue:
                self._seek_to_keyframe(frame_index)

            # 3. Decode forward until the target frame is produced.
            entry = self._decode_until(frame_index)
            if entry is None:
                # Retry once from a fresh seek (iterator may have hit EOF).
                self._packet_iter = None
                self._seek_to_keyframe(frame_index)
                entry = self._decode_until(frame_index)
            if entry is None:
                # Last resort: decode linearly from the very start. Frame 0 is
                # always a correct anchor, so this yields the right frame even
                # for open-GOP keyframes whose leading pictures cannot be
                # reconstructed from a mid-stream entry point.
                self._reopen()
                self._packet_iter = self._container.demux(self._video_stream)
                self._run_kf = 0
                self._decode_pos = -1
                self._emit_next = 0
                self._emit_ptr = 0
                entry = self._decode_until(frame_index)
            return entry

        except Exception as e:
            print(f"Error decoding frame {frame_index}: {e}")
            self._packet_iter = None
            return None

    def _seek_to_keyframe(self, target_index: int) -> None:
        """Position the decoder at the nearest keyframe at or before target.

        Never trusts container timestamps. The keyframe index (and each
        keyframe's byte offset) is built by parsing the whole stream at open.
        We reach the keyframe by one of two means, both giving a clean
        decoder so no stale reorder-buffer frames leak across the jump:

        - Byte-offset seek (raw elementary streams): jump the file straight to
          the keyframe's bytes — O(1), no re-parsing. Valid because raw
          streams repeat parameter sets at each IDR.
        - Parse-skip (containers, or when byte-seek is unavailable): reopen and
          demux past the preceding packets without decoding them.
        """
        kf_index, kf_pos = self._nearest_keyframe(target_index)

        # Fast path: byte-offset seek straight to the keyframe (raw streams).
        if self._raw_stream and kf_pos is not None and self._open_at_offset(kf_pos):
            self._packet_iter = self._container.demux(self._video_stream)
        else:
            # Fallback: reopen for a fresh decoder, parse-skip to the keyframe.
            self._reopen()
            self._packet_iter = self._container.demux(self._video_stream)
            self._skip_packets_to(kf_index)

        # The next packet fed to the decoder is the keyframe (decode index
        # kf_index); the run's first emitted frame is the keyframe, which sits
        # at its display rank in the POC-derived emission order.
        self._run_kf = kf_index
        self._decode_pos = kf_index - 1
        self._emit_next = kf_index
        self._emit_ptr = self._index_to_display.get(kf_index, kf_index)

    def _nearest_keyframe(self, target_index: int) -> tuple[int, Optional[int]]:
        """Decode-order index and byte offset of the nearest keyframe <= target.

        Returns (0, None) when no keyframe index is available, so the caller
        falls back to parsing from the very start (always a correct anchor).
        """
        if not self._keyframe_indices:
            return 0, None
        pos = bisect.bisect_right(self._keyframe_indices, target_index) - 1
        kf_index = self._keyframe_indices[max(0, pos)]
        return kf_index, self._keyframe_pos.get(kf_index)

    def _open_at_offset(self, byte_pos: int) -> bool:
        """Open the stream positioned at a byte offset for O(1) random seek.

        Feeds PyAV a sliced view that begins at the keyframe's bytes, so the
        demuxer reads parameter sets and the IDR slice directly from there.
        Only used for raw elementary streams. Returns False on any error so
        the caller can fall back to parse-skip.

        Note: for open-GOP keyframes (HEVC CRA) the slice cannot reconstruct
        the leading pictures, so the forward decode simply will not reach such
        a target; the decode-from-start safety net in _get_entry then produces
        the correct frame. So this stays correct, just not always O(1).
        """
        try:
            self._close_container()
            sliced = _SlicedFile(self._file_path, byte_pos)
            self._fileobj = sliced
            if self._format_name:
                self._container = av.open(sliced, format=self._format_name)
            else:
                self._container = av.open(sliced)
            self._configure_stream()
            return self._video_stream is not None
        except Exception:
            self._close_container()
            return False

    def _skip_packets_to(self, kf_index: int) -> None:
        """Advance the demux iterator past the first kf_index packets without
        decoding, so the next packet read is the keyframe.

        Counts non-flush packets in demux order, matching the demuxer's frame
        indexing. Reading packets only parses the bitstream (cheap); it never
        decodes, so skipping thousands of frames costs no decode work.
        """
        if kf_index <= 0 or self._packet_iter is None:
            return
        count = 0
        for packet in self._packet_iter:
            if packet.size == 0:  # flush packet — not a coded frame
                continue
            count += 1
            if count >= kf_index:
                return

    def _reopen(self) -> None:
        """Reopen the container from the start (parse-skip seek path)."""
        self._close_container()
        self._container = av.open(self._file_path)
        self._configure_stream()

    def _configure_stream(self) -> None:
        """Bind the video stream and re-apply decoder options + threading.

        Shared by the parse-skip reopen and the byte-offset open so both
        produce an identically configured decoder (analysis side data on,
        multi-threaded).
        """
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
        """Decode forward until the frame at decode index target_index appears.

        Each emitted frame is mapped to its decode-order index (via pts, or
        emission order for raw streams) and cached, so intermediate frames are
        reusable. _decode_pos tracks the decode frontier (last packet fed in).
        """
        if self._packet_iter is None:
            return None

        for packet in self._packet_iter:
            if packet.size:  # not the flush packet
                self._decode_pos += 1
            # Process the WHOLE batch: one packet (especially the EOF flush
            # with frame-threaded decoders) can yield many frames at once.
            # Capture the target within the batch so a large batch cannot
            # evict it from the LRU cache before we return it.
            found = None
            for frame in packet.decode():
                index, entry = self._emit(frame)
                if index == target_index:
                    found = entry

            if found is not None:
                return found

        # Iterator exhausted (EOF reached): it must not be reused.
        self._packet_iter = None
        return self._frame_cache.get(target_index)

    def _emit(self, frame) -> tuple[int, tuple]:
        """Label, convert and cache an emitted frame. Returns (index, entry)."""
        index = self._label_frame(frame)
        rgb = frame.to_ndarray(format="rgb24")
        analysis = None
        if self._extractor:
            try:
                analysis = self._extractor.extract(frame, index)
            except Exception:
                analysis = None
        entry = (rgb, analysis)
        self._cache_put(index, entry)
        return index, entry

    def begin_sequential(self, start_index: int) -> None:
        """Prepare display-order sequential decoding starting at start_index.

        Playback advances in display (presentation) order -- the decoder's
        natural output order -- so each step is one emission (O(1)), unlike
        random access by decode index which must decode from a keyframe.
        Seeks to the start frame's GOP keyframe; decode_next() then discards
        emissions before the start's display position and returns the rest.
        """
        self._seq_buffer = []
        self._seq_skip_until_display = self._index_to_display.get(start_index, start_index)
        self._seek_to_keyframe(start_index)

    def decode_next(self) -> Optional[tuple[int, np.ndarray, Optional[FrameAnalysis]]]:
        """Next frame in display order: (decode_index, rgb, analysis) or None.

        Pulls packets on demand and buffers a packet's batch across calls.
        Frames whose display position precedes the sequential start are
        skipped (they belong to the GOP head before the start frame).
        """
        while True:
            while self._seq_buffer:
                index, entry = self._seq_buffer.pop(0)
                if self._index_to_display.get(index, index) < self._seq_skip_until_display:
                    continue
                return index, entry[0], entry[1]
            if self._packet_iter is None:
                return None
            # Pull the next packet that yields at least one frame.
            for packet in self._packet_iter:
                if packet.size:
                    self._decode_pos += 1
                for frame in packet.decode():
                    self._seq_buffer.append(self._emit(frame))
                if self._seq_buffer:
                    break
            else:
                self._packet_iter = None
                return None

    def _label_frame(self, frame) -> int:
        """Decode-order index for an emitted (presentation-order) frame.

        Three mappings, in order of reliability:
        - pts -> decode index (containers);
        - POC-derived emission order (raw streams with a full POC mapping);
        - sequential from the run's keyframe (raw without POC: emission order
          is assumed to equal decode order, correct for non-reordered content).
        """
        if frame.pts is not None:
            mapped = self._pts_to_index.get(frame.pts)
            if mapped is not None:
                return mapped
        if self._emit_order is not None and 0 <= self._emit_ptr < len(self._emit_order):
            index = self._emit_order[self._emit_ptr]
            self._emit_ptr += 1
            return index
        index = self._emit_next
        self._emit_next += 1
        return index

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
        self._build_keyframe_index(frames)

    def _build_keyframe_index(self, frames: list[FrameInfo]) -> None:
        """Build the keyframe index, byte-offset map, and order maps.

        A keyframe contributes a byte offset only when one was recorded
        (raw streams); without it the seek falls back to parse-skip.

        The order maps translate between the decoder's presentation-order
        output and the canonical decode order: pts -> decode index links an
        emitted frame to its bar-chart frame, and decode index -> display
        rank lets the (presentation-order) block sidecar be queried for a
        decode-order frame. Both are empty for raw streams with no pts, where
        emission order already equals decode order.
        """
        self._keyframe_indices = sorted(f.index for f in frames if f.is_keyframe)
        self._keyframe_pos = {
            f.index: f.pos
            for f in frames
            if f.is_keyframe and f.pos is not None
        }
        self._pts_to_index = {
            f.pts: f.index for f in frames if f.pts is not None
        }
        # Presentation rank = position when frames are ordered by their display
        # key: pts for containers, POC for raw streams (every frame must carry
        # one, else we leave the maps empty and fall back to emission order).
        if self._pts_to_index:
            ordered = sorted(
                ((f.pts, f.index) for f in frames if f.pts is not None)
            )
            self._emit_order = None
        elif frames and all(f.poc is not None for f in frames):
            ordered = sorted((f.poc, f.index) for f in frames)
            self._emit_order = [idx for _key, idx in ordered]
        else:
            ordered = []
            self._emit_order = None
        self._index_to_display = {
            idx: rank for rank, (_key, idx) in enumerate(ordered)
        }
        self._display_to_index = None  # invalidate lazy inverse

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
