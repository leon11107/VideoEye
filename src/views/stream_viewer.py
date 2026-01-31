"""NALU syntax tree viewer."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from ..core.frame_info import FrameInfo, FrameType
from ..parsers.nalu_parser import NALUnit, NALUParser
from ..parsers.h264_parser import H264Parser
from ..parsers.h265_parser import H265Parser


class StreamViewer(QWidget):
    """Displays parsed NAL unit structure in a tree view."""

    nalu_selected = pyqtSignal(int, int)  # (nalu_offset, nalu_size)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_frame: FrameInfo = None
        self._nalus: list[NALUnit] = []
        self._nalu_parser: NALUParser = None
        self._h264_parser = H264Parser()
        self._h265_parser = H265Parser()
        self._is_h265 = False
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Info label
        self._info_label = QLabel("No frame selected")
        self._info_label.setStyleSheet("padding: 4px; background: #333; color: #ccc;")
        layout.addWidget(self._info_label)

        # Tree widget
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Element", "Value"])
        self._tree.setColumnWidth(0, 250)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemClicked.connect(self._on_item_clicked)

        # Style
        self._tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: none;
            }
            QTreeWidget::item:selected {
                background-color: #264f78;
            }
            QTreeWidget::item:hover {
                background-color: #2a2d2e;
            }
            QHeaderView::section {
                background-color: #333;
                color: #ccc;
                padding: 4px;
                border: none;
            }
        """)

        layout.addWidget(self._tree)

    def set_codec(self, codec_name: str, is_avc: bool = False, nal_length_size: int = 4) -> None:
        """Configure for H.264 or H.265 parsing."""
        self._is_h265 = codec_name.lower() in ('hevc', 'h265', 'h.265')
        self._nalu_parser = NALUParser(
            is_h265=self._is_h265,
            is_avc=is_avc,
            nal_length_size=nal_length_size
        )

    def set_extradata(self, extradata: bytes) -> None:
        """Parse extradata (SPS/PPS from container) to populate parameter sets."""
        if not extradata or not self._nalu_parser:
            return

        if self._is_h265:
            nalus = self._nalu_parser.parse_extradata_h265(extradata)
            for nalu in nalus:
                self._h265_parser.parse_nalu(nalu)
        else:
            nalus = self._nalu_parser.parse_extradata_h264(extradata)
            for nalu in nalus:
                self._h264_parser.parse_nalu(nalu)

    def display_frame(self, frame: FrameInfo) -> None:
        """Display NAL unit structure for a frame."""
        self._tree.clear()
        self._current_frame = frame
        self._nalus = []

        if not frame or not self._nalu_parser:
            self._info_label.setText("No frame selected")
            return

        # Parse NAL units
        self._nalus = self._nalu_parser.parse(frame.packet_data)

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

        if nalu_index is not None and 0 <= nalu_index < len(self._nalus):
            nalu = self._nalus[nalu_index]
            self.nalu_selected.emit(nalu.offset, nalu.size)

    def clear(self) -> None:
        """Clear the display."""
        self._tree.clear()
        self._current_frame = None
        self._nalus = []
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
