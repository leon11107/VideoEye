"""Block analysis panels:

- OverlayControls: overlay toggles (QP / MV / Partition CU·PU·TU / Types).
- BlockHoverPanel: Elecard-style name|value table for the block under cursor.
- OverlayPanel: the two above stacked in one widget (the "Overlays" tab), so
  hovering the canvas updates the block table beside the toggles.
- FrameStatsPanel: per-frame statistics.
"""

import numpy as np
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
)

from ..analysis import PredType, block_type_label, qp_field_name
from .overlay import OVERLAYS, DEFAULT_ON, OVERLAY_GROUPS

# Elecard-like section header coloring.
_SECTION_BG = QColor("#2d5a88")
_SECTION_FG = QColor("#ffffff")


def _make_kv_tree() -> QTreeWidget:
    """A name|value tree styled for the analysis panels."""
    tree = QTreeWidget()
    tree.setColumnCount(2)
    tree.setHeaderLabels(["name", "value"])
    tree.setRootIsDecorated(False)
    tree.setIndentation(12)
    tree.setAlternatingRowColors(True)
    tree.setUniformRowHeights(True)
    tree.setStyleSheet(
        "QTreeWidget { font-family: Consolas, monospace; }"
        "QTreeWidget::item { height: 18px; }"
    )
    tree.header().setStretchLastSection(True)
    return tree


def _kv_section(tree: QTreeWidget, title: str) -> QTreeWidgetItem:
    """Add a bold, colored section header row."""
    item = QTreeWidgetItem(tree, [title, ""])
    font = item.font(0)
    font.setBold(True)
    for col in (0, 1):
        item.setBackground(col, QBrush(_SECTION_BG))
        item.setForeground(col, QBrush(_SECTION_FG))
        item.setFont(col, font)
    return item


def _kv_row(parent: QTreeWidgetItem, name: str, value: str) -> None:
    QTreeWidgetItem(parent, [name, value])


class OverlayControls(QWidget):
    """Overlay enable/disable toggles."""

    overlays_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._setup_ui()
        # Stay compact: the toggles sit above the hover panel in the shared
        # Overlays tab, so the controls should take only their natural height.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Independent flat overlays.
        for key, (label, _render) in OVERLAYS.items():
            cb = QCheckBox(label)
            cb.setChecked(key in DEFAULT_ON)  # before connect: no startup emit
            cb.toggled.connect(self._on_toggled)
            layout.addWidget(cb)
            self._checkboxes[key] = cb

        # Collapsible groups (Partition / Mode / Boundary): a master checkbox +
        # an expand arrow revealing the sub-layer options.
        for master, (label, subs, _fn) in OVERLAY_GROUPS.items():
            self._add_group(layout, master, label, subs)

    def _add_group(self, layout, master_key: str, label: str, subs) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        btn = QToolButton()
        btn.setCheckable(True)
        btn.setStyleSheet("QToolButton { border: none; }")
        btn.setArrowType(Qt.ArrowType.RightArrow)
        master_cb = QCheckBox(label)
        master_cb.setChecked(master_key in DEFAULT_ON)
        master_cb.toggled.connect(self._on_toggled)
        self._checkboxes[master_key] = master_cb
        row.addWidget(master_cb)
        row.addWidget(btn)
        row.addStretch()
        layout.addLayout(row)

        container = QWidget()
        sub_layout = QVBoxLayout(container)
        sub_layout.setContentsMargins(28, 0, 0, 0)  # indent under the master
        for key, sub_label in subs:
            cb = QCheckBox(sub_label)
            cb.setChecked(key in DEFAULT_ON)
            cb.toggled.connect(self._on_toggled)
            sub_layout.addWidget(cb)
            self._checkboxes[key] = cb
        container.setVisible(False)            # hidden until expanded
        container.setEnabled(master_cb.isChecked())
        master_cb.toggled.connect(container.setEnabled)
        btn.toggled.connect(
            lambda exp, b=btn, c=container: self._toggle_group(exp, b, c))
        layout.addWidget(container)

    def _toggle_group(self, expanded: bool, btn, container) -> None:
        container.setVisible(expanded)
        btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _on_toggled(self):
        self.overlays_changed.emit(self.overlay_flags())

    def overlay_flags(self) -> dict:
        return {key: cb.isChecked() for key, cb in self._checkboxes.items()}


class OverlayPanel(QWidget):
    """Overlay toggles with the live block-info table beneath them.

    Combining both in one tab lets the cursor's block details update in place
    next to the overlay switches: enable an overlay, then hover the canvas and
    the same panel shows that region's coding info. Forwards the controls'
    signals/API so callers treat it like the old OverlayControls plus a hover
    sink (set_hover / clear_hover)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.controls = OverlayControls()
        self.overlays_changed = self.controls.overlays_changed
        self.overlay_flags = self.controls.overlay_flags
        layout.addWidget(self.controls)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        heading = QLabel("Block Info (cursor)")
        heading.setContentsMargins(8, 4, 8, 2)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        self.hover = BlockHoverPanel()
        layout.addWidget(self.hover, 1)  # the table takes the remaining height

    def set_hover(self, info) -> None:
        self.hover.set_hover(info)

    def clear_hover(self) -> None:
        self.hover.clear()


class FrameStatsPanel(QWidget):
    """Per-frame block-analysis statistics, as a sectioned name|value table
    (same presentation as the cursor Block Info panel)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._tree = _make_kv_tree()
        layout.addWidget(self._tree)
        self._placeholder("No analysis data")

    def set_analysis(self, analysis) -> None:
        """Update per-frame statistics from a FrameAnalysis (or None)."""
        self._tree.clear()
        if analysis is None:
            self._placeholder("No analysis data for this codec")
            return

        pic = _kv_section(self._tree, "Picture")
        _kv_row(pic, "codec", str(analysis.codec))
        _kv_row(pic, "type", str(analysis.pict_type))
        _kv_row(pic, "size", f"{analysis.width}x{analysis.height}")
        _kv_row(pic, "unit", f"{analysis.qp_unit}px")

        qp = _kv_section(self._tree, "QP")
        stats = analysis.qp_stats()
        if stats:
            qp_min, qp_max, qp_avg = stats
            _kv_row(qp, "min", str(qp_min))
            _kv_row(qp, "max", str(qp_max))
            _kv_row(qp, "avg", f"{qp_avg:.1f}")
        else:
            _kv_row(qp, "value", "n/a")

        mvsec = _kv_section(self._tree, "Motion")
        m = analysis.mvs
        if m is not None and len(m) > 0:
            n_l0 = int(np.count_nonzero(m["list"] == 0))
            mag = np.hypot(m["mv_x"], m["mv_y"])
            _kv_row(mvsec, "vectors", str(len(m)))
            _kv_row(mvsec, "L0 / L1", f"{n_l0} / {len(m) - n_l0}")
            _kv_row(mvsec, "|MV| avg", f"{mag.mean():.1f}px")
            _kv_row(mvsec, "|MV| max", f"{mag.max():.1f}px")
        else:
            _kv_row(mvsec, "vectors", "none (intra / n/a)")

        blk = _kv_section(self._tree, "Blocks")
        b = analysis.blocks
        if b is not None and len(b) > 0:
            _kv_row(blk, "total", str(len(b)))
            _kv_row(blk, "intra", str(int(np.count_nonzero(b["pred"] == PredType.INTRA))))
            _kv_row(blk, "inter", str(int(np.count_nonzero(b["pred"] == PredType.INTER))))
            _kv_row(blk, "bi", str(int(np.count_nonzero(b["pred"] == PredType.BI))))
            _kv_row(blk, "skip", str(int(np.count_nonzero(b["pred"] == PredType.SKIP))))
        else:
            _kv_row(blk, "partition", "pending patched-FFmpeg backend")

        self._tree.expandAll()

    def _placeholder(self, text: str) -> None:
        item = QTreeWidgetItem(self._tree, [text, ""])
        item.setForeground(0, QBrush(QColor("#888888")))

    def clear(self) -> None:
        self._tree.clear()
        self._placeholder("No analysis data")


class BlockHoverPanel(QWidget):
    """Name|value table for the block currently under the cursor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._tree = _make_kv_tree()
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
        bits = info.get("bits")
        ctu_bits = info.get("ctu_bits")
        unit = info["unit"]

        if info.get("locked"):
            lk = QTreeWidgetItem(self._tree, ["\U0001f512 locked", "click to release"])
            lk.setForeground(0, QBrush(QColor("#2ecc40")))
            lk.setForeground(1, QBrush(QColor("#888888")))

        loc = self._section("Location")
        self._row(loc, "pixel", f"({info['px']}, {info['py']})")
        self._row(loc, "block", f"({info['block_x']}, {info['block_y']})")
        self._row(loc, "unit", f"{unit}x{unit}")

        # CTU section (Elecard block-presenter top group): the containing CTB's
        # location, slice / tile membership and total coded bit cost. HEVC only.
        ctu_org = info.get("ctu_origin")
        if ctu_org is not None:
            ctb_size = int(info.get("ctb_size") or 0)
            ctu = self._section("CTU")
            self._row(ctu, "location", f"({ctu_org[0]}, {ctu_org[1]})")
            if ctb_size:
                self._row(ctu, "dimensions", f"{ctb_size}x{ctb_size}")
            sidx = info.get("slice_idx")
            tidx = info.get("tile_idx")
            if sidx is not None:
                self._row(ctu, "slice idx", str(int(sidx)))
            if tidx is not None:
                self._row(ctu, "tile idx", str(int(tidx)))
            if ctu_bits is not None:
                size = QTreeWidgetItem(ctu, ["size", f"{int(ctu_bits['cu'])} bits"])
                _kv_row(size, "prediction", str(int(ctu_bits["pu"])))
                _kv_row(size, "transform", str(int(ctu_bits["tu"])))

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

        # Coded bit cost (Elecard-style): size (total) with prediction /
        # transform breakdown. HEVC only; absent otherwise.
        if bits is not None:
            size = QTreeWidgetItem(cu, ["size", f"{int(bits['cu'])} bits"])
            _kv_row(size, "prediction", str(int(bits["pu"])))
            _kv_row(size, "transform", str(int(bits["tu"])))

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

    # -- tree helpers (shared module-level builders) ----------------------

    def _section(self, title: str) -> QTreeWidgetItem:
        return _kv_section(self._tree, title)

    def _row(self, parent: QTreeWidgetItem, name: str, value: str) -> None:
        _kv_row(parent, name, value)

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
