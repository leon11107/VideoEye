"""Block analysis panels:

- OverlayToolBar: overlay toggles as a text-button row (Boundary / Partition /
  Mode / Type / Bits / QP) for the main toolbar; groups expand sub-layers via a
  dropdown menu.
- BlockHoverPanel: Elecard-style name|value table for the block under cursor.
- OverlayPanel: wraps the hover table (the "Block Info" dock).
- FrameStatsPanel: per-frame statistics.
"""

import numpy as np
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QSize
from PyQt6.QtGui import QAction, QBrush, QColor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
)

from ..analysis import (
    PredType, block_type_label, qp_field_name,
    h264_intra_mode_name, h264_mb_type_label,
)
from .overlay import OVERLAYS, DEFAULT_ON, OVERLAY_GROUPS
from .overlay_icons import overlay_icon
from ..theme import current_theme

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


# Overlay groups whose sub-layers are mutually exclusive (radio-style): the
# Bit Size metrics are separate heatmaps that overdraw if combined, so only one
# may be active at a time.
_EXCLUSIVE_GROUPS = {"bits"}


# Order + short labels for the toolbar row, mirroring Elecard's left-to-right
# boundary / partition / mv / type / bits / extend(qp) layout.
_TOOLBAR_ORDER = (
    ("boundary", "Boundary"),
    ("partition", "Partition"),
    ("mode", "Mode"),
    ("types", "Type"),
    ("bits", "Bits"),
    ("qp", "QP"),
)


class OverlayToolBar(QWidget):
    """Overlay toggles as a row of icon chips with dropdown sub-options, for the
    main toolbar (next to FPS). Each category is a checkable icon button (the
    group master / flat overlay), tinted white over the highlight pill when on;
    groups add a dropdown menu of checkable sub-layers. Same overlays_changed /
    overlay_flags API as OverlayControls."""

    overlays_changed = pyqtSignal(dict)
    _ICON_PX = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        # key -> a checkable source: QToolButton for masters/flat overlays,
        # QAction for sub-layers. overlay_flags() reports every registered key.
        self._sources: dict[str, object] = {}
        self._chips: list[QToolButton] = []
        self._icon_btns: list[tuple] = []   # (button, key) for theme retinting
        self._setup_ui()
        self._retint_icons()
        # Equalize chip widths once polished (sizeHint needs the stylesheet's
        # padding applied), so all buttons render the same size.
        QTimer.singleShot(0, self._equalize_widths)

    def _retint_icons(self) -> None:
        """(Re)build every chip's icon for the current theme: theme text color
        when off, highlight text (white) when checked."""
        t = current_theme()
        for btn, key in self._icon_btns:
            btn.setIcon(overlay_icon(key, t.text, t.highlight_text,
                                     self._ICON_PX))

    def apply_theme(self) -> None:
        self._retint_icons()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for key, label in _TOOLBAR_ORDER:
            subs = OVERLAY_GROUPS[key][1] if key in OVERLAY_GROUPS else ()
            self._add_button(layout, key, label, subs)

    def _equalize_widths(self) -> None:
        if not self._chips:
            return
        w = max(c.sizeHint().width() for c in self._chips)
        for c in self._chips:
            c.setFixedWidth(w)

    def _add_button(self, layout, master_key: str, label: str, subs) -> None:
        btn = QToolButton()
        btn.setObjectName("overlayChip")  # themed flat pill (checked = filled)
        btn.setToolTip(label)             # name on hover (icon-only buttons)
        btn.setIconSize(QSize(self._ICON_PX, self._ICON_PX))
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setCheckable(True)
        btn.setChecked(master_key in DEFAULT_ON)  # before connect: no startup emit
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.toggled.connect(self._on_toggled)
        self._sources[master_key] = btn
        self._chips.append(btn)
        self._icon_btns.append((btn, master_key))  # icon set in _retint_icons

        if subs:
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            menu = QMenu(btn)
            menu.setObjectName("overlayMenu")  # borderless check-mark dropdown
            exclusive = master_key in _EXCLUSIVE_GROUPS
            acts = []
            for key, sub_label in subs:
                act = QAction(sub_label, menu)
                act.setCheckable(True)
                act.setChecked(key in DEFAULT_ON)
                menu.addAction(act)
                self._sources[key] = act
                acts.append(act)
            # Exclusive groups (Bit Size) act like radio buttons; unchecking the
            # active one is allowed (shows none). Others allow any combination.
            for act in acts:
                if exclusive:
                    act.toggled.connect(
                        lambda chk, a=act, g=acts: self._on_exclusive(chk, a, g))
                else:
                    act.toggled.connect(self._on_toggled)
            btn.setMenu(menu)
        layout.addWidget(btn)

    def _on_exclusive(self, checked, act, group) -> None:
        if checked:
            for other in group:
                if other is not act and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
        self._on_toggled()

    def _on_toggled(self):
        self.overlays_changed.emit(self.overlay_flags())

    def overlay_flags(self) -> dict:
        return {key: src.isChecked() for key, src in self._sources.items()}


class OverlayPanel(QWidget):
    """The live block-info table for the region under the cursor.

    The overlay toggles now live in the main toolbar (OverlayToolBar); this
    panel shows the coding details of whatever the cursor is over, updating in
    place as you hover the canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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
        aux = info.get("h264_aux")        # H.264: (intra_type, luma_mode, slice)
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

        h264_intra = info.get("h264_intra")   # (mode, block_size) at cursor | None
        iw = int(h264_intra[1]) if h264_intra is not None else 0
        title = "Macroblock" if codec in ("h264", "avc") else "Coded Unit"
        cu = self._section(title)
        if block is not None:
            if aux is not None:
                self._row(cu, "type",
                          h264_mb_type_label(aux[0], int(block["pred"]), iw))
            else:
                self._row(cu, "type", block_type_label(codec, int(block["mode"])))
            self._row(cu, "dimensions", f"{int(block['w'])}x{int(block['h'])}")
            self._row(cu, "prediction",
                      PredType.NAMES.get(int(block["pred"]), "?"))
            if codec == "hevc":
                self._row(cu, "depth", str(int(block["depth"])))
            if aux is not None:
                if h264_intra is not None and h264_intra[0] is not None:
                    it = 2 if iw == 16 else 1     # exact sub-block mode + size
                    self._row(cu, "intra mode",
                              h264_intra_mode_name(it, int(h264_intra[0])))
                    if iw and iw != int(block["w"]):
                        self._row(cu, "intra block", f"{iw}x{iw}")
                elif aux[0] in (1, 2):
                    self._row(cu, "intra mode",
                              h264_intra_mode_name(aux[0], aux[1]))
                self._row(cu, "slice id", str(aux[2]))
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
