"""Orchestrates the patched-FFmpeg helper (veye_probe) and serves the
per-frame block partitions it produces.

The helper decodes the whole stream once and writes a .veblk sidecar; we
cache it in the temp dir keyed on the file's path/size/mtime so repeated
opens are instant. If the helper is missing or fails, the sidecar is simply
unavailable and the overlays fall back to their MB-grid approximation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

from .veye_sidecar import (
    VeyeFrameBlocks,
    blocks_from_frame,
    load_sidecar,
    mvs_from_frame,
    qp_grid_from_frame,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE = _REPO_ROOT / "native" / "veye_probe.exe"


def probe_available() -> bool:
    return _PROBE.exists()


class BlockSidecar:
    """Loads and serves block partitions for one open video file."""

    def __init__(self) -> None:
        self._frames: Optional[dict[int, VeyeFrameBlocks]] = None

    @property
    def available(self) -> bool:
        return bool(self._frames)

    def generate(self, video_path: str) -> bool:
        """Run veye_probe (cached) and load the sidecar. Best-effort."""
        if not _PROBE.exists():
            return False
        try:
            st = os.stat(video_path)
        except OSError:
            return False

        key = hashlib.sha1(
            f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}"
            .encode()
        ).hexdigest()[:16]
        out = Path(tempfile.gettempdir()) / f"veye_{key}.veblk"

        # Trust the cache only if it actually parses to frames; a truncated
        # or empty file (e.g. an interrupted earlier run) must be regenerated.
        frames = load_sidecar(str(out)) if out.exists() else None
        if not frames:
            try:
                subprocess.run(
                    [str(_PROBE), str(video_path), str(out)],
                    check=True, capture_output=True, timeout=600,
                )
            except Exception as e:
                print(f"veye_probe failed: {e}", file=sys.stderr)
                return False
            frames = load_sidecar(str(out))

        self._frames = frames
        return bool(self._frames)

    def blocks_for(self, frame_index: int) -> Optional[np.ndarray]:
        """BLOCK_DTYPE partitions for a frame, or None."""
        if not self._frames:
            return None
        fb = self._frames.get(frame_index)
        if fb is None:
            return None
        return blocks_from_frame(fb)

    def mvs_for(self, frame_index: int) -> Optional[np.ndarray]:
        """MV_DTYPE motion vectors for a frame, or None."""
        if not self._frames:
            return None
        fb = self._frames.get(frame_index)
        if fb is None:
            return None
        return mvs_from_frame(fb)

    def qp_grid_for(self, frame_index: int) -> Optional[np.ndarray]:
        """QP grid (int16, -1 = unknown) for a frame, or None."""
        if not self._frames:
            return None
        fb = self._frames.get(frame_index)
        if fb is None:
            return None
        return qp_grid_from_frame(fb)

    def block_unit_for(self, frame_index: int) -> Optional[int]:
        """Pixels per QP/block grid cell for a frame, or None."""
        if not self._frames:
            return None
        fb = self._frames.get(frame_index)
        if fb is None:
            return None
        return fb.block_unit
