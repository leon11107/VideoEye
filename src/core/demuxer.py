"""FFmpeg-based video demuxer using PyAV.

Memory-efficient design: packet data is NOT stored in memory. Instead, a
dedicated reader container supports on-demand loading of individual packets
with sequential-access optimization.
"""

import av
from pathlib import Path
from fractions import Fraction
from typing import Optional, Callable

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
        # Dedicated reader for on-demand packet loading
        self._reader: Optional[av.container.InputContainer] = None
        self._reader_stream = None
        self._reader_iter = None
        self._reader_pos: int = -1  # Last frame index read by reader
        # AV1: a coded frame's bytes are a slice of its parent MP4 sample, so
        # cache the most-recently-read packet (several coded frames share one).
        self._av1_cache_idx: int = -1
        self._av1_cache_bytes: bytes = b""
        # Persistent file handle for O(1) byte-offset packet reads (raw
        # streams, where a packet's bytes are exactly file[pos:pos+size]).
        self._byte_reader = None

    def open(self, file_path: str,
             progress_cb: Optional[Callable[[str, int, int], None]] = None) -> bool:
        """Open a video file for demuxing.

        progress_cb(stage, current, total) is called periodically during the
        frame-indexing scan so the caller can drive a real progress bar.
        total is 0 when the frame count is not known up front (raw streams).
        """
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
            self._extract_frames(progress_cb)

            # Open dedicated reader for on-demand packet access
            self._reader = av.open(file_path)
            self._reader_stream = next(
                (s for s in self._reader.streams if s.type == 'video'), None
            )

            return True

        except Exception as e:
            print(f"Error opening file: {e}")
            self.close()
            return False

    def close(self) -> None:
        """Close the current file and release all resources."""
        self._reader_iter = None
        self._reader_pos = -1
        self._av1_cache_idx = -1
        self._av1_cache_bytes = b""
        if self._reader:
            try:
                self._reader.close()
            except Exception:
                pass
        self._reader = None
        self._reader_stream = None
        if self._byte_reader is not None:
            try:
                self._byte_reader.close()
            except Exception:
                pass
        self._byte_reader = None

        if self._container:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._video_stream = None
        self._stream_info = None
        self._frames = []

    # ------------------------------------------------------------------ #
    # On-demand packet data loading (lazy, memory-efficient)
    # ------------------------------------------------------------------ #

    def read_packet_data(self, frame_index: int) -> bytes:
        """Read packet data for a single frame on demand.

        Uses sequential optimization: if the last read was for the
        previous frame, continues the iterator without seeking.
        Otherwise seeks to the frame's PTS.

        Only one packet's worth of data is in memory at a time.
        """
        if not self._reader or not self._reader_stream:
            return b''
        if not 0 <= frame_index < len(self._frames):
            return b''

        frame = self._frames[frame_index]

        # AV1: the coded frame is a byte slice of its parent MP4 sample.
        if frame.parent_packet >= 0:
            return self._read_av1_frame(frame)

        # Sequential fast-path: continue from current reader position
        if (
            self._reader_iter is not None
            and frame_index == self._reader_pos + 1
        ):
            return self._reader_next(frame_index, frame)

        # Random access: seek then scan forward
        return self._reader_seek_and_read(frame_index, frame)

    def _read_av1_frame(self, frame: FrameInfo) -> bytes:
        """Return one AV1 coded frame's bytes: its slice of the parent MP4
        sample. The parent packet is cached, so the several coded frames in one
        temporal unit are read with a single packet fetch."""
        ppkt = frame.parent_packet
        if self._av1_cache_idx != ppkt:
            self._av1_cache_bytes = self._read_parent_packet(frame)
            self._av1_cache_idx = ppkt
        buf = self._av1_cache_bytes
        off = frame.packet_byte_off
        return buf[off:off + frame.size]

    def _read_parent_packet(self, frame: FrameInfo) -> bytes:
        """Fetch the bytes of an AV1 coded frame's parent MP4 sample, located by
        its DTS (every coded frame carries its parent packet's timestamps; DTS
        is monotonic per packet so it identifies the sample uniquely)."""
        use_dts = frame.dts is not None
        seek_ts = frame.dts if use_dts else frame.pts
        if seek_ts is None:
            return b""
        try:
            self._reader.seek(seek_ts, stream=self._reader_stream)
            for packet in self._reader.demux(self._reader_stream):
                if packet.size == 0:
                    continue
                ts = packet.dts if use_dts else packet.pts
                if ts == seek_ts:
                    return bytes(packet)
                if ts is not None and ts > seek_ts:
                    break
        except Exception as e:
            print(f"AV1 parent-packet read error (dts={seek_ts}): {e}")
        return b""

    def _reader_next(self, frame_index: int, frame: FrameInfo) -> bytes:
        """Read the next packet from the active iterator."""
        try:
            for packet in self._reader_iter:
                if packet.size == 0:  # flush packet
                    continue
                self._reader_pos = frame_index
                return bytes(packet)
        except Exception:
            pass
        # Iterator exhausted or error — fall back to seek
        return self._reader_seek_and_read(frame_index, frame)

    def _reader_seek_and_read(self, frame_index: int, frame: FrameInfo) -> bytes:
        """Seek near the frame and read the matching packet.

        Seeks and matches by DTS (monotonic in stream order; av_seek_frame
        is DTS-based for most demuxers). Streams without any timestamps
        (raw elementary streams) fall back to an ordinal scan.
        """
        use_dts = frame.dts is not None
        seek_ts = frame.dts if use_dts else frame.pts
        if seek_ts is None:
            # Raw elementary stream: no timestamps to seek by. The packet
            # bytes are exactly file[pos:pos+size], so read them directly in
            # O(1) instead of re-parsing the whole stream from the start.
            data = self._read_packet_at(frame)
            if data:
                return data
            return self._reader_ordinal_scan(frame_index)

        try:
            self._reader.seek(seek_ts, stream=self._reader_stream)
            self._reader_iter = self._reader.demux(self._reader_stream)

            for packet in self._reader_iter:
                if packet.size == 0:
                    continue
                ts = packet.dts if use_dts else packet.pts
                if ts == seek_ts:
                    self._reader_pos = frame_index
                    return bytes(packet)
                # Went past target — stop
                if ts is not None and ts > seek_ts:
                    break
        except Exception as e:
            print(f"Error reading packet for frame {frame_index}: {e}")

        self._reader_iter = None
        self._reader_pos = -1
        return b''

    def _read_packet_at(self, frame: FrameInfo) -> bytes:
        """Read a packet's bytes directly by byte offset (O(1)).

        For raw elementary streams a packet on disk is contiguous, so its
        bytes are exactly file[pos : pos+size] -- identical to bytes(packet).
        Returns b'' when no offset was recorded so the caller can fall back
        to an ordinal scan.
        """
        if frame.pos is None or frame.size <= 0:
            return b''
        try:
            if self._byte_reader is None:
                self._byte_reader = open(self._file_path, "rb")
            self._byte_reader.seek(frame.pos)
            data = self._byte_reader.read(frame.size)
            # A byte read does not advance the demux iterator; force the next
            # access back through this path rather than the sequential one.
            self._reader_iter = None
            self._reader_pos = -1
            return data
        except Exception:
            return b''

    def _reader_ordinal_scan(self, frame_index: int) -> bytes:
        """Reopen the reader and scan to the Nth packet (unseekable input)."""
        try:
            if self._reader:
                self._reader.close()
        except Exception:
            pass
        try:
            self._reader = av.open(self._file_path)
            self._reader_stream = next(
                (s for s in self._reader.streams if s.type == 'video'), None
            )
            self._reader_iter = self._reader.demux(self._reader_stream)
            idx = -1
            for packet in self._reader_iter:
                if packet.size == 0:
                    continue
                idx += 1
                if idx == frame_index:
                    self._reader_pos = frame_index
                    return bytes(packet)
        except Exception as e:
            print(f"Error reading packet for frame {frame_index}: {e}")

        self._reader_iter = None
        self._reader_pos = -1
        return b''

    # ------------------------------------------------------------------ #
    # Single-pass frame type classification (memory-efficient)
    # ------------------------------------------------------------------ #

    def classify_frame_types(
        self,
        classifier_fn: Callable[[bytes], FrameType],
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        poc_tracker=None,
    ) -> None:
        """Refine frame types in a single sequential pass.

        Opens a temporary container, reads each packet once, passes
        its bytes to classifier_fn, and immediately discards the data.
        Only one packet is in memory at a time.

        progress_cb(stage, current, total) is called periodically so the
        caller can drive a real progress bar; total is the exact frame count.

        poc_tracker, when given, is fed every frame's bytes in decode order to
        derive each frame's display key (FrameInfo.poc) -- used for raw streams
        that have no container timestamps. If any frame's POC cannot be
        derived the whole stream's poc is cleared, so callers fall back to
        emission order rather than trusting a partial mapping.

        AV1 is skipped entirely: its decode-order model already carries the
        authoritative frame type (KEY/INTER) and order_hint from the OBU parse,
        and this packet-indexed pass would mis-map (one packet bundles several
        coded frames).
        """
        if self.codec_name and "av1" in self.codec_name.lower():
            return
        try:
            tmp = av.open(self._file_path)
            stream = next(
                (s for s in tmp.streams if s.type == 'video'), None
            )
            if not stream:
                tmp.close()
                return

            total = len(self._frames)
            idx = 0
            poc_ok = poc_tracker is not None
            for packet in tmp.demux(stream):
                if packet.size == 0:  # flush packet
                    continue
                if idx >= len(self._frames):
                    break

                frame = self._frames[idx]
                # POC needs every frame (incl. keyframes, which reset it);
                # type refinement only needs the non-keyframes.
                need_bytes = poc_tracker is not None or not frame.is_keyframe
                if need_bytes:
                    packet_bytes = bytes(packet)
                    if not frame.is_keyframe:
                        ft = classifier_fn(packet_bytes)
                        if ft != FrameType.UNKNOWN:
                            frame.frame_type = ft
                    if poc_tracker is not None:
                        key = poc_tracker.feed(packet_bytes)
                        if key is None:
                            poc_ok = False
                        frame.poc = key
                    # packet_bytes goes out of scope immediately

                idx += 1
                if progress_cb is not None and (idx & 0x3F) == 0:
                    progress_cb("classify", idx, total)

            # Partial POC is worse than none: clear it so the decoder uses
            # emission order uniformly instead of a half-built mapping.
            if poc_tracker is not None and not poc_ok:
                for f in self._frames:
                    f.poc = None

            if progress_cb is not None:
                progress_cb("classify", total, total)
            tmp.close()
        except Exception as e:
            print(f"Error classifying frame types: {e}")

        # Authoritative refinement: the decoder knows each frame's real
        # picture type (I/P/B). Hand-parsing slice headers (esp. HEVC) is
        # unreliable, so decode the stream and assign pict_type by PTS. Frames
        # without a PTS (raw streams) keep their slice-parsed type above.
        self._refine_types_via_decode(progress_cb)

    # AVPictureType enum values (NONE=0, I=1, P=2, B=3, S=4, SI=5, SP=6, BI=7).
    # frame.pict_type may be an int or an IntEnum depending on the PyAV version.
    _PICT_TYPE_MAP = {
        1: FrameType.I, 5: FrameType.I,
        2: FrameType.P, 4: FrameType.P, 6: FrameType.P,
        3: FrameType.B, 7: FrameType.B,
    }

    def _refine_types_via_decode(self, progress_cb=None) -> None:
        """Override frame types with the decoder's authoritative pict_type.

        Hand-parsing slice headers (esp. HEVC) is unreliable, so we trust the
        decoder's pict_type. The hard part is mapping each decoded frame back to
        our FrameInfo list, because the decoder emits in display order while our
        list is in decode order:

        - Container streams: key by PTS (survives reordering, 1:1 and robust).
        - Raw streams (no PTS): the decoder still emits in display order, and our
          frames carry a continuous display key (.poc, from poc_tracker). So we
          sort our frames into display order and zip with the emission order.
          This only runs when every frame has a unique poc and the counts match;
          otherwise we leave the slice-parsed types untouched (a partial mapping
          is worse than none).
        """
        try:
            cont = av.open(self._file_path)
            stream = next((s for s in cont.streams if s.type == "video"), None)
            if stream is None:
                cont.close()
                return
            any_pts = any(f.pts is not None for f in self._frames)
            by_pts = {}
            display_types = []
            done = 0
            total = len(self._frames)
            for frame in cont.decode(stream):
                try:
                    ft = self._PICT_TYPE_MAP.get(int(frame.pict_type))
                except (TypeError, ValueError):
                    ft = None
                if any_pts:
                    if ft is not None and frame.pts is not None:
                        by_pts[frame.pts] = ft
                else:
                    display_types.append(ft)
                done += 1
                if progress_cb is not None and (done & 0x3F) == 0:
                    progress_cb("classify", done, total)
            cont.close()

            if any_pts:
                for f in self._frames:
                    if f.pts in by_pts:
                        f.frame_type = by_pts[f.pts]
                return

            # Raw stream: align by display order via .poc.
            pocs = [f.poc for f in self._frames]
            if (None not in pocs
                    and len(set(pocs)) == len(pocs)
                    and len(display_types) == len(self._frames)):
                for f, ft in zip(sorted(self._frames, key=lambda x: x.poc),
                                 display_types):
                    if ft is not None:
                        f.frame_type = ft
        except Exception as e:
            print(f"Error refining frame types via decode: {e}")

    # ------------------------------------------------------------------ #
    # Stream info & frame extraction
    # ------------------------------------------------------------------ #

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
        info.is_avc = info.container_format in ('mov', 'mp4', 'm4v', 'mkv', 'webm')
        if codec_ctx and codec_ctx.extradata:
            extradata = bytes(codec_ctx.extradata)
            if info.codec_name in ('h264', 'hevc') and len(extradata) > 0:
                if extradata[0] == 1:  # AVCDecoderConfigurationRecord
                    info.is_avc = True
                    if len(extradata) > 4:
                        info.nal_length_size = (extradata[4] & 0x03) + 1

        self._stream_info = info

    def _extract_frames(
        self,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> None:
        """Extract frame metadata without storing packet data.

        Only lightweight metadata (pts, dts, size, keyframe flag) is kept.
        Packet data is loaded on demand via read_packet_data().
        """
        if not self._container or not self._video_stream:
            return

        self._frames = []
        stream = self._video_stream
        time_base = float(stream.time_base) if stream.time_base else 1.0
        # Stream fps, used as the bitrate fallback when packets carry no
        # duration (e.g. some raw/elementary streams).
        fps = (float(stream.average_rate) if stream.average_rate
               else float(stream.base_rate) if stream.base_rate else 0.0)
        # Best-effort total for a determinate bar; 0 (unknown) for raw
        # streams where the container reports no frame count.
        total_est = stream.frames if stream.frames and stream.frames > 0 else 0

        # AV1: one MP4 sample (packet) bundles several coded frames (hidden
        # alt-refs + the shown frame + show_existing events). Build a
        # decode-order model -- one FrameInfo per coded frame -- so no-show
        # frames are first-class, selectable entries (matching Elecard).
        codec_name = ""
        try:
            codec_name = (stream.codec_context.name or "").lower()
        except Exception:
            pass
        # PyAV reports the active decoder ("libdav1d") or codec ("av1",
        # "libaom-av1"); "av1" is a substring of all of them.
        if "av1" in codec_name:
            self._extract_frames_av1(stream, time_base, fps, total_est,
                                     progress_cb)
            return

        # No seek here: the container is freshly opened (already at the
        # start), and a *failed* seek on unseekable input corrupts the
        # demuxer state so it yields almost no packets afterwards.
        frame_index = 0
        keyframe_count = 0
        max_bitrate = 0
        total_bytes = 0

        for packet in self._container.demux(stream):
            if packet.size == 0:  # flush packet
                continue

            is_key = packet.is_keyframe
            if is_key:
                keyframe_count += 1
                frame_type = FrameType.I
            else:
                frame_type = FrameType.P

            frame = FrameInfo(
                index=frame_index,
                pts=packet.pts,
                dts=packet.dts,
                pos=packet.pos if packet.pos is not None and packet.pos >= 0 else None,
                duration=packet.duration,
                size=packet.size,
                is_keyframe=is_key,
                frame_type=frame_type,
                # packet_data intentionally left empty (lazy loaded)
                time_seconds=packet.pts * time_base if packet.pts is not None else 0.0
            )

            if packet.duration and packet.duration > 0:
                frame.instant_bitrate = int(
                    (packet.size * 8) / (packet.duration * time_base))
            elif fps > 0:
                frame.instant_bitrate = int(packet.size * 8 * fps)

            self._frames.append(frame)
            total_bytes += packet.size
            max_bitrate = max(max_bitrate, frame.instant_bitrate)

            frame_index += 1
            if progress_cb is not None and (frame_index & 0x3F) == 0:
                progress_cb("index", frame_index, total_est)

        if progress_cb is not None:
            progress_cb("index", frame_index, frame_index)

        # Update stream info
        if self._stream_info:
            self._stream_info.total_frames = len(self._frames)
            self._stream_info.keyframe_count = keyframe_count
            self._stream_info.max_bit_rate = max_bitrate

            if self._stream_info.bit_rate == 0 and self._stream_info.duration_seconds > 0:
                self._stream_info.bit_rate = int((total_bytes * 8) / self._stream_info.duration_seconds)

    def _extract_frames_av1(self, stream, time_base, fps, total_est,
                            progress_cb) -> None:
        """AV1 decode-order frame model: split every MP4 sample into its coded
        frames via the OBU parser, one FrameInfo per coded frame."""
        from .av1_obu import (split_temporal_unit, assign_display_ranks,
                              KEY_FRAME, INTRA_ONLY_FRAME)

        # Pass 1: split every packet into its coded frames (decode order),
        # remembering each coded frame's parent packet metadata.
        seq = None
        packet_index = -1
        flat = []                 # (Av1CodedFrame, packet_index, pts, dts, pos, dur)
        for packet in self._container.demux(stream):
            if packet.size == 0:
                continue
            packet_index += 1
            try:
                coded, seq = split_temporal_unit(bytes(packet), seq)
            except Exception as e:
                print(f"AV1 OBU split failed at packet {packet_index}: {e}")
                coded = []
            ppos = (packet.pos if packet.pos is not None and packet.pos >= 0
                    else None)
            for cf in coded:
                flat.append((cf, packet_index, packet.pts, packet.dts, ppos,
                             packet.duration))
            if progress_cb is not None and (len(flat) & 0x3F) == 0:
                progress_cb("index", len(flat), total_est)

        # Pass 2: resolve each coded frame's display rank via DPB tracking, then
        # build the FrameInfo list (one entry per coded frame, decode order).
        assign_display_ranks([cf for cf, *_ in flat])
        keyframe_count = 0
        max_bitrate = 0
        total_bytes = 0
        for frame_index, (cf, ppkt, pts, dts, ppos, dur) in enumerate(flat):
            is_key = cf.frame_type == KEY_FRAME
            if is_key:
                keyframe_count += 1
            ftype = (FrameType.I if cf.frame_type in (KEY_FRAME, INTRA_ONLY_FRAME)
                     else FrameType.P)   # INTER / SWITCH / show_existing
            frame = FrameInfo(
                index=frame_index, pts=pts, dts=dts, pos=ppos, duration=dur,
                size=cf.size, is_keyframe=is_key, frame_type=ftype,
                time_seconds=pts * time_base if pts is not None else 0.0,
                parent_packet=ppkt, packet_byte_off=cf.byte_off,
                order_hint=cf.order_hint, show_frame=cf.show_frame,
                show_existing=cf.show_existing, display_index=cf.display_rank,
                av1_ref_l0=cf.ref_decode_l0, av1_ref_l1=cf.ref_decode_l1,
            )
            if fps > 0:
                frame.instant_bitrate = int(cf.size * 8 * fps)
            self._frames.append(frame)
            total_bytes += cf.size
            max_bitrate = max(max_bitrate, frame.instant_bitrate)

        if progress_cb is not None:
            progress_cb("index", len(self._frames), len(self._frames))

        if self._stream_info:
            self._stream_info.total_frames = len(self._frames)
            self._stream_info.keyframe_count = keyframe_count
            self._stream_info.max_bit_rate = max_bitrate
            if (self._stream_info.bit_rate == 0
                    and self._stream_info.duration_seconds > 0):
                self._stream_info.bit_rate = int(
                    (total_bytes * 8) / self._stream_info.duration_seconds)

    @property
    def stream_info(self) -> Optional[StreamInfo]:
        return self._stream_info

    @property
    def frames(self) -> list[FrameInfo]:
        return self._frames

    def get_frame(self, index: int) -> Optional[FrameInfo]:
        if 0 <= index < len(self._frames):
            return self._frames[index]
        return None

    @property
    def is_open(self) -> bool:
        return self._container is not None

    @property
    def codec_name(self) -> str:
        return self._stream_info.codec_name if self._stream_info else ""

    def get_extradata(self) -> Optional[bytes]:
        if self._video_stream and self._video_stream.codec_context:
            extradata = self._video_stream.codec_context.extradata
            return bytes(extradata) if extradata else None
        return None
