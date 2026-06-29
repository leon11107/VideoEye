"""NALU syntax tree viewer."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from ..core.frame_info import FrameInfo, FrameType
from ..parsers.av1_parser import Av1Parser
from ..theme import current_theme
from ..parsers.nalu_parser import NALUnit, NALUParser
from ..parsers.h264_parser import H264Parser
from ..parsers.h265_parser import H265Parser


class StreamViewer(QWidget):
    """Displays the bitstream structure in a tree view: NAL units for
    H.264/HEVC, OBUs for AV1."""

    nalu_selected = pyqtSignal(int, int)  # (unit_offset, unit_size) -- NALU or OBU

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_frame: FrameInfo = None
        self._nalus: list[NALUnit] = []
        self._nalu_parser: NALUParser = None
        self._h264_parser = H264Parser()
        self._h265_parser = H265Parser()
        self._is_h265 = False
        self._is_av1 = False
        self._av1_parser = Av1Parser()
        self._obus: list[dict] = []   # AV1: parsed OBUs of the current frame
        # H.264/HEVC parameter sets parsed from the container extradata
        # (avcC/hvcC), shown at the top of every frame since they aren't in the
        # per-frame packets. Each entry is (nalu, parsed_syntax).
        self._param_sets: list[tuple] = []
        self._nal_codec = False
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label
        self._info_label = QLabel("No frame selected")
        layout.addWidget(self._info_label)

        # Tree widget
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Element", "Value"])
        self._tree.setAlternatingRowColors(True)
        self._tree.itemClicked.connect(self._on_item_clicked)

        # Both columns are user-resizable: drag the divider to trade width
        # between Element and Value (a horizontal scrollbar appears when the
        # columns exceed the panel). Start with practical defaults.
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionsMovable(False)
        self._tree.setColumnWidth(0, 220)
        self._tree.setColumnWidth(1, 360)

        layout.addWidget(self._tree)
        self.apply_theme()

    def apply_theme(self) -> None:
        """Theme the info strip and the NALU tree chrome."""
        t = current_theme()
        self._info_label.setStyleSheet(
            f"padding: 4px; background: {t.hx(t.panel_bg)}; color: {t.hx(t.panel_fg)};")
        self._tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {t.hx(t.base)};
                color: {t.hx(t.text)};
                border: none;
            }}
            QTreeWidget::item:selected {{ background-color: {t.hx(t.highlight)}; }}
            QTreeWidget::item:hover {{ background-color: {t.hx(t.tree_hover)}; }}
            QHeaderView::section {{
                background-color: {t.hx(t.panel_bg)};
                color: {t.hx(t.panel_fg)};
                padding: 4px;
                border: none;
            }}
        """)

    def set_codec(self, codec_name: str, is_avc: bool = False, nal_length_size: int = 4) -> None:
        """Configure for H.264 or H.265 parsing."""
        cn = codec_name.lower()
        self._is_h265 = cn in ('hevc', 'h265', 'h.265')
        self._is_av1 = 'av1' in cn       # av1 / libdav1d / libaom-av1
        # The NALU parser only understands H.264/HEVC Annex-B/AVCC. Other codecs
        # (AV1 OBUs, etc.) must not be parsed as NAL or frame-type classification
        # reads garbage (e.g. AV1 P-frames misdetected as I).
        self._nal_codec = cn in ('h264', 'avc', 'h.264', 'hevc', 'h265', 'h.265')
        self._param_sets = []
        self._nalu_parser = NALUParser(
            is_h265=self._is_h265,
            is_avc=is_avc,
            nal_length_size=nal_length_size
        ) if self._nal_codec else None

    def set_extradata(self, extradata: bytes) -> None:
        """Parse container extradata to populate parameter sets / sequence state.

        H.264/HEVC: SPS/PPS from avcC/hvcC. AV1: the av1C record's configOBUs
        carry the sequence header, so seeding it lets frame headers parse even
        when a non-keyframe is viewed before any sequence-header-bearing frame.
        """
        if not extradata:
            return

        if self._is_av1:
            # av1C = 4-byte AV1CodecConfigurationRecord then configOBUs.
            if len(extradata) > 4:
                self._av1_parser.parse(extradata[4:])
            return

        if not self._nalu_parser:
            return

        if self._is_h265:
            nalus = self._nalu_parser.parse_extradata_h265(extradata)
            parser = self._h265_parser
        else:
            nalus = self._nalu_parser.parse_extradata_h264(extradata)
            parser = self._h264_parser
        # Parse (populates parser context) and keep them for display.
        self._param_sets = [(nalu, parser.parse_nalu(nalu)) for nalu in nalus]

    def display_frame(self, frame: FrameInfo, packet_data: bytes = b"") -> None:
        """Display the frame's bitstream structure: NAL units for H.264/HEVC,
        OBUs for AV1."""
        self._tree.clear()
        self._current_frame = frame
        self._nalus = []
        self._obus = []

        if not frame:
            self._info_label.setText("No frame selected")
            return

        if self._is_av1:
            self._display_av1(frame, packet_data)
            return

        if not self._nalu_parser:
            self._info_label.setText("No frame selected")
            return

        # Parse NAL units
        self._nalus = self._nalu_parser.parse(packet_data)

        # Update info label
        slice_count = sum(1 for n in self._nalus if n.is_slice())
        multi_slice = "Multi-slice" if slice_count > 1 else ""
        self._info_label.setText(
            f"Frame {frame.index} | {frame.frame_type.value}-frame | "
            f"{frame.size:,} bytes | {len(self._nalus)} NALUs {multi_slice}"
        )

        # Create frame root item
        frame_item = QTreeWidgetItem([
            f"Frame {frame.index} ({frame.frame_type.value}-Frame)",
            f"{frame.size:,} bytes"
        ])
        frame_item.setExpanded(True)

        # Set color based on frame type
        colors = {
            FrameType.I: QColor(220, 80, 80),
            FrameType.P: QColor(80, 180, 80),
            FrameType.B: QColor(80, 120, 220),
        }
        color = colors.get(frame.frame_type, QColor(150, 150, 150))
        frame_item.setForeground(0, color)

        # Add frame metadata
        if frame.pts is not None:
            self._add_item(frame_item, "PTS", str(frame.pts))
        if frame.dts is not None:
            self._add_item(frame_item, "DTS", str(frame.dts))
        self._add_item(frame_item, "Time", f"{frame.time_seconds:.3f}s")
        self._add_item(frame_item, "Keyframe", "Yes" if frame.is_keyframe else "No")

        # Add NAL units
        for i, nalu in enumerate(self._nalus):
            nalu_item = self._create_nalu_item(i, nalu)
            frame_item.addChild(nalu_item)

        # Parameter sets (SPS/PPS/VPS) live in the avcC/hvcC extradata, not the
        # per-frame packets, so surface them at the top of the tree.
        self._add_param_sets_item()
        self._tree.addTopLevelItem(frame_item)

    def _add_param_sets_item(self) -> None:
        """Top-level node showing the extradata SPS/PPS/VPS for the stream."""
        if not self._param_sets:
            return
        ps_item = QTreeWidgetItem(
            ["Parameter Sets (from extradata)",
             f"{len(self._param_sets)} NAL units"])
        ps_item.setExpanded(True)
        ps_item.setForeground(0, QColor(78, 201, 176))    # teal
        for nalu, syntax in self._param_sets:
            child = QTreeWidgetItem(
                [syntax.get("_name", nalu.type_name), f"{len(nalu.data):,} bytes"])
            child.setForeground(0, QColor(78, 201, 176))
            # nal header fields, then the parsed syntax tree.
            if self._is_h265:
                self._add_item(child, "nal_unit_type", str(nalu.nal_unit_type))
            else:
                self._add_item(child, "nal_ref_idc", str(nalu.nal_ref_idc))
                self._add_item(child, "nal_unit_type", str(nalu.nal_unit_type))
            self._add_syntax_tree(child, syntax)
            ps_item.addChild(child)
        self._tree.addTopLevelItem(ps_item)

    def _display_av1(self, frame: FrameInfo, packet_data: bytes) -> None:
        """Display the AV1 OBU syntax tree for a coded frame (AV1 has no NALUs)."""
        self._obus = self._av1_parser.parse(packet_data) if packet_data else []

        kind = "show_existing" if frame.show_existing else (
            "no-show" if frame.show_frame is False else frame.frame_type.value)
        self._info_label.setText(
            f"Frame {frame.index} | {kind} | {frame.size:,} bytes | "
            f"{len(self._obus)} OBUs"
        )

        frame_item = QTreeWidgetItem([
            f"Frame {frame.index} ({frame.frame_type.value}-Frame)",
            f"{frame.size:,} bytes"
        ])
        frame_item.setExpanded(True)
        colors = {FrameType.I: QColor(220, 80, 80), FrameType.P: QColor(80, 180, 80)}
        frame_item.setForeground(0, colors.get(frame.frame_type, QColor(150, 150, 150)))

        # AV1 frame-level metadata resolved by the demuxer/decode-order model
        # (decode + display ordering and references the raw header doesn't give).
        if frame.pts is not None:
            self._add_item(frame_item, "PTS", str(frame.pts))
        self._add_item(frame_item, "Time", f"{frame.time_seconds:.3f}s")
        self._add_item(frame_item, "decode_index", str(frame.index))
        if frame.display_index is not None:
            self._add_item(frame_item, "display_index", str(frame.display_index))
        if frame.av1_sb_size:
            self._add_item(frame_item, "superblock",
                           f"{frame.av1_sb_size}x{frame.av1_sb_size}")
        if frame.av1_ref_l0 or frame.av1_ref_l1:
            refs = ", ".join(str(i) for i in
                             (frame.av1_ref_l0 or []) + (frame.av1_ref_l1 or []))
            self._add_item(frame_item, "references (decode idx)", refs)

        # One node per OBU with its full parsed syntax tree; clicking any node
        # highlights the OBU's bytes in the hex viewer.
        for i, obu in enumerate(self._obus):
            label = obu["syntax"].get("_name", obu["name"])
            obu_item = QTreeWidgetItem([
                f"OBU {i}: {label}", f"{obu['size']:,} bytes"
            ])
            obu_item.setData(0, Qt.ItemDataRole.UserRole, i)
            obu_item.setExpanded(True)
            t = obu["type"]
            if t == 1:                                   # sequence header
                obu_item.setForeground(0, QColor(78, 201, 176))
            elif t in (3, 6, 7):                         # frame header / frame
                obu_item.setForeground(0, QColor(220, 80, 80) if frame.is_keyframe
                                       else QColor(156, 220, 254))
            self._add_syntax_tree(obu_item, obu["syntax"])
            frame_item.addChild(obu_item)

        self._tree.addTopLevelItem(frame_item)

    def _create_nalu_item(self, index: int, nalu: NALUnit) -> QTreeWidgetItem:
        """Create tree item for a NAL unit."""
        item = QTreeWidgetItem([
            f"NALU {index}: {nalu.type_name}",
            f"{len(nalu.data):,} bytes"
        ])

        # Store NALU reference for selection
        item.setData(0, Qt.ItemDataRole.UserRole, index)

        # Color code by type
        if nalu.is_parameter_set():
            item.setForeground(0, QColor(78, 201, 176))  # Teal for SPS/PPS/VPS
        elif nalu.is_idr():
            item.setForeground(0, QColor(220, 80, 80))  # Red for IDR
        elif nalu.is_slice():
            item.setForeground(0, QColor(156, 220, 254))  # Light blue for slices

        # Add header info
        if self._is_h265:
            self._add_item(item, "nal_unit_type", str(nalu.nal_unit_type))
            self._add_item(item, "nuh_layer_id", str(nalu.nuh_layer_id))
            self._add_item(item, "nuh_temporal_id_plus1", str(nalu.nuh_temporal_id_plus1))
        else:
            self._add_item(item, "nal_ref_idc", str(nalu.nal_ref_idc))
            self._add_item(item, "nal_unit_type", str(nalu.nal_unit_type))

        # Parse and add syntax elements
        if self._is_h265:
            syntax = self._h265_parser.parse_nalu(nalu)
        else:
            syntax = self._h264_parser.parse_nalu(nalu)

        self._add_syntax_tree(item, syntax)

        return item

    def _add_syntax_tree(self, parent: QTreeWidgetItem, syntax: dict) -> None:
        """Add parsed syntax elements to tree."""
        for key, value in syntax.items():
            if key.startswith('_'):
                # Special keys
                if key == '_name':
                    continue  # Skip name, already in header
                elif key == '_parse_error':
                    error_item = self._add_item(parent, "Parse Error", str(value))
                    error_item.setForeground(0, QColor(255, 100, 100))
                    error_item.setForeground(1, QColor(255, 100, 100))
                elif key.startswith('_calculated'):
                    calc_item = self._add_item(parent, key[1:], str(value))
                    calc_item.setForeground(0, QColor(150, 150, 150))
                    calc_item.setForeground(1, QColor(150, 150, 150))
            elif isinstance(value, dict):
                # Nested structure
                child_item = QTreeWidgetItem([key, ""])
                parent.addChild(child_item)
                self._add_syntax_tree(child_item, value)
            else:
                self._add_item(parent, key, str(value))

    def _add_item(self, parent: QTreeWidgetItem, name: str, value: str) -> QTreeWidgetItem:
        """Add a name-value item to the tree."""
        item = QTreeWidgetItem([name, value])
        parent.addChild(item)
        return item

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle item click to emit NALU selection."""
        # Find NALU index
        nalu_index = item.data(0, Qt.ItemDataRole.UserRole)

        # Check parent if this item doesn't have NALU index
        if nalu_index is None:
            parent = item.parent()
            while parent:
                nalu_index = parent.data(0, Qt.ItemDataRole.UserRole)
                if nalu_index is not None:
                    break
                parent = parent.parent()

        if nalu_index is None:
            return
        if self._is_av1:
            if 0 <= nalu_index < len(self._obus):
                obu = self._obus[nalu_index]
                self.nalu_selected.emit(obu["offset"], obu["size"])
        elif 0 <= nalu_index < len(self._nalus):
            nalu = self._nalus[nalu_index]
            self.nalu_selected.emit(nalu.offset, nalu.size)

    def clear(self) -> None:
        """Clear the display."""
        self._tree.clear()
        self._current_frame = None
        self._nalus = []
        self._obus = []
        self._param_sets = []
        self._info_label.setText("No frame selected")

    def get_frame_type_from_nalus(self, packet_data: bytes) -> FrameType:
        """Determine frame type from NAL units."""
        if not self._nalu_parser:
            return FrameType.UNKNOWN

        nalus = self._nalu_parser.parse(packet_data)

        for nalu in nalus:
            if nalu.is_idr():
                return FrameType.I

            if nalu.is_slice():
                if self._is_h265:
                    slice_type = self._h265_parser.get_slice_type(nalu)
                else:
                    slice_type = self._h264_parser.get_slice_type(nalu)

                if slice_type == "I":
                    return FrameType.I
                elif slice_type == "P":
                    return FrameType.P
                elif slice_type == "B":
                    return FrameType.B

        return FrameType.UNKNOWN
