"""Orchestrates the patched-FFmpeg helper (veye_probe) and serves the
per-frame block partitions it produces.

The helper decodes the whole stream once and appends to a .veblk sidecar; we
cache it in the temp dir keyed on the file's path/size/mtime so repeated opens
are instant. A completed cache loads synchronously. A cold cache is generated
in a background thread: the helper writes frame-by-frame in decode order, and
we incrementally parse whatever complete entries exist so analysis appears as
decoding progresses instead of blocking the UI until the whole stream is done.

Thread model: one background thread runs the helper subprocess. Parsing and
all shared state (the frame map, parse offset, status) are guarded by a single
lock; refresh() is cheap and idempotent, called both by the GUI (on frame
queries and a poll timer) and once by the worker when the helper exits.
If the helper is missing or fails, the sidecar is simply unavailable and the
overlays fall back to their MB-grid approximation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .veye_sidecar import (
    VeyeFrameBlocks,
    blocks_from_frame,
    load_sidecar,
    mvs_from_frame,
    pus_from_frame,
    qp_grid_from_frame,
    read_incremental,
    tu_chroma_from_frame,
    tu_luma_from_frame,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE = _REPO_ROOT / "native" / "veye_probe.exe"

# Hard ceiling on a single probe run, mirroring the old synchronous timeout.
_PROBE_TIMEOUT = 600

# Status values exposed via progress().
STATUS_UNAVAILABLE = "unavailable"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def probe_available() -> bool:
    return _PROBE.exists()


class BlockSidecar:
    """Loads and serves block partitions for one open video file."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: dict[int, VeyeFrameBlocks] = {}
        self._status = STATUS_UNAVAILABLE
        self._total: Optional[int] = None
        self._out_path: Optional[Path] = None
        self._parse_offset = 0
        self._header_ok = False
        self._fully_parsed = False
        self._error: Optional[str] = None
        # Background generation.
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._stopping = False
        # Cached POC -> sidecar-index map for reference resolution.
        self._poc_map: dict[int, list[int]] = {}
        self._poc_map_n = -1

    @property
    def available(self) -> bool:
        with self._lock:
            return self._status in (STATUS_RUNNING, STATUS_DONE) or bool(self._frames)

    def generate(self, video_path: str, total_frames: Optional[int] = None) -> bool:
        """Make block analysis available for a file. Best-effort, non-blocking.

        A complete cache is loaded synchronously (instant on repeat opens).
        Otherwise the helper runs in a background thread and frames stream in;
        returns True if analysis is or will be available, False if the helper
        binary is missing or the file cannot be read.
        """
        if not _PROBE.exists():
            return False
        try:
            st = os.stat(video_path)
        except OSError:
            return False

        with self._lock:
            self._total = total_frames

        # The trailing tag is the sidecar record format; bump it whenever the
        # .veblk record layout changes so stale caches are regenerated.
        key = hashlib.sha1(
            f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}|v8"
            .encode()
        ).hexdigest()[:16]
        out = Path(tempfile.gettempdir()) / f"veye_{key}.veblk"

        # Trust the cache only if it parses to a complete, non-empty set of
        # frames (a finished run patches n_frames in the header). A truncated
        # or interrupted file parses to nothing and is regenerated.
        cached = load_sidecar(str(out)) if out.exists() else None
        if cached:
            with self._lock:
                self._frames = cached
                self._status = STATUS_DONE
                self._fully_parsed = True
                self._out_path = out
            return True

        # Drop any stale/partial cache so the reader never parses leftover
        # bytes from an earlier interrupted run before the helper truncates
        # and rewrites the file.
        try:
            if out.exists():
                out.unlink()
        except OSError:
            pass

        with self._lock:
            self._out_path = out
            self._parse_offset = 0
            self._header_ok = False
            self._fully_parsed = False
            self._frames = {}
            self._status = STATUS_RUNNING
            self._stopping = False
            self._thread = threading.Thread(
                target=self._worker, args=(os.path.abspath(video_path), out),
                daemon=True,
            )
            self._thread.start()
        return True

    def _worker(self, video_path: str, out_path: Path) -> None:
        """Run the helper to completion (background thread)."""
        try:
            proc = subprocess.Popen(
                [str(_PROBE), video_path, str(out_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        except Exception as e:
            with self._lock:
                self._status = STATUS_FAILED
                self._error = str(e)
            print(f"veye_probe launch failed: {e}", file=sys.stderr)
            return

        with self._lock:
            self._proc = proc

        stderr = b""
        try:
            _, stderr = proc.communicate(timeout=_PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            with self._lock:
                self._status = STATUS_FAILED
                self._error = "probe timeout"
            return
        except Exception as e:
            with self._lock:
                self._status = STATUS_FAILED
                self._error = str(e)
            return

        # Parse any trailing frames the last poll may have missed.
        self.refresh()
        with self._lock:
            self._proc = None
            if self._stopping:
                self._status = STATUS_FAILED
                self._error = "cancelled"
            elif proc.returncode == 0:
                self._status = STATUS_DONE
                self._fully_parsed = True
            else:
                self._status = STATUS_FAILED
                self._error = (stderr or b"").decode("utf-8", "replace")[:200]
                print(f"veye_probe exited {proc.returncode}: {self._error}",
                      file=sys.stderr)

    def refresh(self) -> None:
        """Incrementally parse newly written entries. Cheap and idempotent.

        The whole parse runs under the lock. Reads are small (only the tail
        appended since the last call), so serialising them keeps the
        threading model trivial: no two parses ever race on the offset.
        """
        with self._lock:
            if self._fully_parsed or self._out_path is None:
                return
            if self._status not in (STATUS_RUNNING, STATUS_DONE):
                return
            # If the file shrank, it was truncated/replaced (e.g. the helper
            # reopened a leftover cache with "wb"); restart parsing cleanly.
            try:
                if os.path.getsize(self._out_path) < self._parse_offset:
                    self._parse_offset = 0
                    self._header_ok = False
                    self._frames = {}
            except OSError:
                pass
            new_frames, new_offset, header_ok = read_incremental(
                str(self._out_path), self._parse_offset, self._header_ok
            )
            self._frames.update(new_frames)
            self._parse_offset = new_offset
            self._header_ok = header_ok

    def progress(self) -> tuple[int, Optional[int], str]:
        """(frames ready, total expected or None, status)."""
        with self._lock:
            return len(self._frames), self._total, self._status

    def close(self) -> None:
        """Stop any running helper and release the worker thread."""
        with self._lock:
            self._stopping = True
            proc = self._proc
            thread = self._thread
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
            if thread.is_alive():
                # terminate() did not take (helper ignored it / mid-write):
                # force kill so it cannot keep writing the cache after we
                # return, then wait for the worker to unwind.
                if proc is not None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                thread.join(timeout=5)
        with self._lock:
            self._frames = {}
            self._proc = None
            self._thread = None

    def blocks_for(self, frame_index: int) -> Optional[np.ndarray]:
        """BLOCK_DTYPE partitions for a frame, or None if not ready."""
        fb = self._frame(frame_index)
        return blocks_from_frame(fb) if fb is not None else None

    def mvs_for(self, frame_index: int) -> Optional[np.ndarray]:
        """MV_DTYPE motion vectors for a frame, or None if not ready."""
        fb = self._frame(frame_index)
        return mvs_from_frame(fb) if fb is not None else None

    def pus_for(self, frame_index: int) -> Optional[np.ndarray]:
        """HEVC prediction-unit partitions for a frame, or None if not ready."""
        fb = self._frame(frame_index)
        return pus_from_frame(fb) if fb is not None else None

    def refs_for(self, sc_index: int):
        """Reference frames of a frame, as (l0_indices, l1_indices) in sidecar
        (display) order. Resolves each reference POC to the frame carrying it
        (nearest in display order when a POC repeats across GOPs). None if not
        ready or the codec carries no reference info."""
        fb = self._frame(sc_index)
        if fb is None or fb.own_poc is None:
            return None
        with self._lock:
            if self._poc_map_n != len(self._frames):  # rebuild when frames grow
                self._poc_map = {}
                for i, f in self._frames.items():
                    if f.own_poc is not None:
                        self._poc_map.setdefault(f.own_poc, []).append(i)
                self._poc_map_n = len(self._frames)
            by_poc = self._poc_map

        def resolve(pocs):
            out = []
            for p in pocs:
                cands = by_poc.get(p)
                if cands:
                    out.append(min(cands, key=lambda i: abs(i - sc_index)))
            return out

        return resolve(fb.ref_l0), resolve(fb.ref_l1)

    def tu_luma_for(self, frame_index: int) -> Optional[np.ndarray]:
        """HEVC luma transform-unit partitions, or None if not ready."""
        fb = self._frame(frame_index)
        return tu_luma_from_frame(fb) if fb is not None else None

    def tu_chroma_for(self, frame_index: int) -> Optional[np.ndarray]:
        """HEVC chroma transform-unit partitions, or None if not ready."""
        fb = self._frame(frame_index)
        return tu_chroma_from_frame(fb) if fb is not None else None

    def qp_grid_for(self, frame_index: int) -> Optional[np.ndarray]:
        """QP grid (int16, -1 = unknown) for a frame, or None if not ready."""
        fb = self._frame(frame_index)
        return qp_grid_from_frame(fb) if fb is not None else None

    def block_unit_for(self, frame_index: int) -> Optional[int]:
        """Pixels per QP/block grid cell for a frame, or None if not ready."""
        fb = self._frame(frame_index)
        return fb.block_unit if fb is not None else None

    def _frame(self, frame_index: int) -> Optional[VeyeFrameBlocks]:
        """Pick up newly decoded frames, then return one if ready."""
        self.refresh()
        with self._lock:
            return self._frames.get(frame_index)
