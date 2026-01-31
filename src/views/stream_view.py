"""Stream information display panel."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QGroupBox, QScrollArea
)
from PyQt6.QtCore import Qt

from ..core.stream_info import StreamInfo


class StreamView(QWidget):
    """Displays stream metadata in a formatted panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)

        # File info group
        self._file_group = self._create_group("File Information")
        self._file_path_label = QLabel("N/A")
        self._file_path_label.setWordWrap(True)
        self._file_size_label = QLabel("N/A")
        self._container_label = QLabel("N/A")
        self._duration_label = QLabel("N/A")

        file_form = QFormLayout()
        file_form.addRow("Path:", self._file_path_label)
        file_form.addRow("Size:", self._file_size_label)
        file_form.addRow("Container:", self._container_label)
        file_form.addRow("Duration:", self._duration_label)
        self._file_group.setLayout(file_form)
        content_layout.addWidget(self._file_group)

        # Video codec group
        self._codec_group = self._create_group("Video Codec")
        self._codec_label = QLabel("N/A")
        self._profile_label = QLabel("N/A")
        self._level_label = QLabel("N/A")
        self._pix_fmt_label = QLabel("N/A")

        codec_form = QFormLayout()
        codec_form.addRow("Codec:", self._codec_label)
        codec_form.addRow("Profile:", self._profile_label)
        codec_form.addRow("Level:", self._level_label)
        codec_form.addRow("Pixel Format:", self._pix_fmt_label)
        self._codec_group.setLayout(codec_form)
        content_layout.addWidget(self._codec_group)

        # Video format group
        self._format_group = self._create_group("Video Format")
        self._resolution_label = QLabel("N/A")
        self._aspect_label = QLabel("N/A")
        self._framerate_label = QLabel("N/A")
        self._bitrate_label = QLabel("N/A")

        format_form = QFormLayout()
        format_form.addRow("Resolution:", self._resolution_label)
        format_form.addRow("Aspect Ratio:", self._aspect_label)
        format_form.addRow("Frame Rate:", self._framerate_label)
        format_form.addRow("Bitrate:", self._bitrate_label)
        self._format_group.setLayout(format_form)
        content_layout.addWidget(self._format_group)

        # Frame statistics group
        self._stats_group = self._create_group("Frame Statistics")
        self._total_frames_label = QLabel("N/A")
        self._keyframes_label = QLabel("N/A")
        self._avg_frame_size_label = QLabel("N/A")

        stats_form = QFormLayout()
        stats_form.addRow("Total Frames:", self._total_frames_label)
        stats_form.addRow("Keyframes:", self._keyframes_label)
        stats_form.addRow("Avg Frame Size:", self._avg_frame_size_label)
        self._stats_group.setLayout(stats_form)
        content_layout.addWidget(self._stats_group)

        # Color info group
        self._color_group = self._create_group("Color Information")
        self._color_range_label = QLabel("N/A")
        self._color_space_label = QLabel("N/A")

        color_form = QFormLayout()
        color_form.addRow("Color Range:", self._color_range_label)
        color_form.addRow("Color Space:", self._color_space_label)
        self._color_group.setLayout(color_form)
        content_layout.addWidget(self._color_group)

        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _create_group(self, title: str) -> QGroupBox:
        """Create a styled group box."""
        group = QGroupBox(title)
        return group

    def update_info(self, info: StreamInfo) -> None:
        """Update the display with stream information."""
        # File info
        self._file_path_label.setText(info.file_path or "N/A")
        self._file_size_label.setText(self._format_size(info.file_size))
        self._container_label.setText(info.container_format or "N/A")
        self._duration_label.setText(info.duration_str())

        # Codec info
        codec_text = info.codec_long_name or info.codec_name or "N/A"
        self._codec_label.setText(codec_text)
        self._profile_label.setText(info.profile or "N/A")
        self._level_label.setText(info.level or "N/A")
        self._pix_fmt_label.setText(info.pix_fmt or "N/A")

        # Format info
        self._resolution_label.setText(info.resolution_str())
        self._aspect_label.setText(info.aspect_ratio_str())

        if info.frame_rate:
            fps = float(info.frame_rate)
            self._framerate_label.setText(f"{fps:.3f} fps")
        elif info.avg_frame_rate:
            fps = float(info.avg_frame_rate)
            self._framerate_label.setText(f"{fps:.3f} fps (avg)")
        else:
            self._framerate_label.setText("N/A")

        self._bitrate_label.setText(info.bitrate_str())

        # Frame statistics
        self._total_frames_label.setText(str(info.total_frames))
        self._keyframes_label.setText(str(info.keyframe_count))

        if info.total_frames > 0 and info.file_size > 0:
            avg_size = info.file_size / info.total_frames
            self._avg_frame_size_label.setText(self._format_size(int(avg_size)))
        else:
            self._avg_frame_size_label.setText("N/A")

        # Color info
        self._color_range_label.setText(info.color_range or "N/A")
        self._color_space_label.setText(info.color_space or "N/A")

    def clear(self) -> None:
        """Clear all displayed information."""
        for label in [
            self._file_path_label, self._file_size_label, self._container_label,
            self._duration_label, self._codec_label, self._profile_label,
            self._level_label, self._pix_fmt_label, self._resolution_label,
            self._aspect_label, self._framerate_label, self._bitrate_label,
            self._total_frames_label, self._keyframes_label, self._avg_frame_size_label,
            self._color_range_label, self._color_space_label
        ]:
            label.setText("N/A")

    def _format_size(self, size: int) -> str:
        """Format byte size as human-readable string."""
        if size <= 0:
            return "N/A"
        if size >= 1_073_741_824:  # 1 GB
            return f"{size / 1_073_741_824:.2f} GB"
        if size >= 1_048_576:  # 1 MB
            return f"{size / 1_048_576:.2f} MB"
        if size >= 1024:
            return f"{size / 1024:.2f} KB"
        return f"{size} bytes"
