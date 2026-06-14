"""Block analysis panels, presented as separate tabs:

- OverlayControls: overlay toggles (QP / MV / Partition CU·PU·TU / Types).
- FrameStatsPanel: per-frame statistics.
- BlockHoverPanel: Elecard-style name|value table for the block under cursor.
"""

import numpy as np
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget
)

from ..analysis import PredType, block_type_label, qp_field_name
from .overlay import OVERLAYS, DEFAULT_ON, PARTITION_KEY, PARTITION_LAYERS

# Elecard-like section header coloring.
_SECTION_BG = QColor("#2d5a88")
_SECTION_FG = QColor("#ffffff")


class OverlayControls(QWidget):
    """Overlay enable/disable toggles."""

    overlays_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        for key, (label, _render) in OVERLAYS.items():
            cb = QCheckBox(label)
            cb.setChecked(key in DEFAULT_ON)  # before connect: no startup emit
            cb.toggled.connect(self._on_toggled)
            layout.addWidget(cb)
            self._checkboxes[key] = cb

        # Partition: a master checkbox (enabling it always draws CU) plus an
        # expand arrow that reveals the PU/TU refinement options.
        part_row = QHBoxLayout()
        part_row.setContentsMargins(0, 0, 0, 0)
        self._part_btn = QToolButton()
        self._part_btn.setCheckable(True)
        self._part_btn.setStyleSheet("QToolButton { border: none; }")
        self._part_btn.setArrowType(Qt.ArrowType.RightArrow)
        self._part_btn.toggled.connect(self._on_partition_expand)
        part_master = QCheckBox("Partition")
        part_master.setChecked(PARTITION_KEY in DEFAULT_ON)
        part_master.toggled.connect(self._on_toggled)
        self._checkboxes[PARTITION_KEY] = part_master
        part_row.addWidget(part_master)
        part_row.addWidget(self._part_btn)
        part_row.addStretch()
        layout.addLayout(part_row)

        self._part_container = QWidget()
        part_layout = QVBoxLayout(self._part_container)
        part_layout.setContentsMargins(28, 0, 0, 0)  # indent under "Partition"
        for key, label in PARTITION_LAYERS:
            cb = QCheckBox(label)
            cb.setChecked(key in DEFAULT_ON)
            cb.toggled.connect(self._on_toggled)
            part_layout.addWidget(cb)
            self._checkboxes[key] = cb
        self._part_container.setVisible(False)  # hidden until expanded
        self._part_container.setEnabled(part_master.isChecked())
        part_master.toggled.connect(self._part_container.setEnabled)
        layout.addWidget(self._part_container)
        layout.addStretch()

    def _on_partition_expand(self, expanded: bool):
        self._part_container.setVisible(expanded)
        self._part_btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _on_toggled(self):
        self.overlays_changed.emit(self.overlay_flags())

    def overlay_flags(self) -> dict:
        return {key: cb.isChecked() for key, cb in self._checkboxes.items()}


class FrameStatsPanel(QWidget):
    """Per-frame block-analysis statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._stats_label = QLabel("No analysis data")
        self._stats_label.setWordWrap(True)
        self._stats_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._stats_label.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self._stats_label)
        layout.addStretch()

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
            bi = int(np.count_nonzero(b["pred"] == PredType.BI))
            skip = int(np.count_nonzero(b["pred"] == PredType.SKIP))
            lines.append(f"Blocks: {n}  intra {intra} / inter {inter}"
                         f" / bi {bi} / skip {skip}")
        else:
            lines.append("Partition/Types: pending patched-FFmpeg backend")

        self._stats_label.setText("\n".join(lines))

    def clear(self) -> None:
        self._stats_label.setText("No analysis data")


class BlockHoverPanel(QWidget):
    """Name|value table for the block currently under the cursor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["name", "value"])
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(12)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setStyleSheet(
            "QTreeWidget { font-family: Consolas, monospace; }"
            "QTreeWidget::item { height: 18px; }"
        )
        self._tree.header().setStretchLastSection(True)
        layout.addWidget(self._tree)
        self._set_tree_placeholder("Hover over the frame")

    def set_hover(self, info) -> None:
        """Rebuild the block-info tree from a hover dict (or None)."""
        self._tree.clear()
        if info is None:
            self._set_tree_placeholder("Hover over the frame")
            return

        codec = info.get("codec", "")
        block = info.get("block")
        qp = info.get("qp")
        mvs = info.get("mvs")
        unit = info["unit"]

        loc = self._section("Location")
        self._row(loc, "pixel", f"({info['px']}, {info['py']})")
        self._row(loc, "block", f"({info['block_x']}, {info['block_y']})")
        self._row(loc, "unit", f"{unit}x{unit}")

        cu = self._section("Coded Unit")
        if block is not None:
            self._row(cu, "type", block_type_label(codec, int(block["mode"])))
            self._row(cu, "dimensions", f"{int(block['w'])}x{int(block['h'])}")
            self._row(cu, "prediction",
                      PredType.NAMES.get(int(block["pred"]), "?"))
            if codec == "hevc":
                self._row(cu, "depth", str(int(block["depth"])))
        else:
            self._row(cu, "type", "n/a (no partition data)")

        tu = self._section("Transform Unit")
        self._row(tu, qp_field_name(codec),
                  str(qp) if qp is not None else "n/a")

        pu = self._section("Prediction Unit")
        if mvs is not None and len(mvs) > 0:
            has_l0 = bool(np.any(mvs["list"] == 0))
            has_l1 = bool(np.any(mvs["list"] == 1))
            inter = ("Pred_BI" if has_l0 and has_l1
                     else "Pred_L0" if has_l0 else "Pred_L1")
            self._row(pu, "inter type", inter)
            for mv in mvs[:6]:
                self._row(pu, f"L{int(mv['list'])} mv",
                          f"({mv['mv_x']:+.2f}, {mv['mv_y']:+.2f})")
            if len(mvs) > 6:
                self._row(pu, "...", f"+{len(mvs) - 6} more")
        else:
            self._row(pu, "inter type", "intra / none")

        self._tree.expandAll()

    def clear(self) -> None:
        self._tree.clear()
        self._set_tree_placeholder("Hover over the frame")

    # -- tree helpers -----------------------------------------------------

    def _section(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self._tree, [title, ""])
        font = item.font(0)
        font.setBold(True)
        for col in (0, 1):
            item.setBackground(col, QBrush(_SECTION_BG))
            item.setForeground(col, QBrush(_SECTION_FG))
            item.setFont(col, font)
        return item

    def _row(self, parent: QTreeWidgetItem, name: str, value: str) -> None:
        QTreeWidgetItem(parent, [name, value])

    def _set_tree_placeholder(self, text: str) -> None:
        item = QTreeWidgetItem(self._tree, [text, ""])
        item.setForeground(0, QBrush(QColor("#888888")))

    def _hover_text(self) -> str:
        """Flatten the block-info tree to text (for headless smoke tests)."""
        lines = []
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            lines.append(top.text(0))
            for j in range(top.childCount()):
                child = top.child(j)
                lines.append(f"  {child.text(0)}: {child.text(1)}")
        return "\n".join(lines)
