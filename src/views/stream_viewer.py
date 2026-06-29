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

    nalu_selected = pyqtSignal(int, int)  # (offset, size) within the frame packet
    # (offset, size) within the container extradata (avcC/hvcC/av1C) -- the
    # parameter sets / sequence header live there, not in the per-frame packet.
    extradata_selected = pyqtSignal(int, int)

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
        # AV1 sequence header OBU(s) from the av1C extradata (the SPS analog),
        # shown top-level when a packet has no in-band sequence header.
        self._av1_seq_obus: list[dict] = []
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
        self._av1_seq_obus = []
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
            # av1C = 4-byte AV1CodecConfigurationRecord then configOBUs (the
            # sequence header). Parsing seeds the parser state and gives us the
            # sequence header to show top-level (the SPS analog).
            if len(extradata) > 4:
                obus = self._av1_parser.parse(extradata[4:])
                self._av1_seq_obus = [o for o in obus if o["type"] == 1]
            return

        if not self._nalu_parser:
            return

        if self._is_h265:
            nalus = self._nalu_parser.parse_extradata_h265(extradata)
            parser = self._h265_parser
        else:
            nalus = self._nalu_parser.parse_extradata_h264(extradata)
            parser = self._h264_parser
        # Parse (populates parser context) and keep the *parameter sets* for
        # display. hvcC may also carry SEI (e.g. x265 build info); SEI is not a
        # sequence-level parameter set but per-frame supplemental data, so it is
        # excluded here -- it only shows on frames whose packet actually carries
        # one (via the per-frame NALU path).
        self._param_sets = [(nalu, parser.parse_nalu(nalu)) for nalu in nalus
                            if nalu.is_parameter_set()]

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

        # Pure bitstream structure: the extradata parameter sets (SPS/PPS/VPS),
        # then this frame's NAL units -- all top-level. Frame timing / keyframe
        # flag isn't NAL syntax and lives in the frame panels.
        self._add_param_sets_item()
        for i, nalu in enumerate(self._nalus):
            self._tree.addTopLevelItem(self._create_nalu_item(i, nalu))

    def _add_param_sets_item(self) -> None:
        """Show the extradata SPS/PPS/VPS as top-level nodes (they aren't in the
        per-frame packets)."""
        for i, (nalu, syntax) in enumerate(self._param_sets):
            item = QTreeWidgetItem(
                [syntax.get("_name", nalu.type_name), f"{len(nalu.data):,} bytes"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("extra", i))
            item.setForeground(0, QColor(78, 201, 176))   # teal
            if self._is_h265:
                self._add_item(item, "nal_unit_type", str(nalu.nal_unit_type))
            else:
                self._add_item(item, "nal_ref_idc", str(nalu.nal_ref_idc))
                self._add_item(item, "nal_unit_type", str(nalu.nal_unit_type))
            self._add_syntax_tree(item, syntax)
            self._tree.addTopLevelItem(item)

    def _display_av1(self, frame: FrameInfo, packet_data: bytes) -> None:
        """Display the AV1 OBU syntax tree for a coded frame (AV1 has no NALUs)."""
        self._obus = self._av1_parser.parse(packet_data) if packet_data else []

        kind = "show_existing" if frame.show_existing else (
            "no-show" if frame.show_frame is False else frame.frame_type.value)
        self._info_label.setText(
            f"Frame {frame.index} | {kind} | {frame.size:,} bytes | "
            f"{len(self._obus)} OBUs"
        )

        # Pure bitstream structure: sequence header first (the SPS analog), then
        # the per-frame OBUs -- both top-level. Frame timing / decode-order /
        # reference metadata isn't header syntax and lives in the frame panels.
        inband = [(i, o) for i, o in enumerate(self._obus) if o["type"] == 1]
        if inband:
            for i, o in inband:
                self._add_av1_seq_item(o, ("pkt", i))
        else:
            for si, o in enumerate(self._av1_seq_obus):
                self._add_av1_seq_item(o, ("extra", si))

        for i, obu in enumerate(self._obus):
            if obu["type"] == 1:
                continue                                 # shown above
            item = QTreeWidgetItem(
                [obu["syntax"].get("_name", obu["name"]), f"{obu['size']:,} bytes"])
            item.setData(0, Qt.ItemDataRole.UserRole, ("pkt", i))
            item.setExpanded(True)
            if obu["type"] in (3, 6, 7):                 # frame header / frame
                item.setForeground(0, QColor(220, 80, 80) if frame.is_keyframe
                                   else QColor(156, 220, 254))
            self._add_syntax_tree(item, obu["syntax"])
            self._tree.addTopLevelItem(item)

    def _add_av1_seq_item(self, obu: dict, tag: tuple) -> None:
        """Top-level sequence_header_obu() node (AV1's SPS analog). `tag` is
        ("pkt", obu_index) for an in-band header or ("extra", seq_index) for one
        carried in the av1C extradata."""
        item = QTreeWidgetItem(
            [obu["syntax"].get("_name", obu["name"]), f"{obu['size']:,} bytes"])
        item.setForeground(0, QColor(78, 201, 176))      # teal, like SPS/PPS
        item.setExpanded(True)
        item.setData(0, Qt.ItemDataRole.UserRole, tag)
        self._add_syntax_tree(item, obu["syntax"])
        self._tree.addTopLevelItem(item)

    def _create_nalu_item(self, index: int, nalu: NALUnit) -> QTreeWidgetItem:
        """Create tree item for a NAL unit."""
        item = QTreeWidgetItem([
            f"NALU {index}: {nalu.type_name}",
            f"{len(nalu.data):,} bytes"
        ])

        # Store NALU reference for selection (packet-relative)
        item.setData(0, Qt.ItemDataRole.UserRole, ("pkt", index))

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
        """Map the clicked structure to a byte range in the hex view.

        The highlight is at top-level-structure granularity: clicking any field
        walks up to the owning NALU/OBU/parameter-set node, so the whole
        structure's bytes are highlighted (not the individual field)."""
        tag = item.data(0, Qt.ItemDataRole.UserRole)
        node = item
        while tag is None and node.parent() is not None:
            node = node.parent()
            tag = node.data(0, Qt.ItemDataRole.UserRole)
        if not tag:
            return

        source, idx = tag
        if source == "extra":
            self._emit_extradata_region(idx)
        elif self._is_av1:
            if 0 <= idx < len(self._obus):
                obu = self._obus[idx]
                self.nalu_selected.emit(obu["offset"], obu["size"])
        elif 0 <= idx < len(self._nalus):
            nalu = self._nalus[idx]
            self.nalu_selected.emit(nalu.offset, nalu.size)

    def _emit_extradata_region(self, idx: int) -> None:
        """Emit the byte range of an extradata parameter set / sequence header."""
        if self._is_av1:
            if 0 <= idx < len(self._av1_seq_obus):
                obu = self._av1_seq_obus[idx]
                # Seq OBU offsets are relative to extradata[4:] (after the
                # 4-byte av1C config record), so shift back into full extradata.
                self.extradata_selected.emit(4 + obu["offset"], obu["size"])
        elif 0 <= idx < len(self._param_sets):
            nalu = self._param_sets[idx][0]
            self.extradata_selected.emit(nalu.offset, nalu.size)

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
