"""Block Info panel: overlay toggles, per-frame stats, hovered-block details."""

import numpy as np
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget
)

from ..analysis import PredType
from .overlay import OVERLAYS


class BlockInfoView(QWidget):
    """Controls analysis overlays and shows block-level statistics."""

    overlays_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        overlay_group = QGroupBox("Overlays")
        overlay_layout = QVBoxLayout(overlay_group)
        for key, (label, _render) in OVERLAYS.items():
            cb = QCheckBox(label)
            cb.toggled.connect(self._on_toggled)
            overlay_layout.addWidget(cb)
            self._checkboxes[key] = cb
        layout.addWidget(overlay_group)

        stats_group = QGroupBox("Frame Statistics")
        stats_layout = QVBoxLayout(stats_group)
        self._stats_label = QLabel("No analysis data")
        self._stats_label.setWordWrap(True)
        self._stats_label.setStyleSheet("font-family: Consolas, monospace;")
        stats_layout.addWidget(self._stats_label)
        layout.addWidget(stats_group)

        hover_group = QGroupBox("Block at Cursor")
        hover_layout = QVBoxLayout(hover_group)
        self._hover_label = QLabel("Hover over the frame")
        self._hover_label.setWordWrap(True)
        self._hover_label.setStyleSheet("font-family: Consolas, monospace;")
        hover_layout.addWidget(self._hover_label)
        layout.addWidget(hover_group)

        layout.addStretch()

    def _on_toggled(self):
        self.overlays_changed.emit(self.overlay_flags())

    def overlay_flags(self) -> dict:
        return {key: cb.isChecked() for key, cb in self._checkboxes.items()}

    def set_analysis(self, analysis) -> None:
        """Update per-frame statistics from a FrameAnalysis (or None)."""
        if analysis is None:
            self._stats_label.setText("No analysis data for this codec")
            return

        lines = [
            f"Codec: {analysis.codec}  Type: {analysis.pict_type}",
            f"Size:  {analysis.width}x{analysis.height}"
            f"  Unit: {analysis.qp_unit}px",
        ]

        stats = analysis.qp_stats()
        if stats:
            qp_min, qp_max, qp_avg = stats
            lines.append(f"QP:    min {qp_min}  max {qp_max}  avg {qp_avg:.1f}")
        else:
            lines.append("QP:    n/a")

        if analysis.mvs is not None and len(analysis.mvs) > 0:
            m = analysis.mvs
            n_l0 = int(np.count_nonzero(m["list"] == 0))
            n_l1 = len(m) - n_l0
            mag = np.hypot(m["mv_x"], m["mv_y"])
            lines.append(f"MV:    {len(m)} (L0 {n_l0} / L1 {n_l1})")
            lines.append(f"|MV|:  avg {mag.mean():.1f}px  max {mag.max():.1f}px")
        else:
            lines.append("MV:    none (intra frame or n/a)")

        if analysis.blocks is not None and len(analysis.blocks) > 0:
            b = analysis.blocks
            n = len(b)
            intra = int(np.count_nonzero(b["pred"] == PredType.INTRA))
            inter = int(np.count_nonzero(b["pred"] == PredType.INTER))
            skip = int(np.count_nonzero(b["pred"] == PredType.SKIP))
            lines.append(f"Blocks: {n}  intra {intra} / inter {inter}"
                         f" / skip {skip}")
        else:
            lines.append("Partition/Types: pending patched-FFmpeg backend")

        self._stats_label.setText("\n".join(lines))

    def set_hover(self, info) -> None:
        """Update hovered-block details (info dict from DecodedView)."""
        if info is None:
            self._hover_label.setText("Hover over the frame")
            return

        unit = info["unit"]
        lines = [
            f"Pixel: ({info['px']}, {info['py']})",
            f"Block: ({info['block_x']}, {info['block_y']})"
            f" @ {unit}x{unit}",
            f"QP:    {info['qp'] if info['qp'] is not None else 'n/a'}",
        ]
        mvs = info.get("mvs")
        if mvs is not None and len(mvs) > 0:
            for mv in mvs[:6]:
                lines.append(
                    f"MV L{int(mv['list'])}: ({mv['mv_x']:+.2f}, {mv['mv_y']:+.2f})"
                    f"  blk {int(mv['w'])}x{int(mv['h'])}"
                    f" @({int(mv['x'])},{int(mv['y'])})"
                )
            if len(mvs) > 6:
                lines.append(f"... +{len(mvs) - 6} more")
        else:
            lines.append("MV:    none")

        self._hover_label.setText("\n".join(lines))

    def clear(self) -> None:
        self._stats_label.setText("No analysis data")
        self._hover_label.setText("Hover over the frame")
