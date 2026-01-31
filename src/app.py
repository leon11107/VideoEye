"""Main application window."""

import os
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog, QMessageBox,
    QApplication, QToolBar, QStatusBar, QProgressDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence

from .core.demuxer import Demuxer
from .core.decoder import Decoder
from .core.frame_info import FrameType
from .views.stream_view import StreamView
from .views.barchart_view import BarChartView
from .views.decoded_view import DecodedView
from .views.stream_viewer import StreamViewer
from .views.hex_viewer import HexViewer


class LoadWorker(QThread):
    """Worker thread for loading video files."""

    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, demuxer: Demuxer, file_path: str):
        super().__init__()
        self._demuxer = demuxer
        self._file_path = file_path

    def run(self):
        try:
            self.progress.emit("Opening file...")
            success = self._demuxer.open(self._file_path)
            if success:
                self.finished.emit(True, "")
            else:
                self.finished.emit(False, "Failed to open file")
        except Exception as e:
            self.finished.emit(False, str(e))


class MainWindow(QMainWindow):
    """Main application window with dockable panels."""

    def __init__(self):
        super().__init__()

        self._demuxer = Demuxer()
        self._decoder = Decoder()
        self._current_file = ""

        self._setup_ui()
        self._setup_menus()
        self._setup_toolbar()
        self._setup_connections()

        self.setWindowTitle("VideoEye - Video Analysis Tool")
        self.resize(1400, 900)

    def _setup_ui(self):
        """Set up the UI with dockable panels."""
        # Central widget - decoded frame view
        self._decoded_view = DecodedView()
        self.setCentralWidget(self._decoded_view)

        # Left dock - Stream info
        self._stream_view = StreamView()
        stream_dock = QDockWidget("Stream Info", self)
        stream_dock.setWidget(self._stream_view)
        stream_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, stream_dock)

        # Bottom dock - Bar chart
        self._barchart_view = BarChartView()
        barchart_dock = QDockWidget("Frame Chart", self)
        barchart_dock.setWidget(self._barchart_view)
        barchart_dock.setAllowedAreas(Qt.DockWidgetArea.TopDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, barchart_dock)

        # Right dock - Stream viewer (NALU tree)
        self._stream_viewer = StreamViewer()
        viewer_dock = QDockWidget("NALU Viewer", self)
        viewer_dock.setWidget(self._stream_viewer)
        viewer_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, viewer_dock)

        # Right dock - Hex viewer (tabified with NALU viewer)
        self._hex_viewer = HexViewer()
        hex_dock = QDockWidget("Hex Viewer", self)
        hex_dock.setWidget(self._hex_viewer)
        hex_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, hex_dock)
        self.tabifyDockWidget(viewer_dock, hex_dock)
        viewer_dock.raise_()  # Show NALU viewer by default

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # Store dock references for menu
        self._docks = {
            'stream': stream_dock,
            'barchart': barchart_dock,
            'viewer': viewer_dock,
            'hex': hex_dock
        }

    def _setup_menus(self):
        """Set up menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_file_dialog)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        for name, dock in self._docks.items():
            action = dock.toggleViewAction()
            view_menu.addAction(action)

        view_menu.addSeparator()

        fit_action = QAction("&Fit to Window", self)
        fit_action.setShortcut("F")
        fit_action.triggered.connect(lambda: self._decoded_view.set_fit_to_window(True))
        view_menu.addAction(fit_action)

        zoom_100_action = QAction("Zoom &100%", self)
        zoom_100_action.setShortcut("1")
        zoom_100_action.triggered.connect(self._decoded_view.zoom_100)
        view_menu.addAction(zoom_100_action)

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_action.triggered.connect(self._decoded_view.zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_action.triggered.connect(self._decoded_view.zoom_out)
        view_menu.addAction(zoom_out_action)

        # Navigate menu
        nav_menu = menubar.addMenu("&Navigate")

        prev_frame_action = QAction("&Previous Frame", self)
        prev_frame_action.setShortcut("Left")
        prev_frame_action.triggered.connect(self._prev_frame)
        nav_menu.addAction(prev_frame_action)

        next_frame_action = QAction("&Next Frame", self)
        next_frame_action.setShortcut("Right")
        next_frame_action.triggered.connect(self._next_frame)
        nav_menu.addAction(next_frame_action)

        nav_menu.addSeparator()

        prev_key_action = QAction("Previous &Keyframe", self)
        prev_key_action.setShortcut("Shift+Left")
        prev_key_action.triggered.connect(self._prev_keyframe)
        nav_menu.addAction(prev_key_action)

        next_key_action = QAction("Next K&eyframe", self)
        next_key_action.setShortcut("Shift+Right")
        next_key_action.triggered.connect(self._next_keyframe)
        nav_menu.addAction(next_key_action)

        nav_menu.addSeparator()

        first_action = QAction("&First Frame", self)
        first_action.setShortcut("Home")
        first_action.triggered.connect(lambda: self._select_frame(0))
        nav_menu.addAction(first_action)

        last_action = QAction("&Last Frame", self)
        last_action.setShortcut("End")
        last_action.triggered.connect(lambda: self._select_frame(len(self._demuxer.frames) - 1))
        nav_menu.addAction(last_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_toolbar(self):
        """Set up toolbar."""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = QAction("Open", self)
        open_action.triggered.connect(self._open_file_dialog)
        toolbar.addAction(open_action)

        toolbar.addSeparator()

        prev_action = QAction("◀ Prev", self)
        prev_action.triggered.connect(self._prev_frame)
        toolbar.addAction(prev_action)

        next_action = QAction("Next ▶", self)
        next_action.triggered.connect(self._next_frame)
        toolbar.addAction(next_action)

        toolbar.addSeparator()

        prev_key_action = QAction("◀◀ Prev Key", self)
        prev_key_action.triggered.connect(self._prev_keyframe)
        toolbar.addAction(prev_key_action)

        next_key_action = QAction("Next Key ▶▶", self)
        next_key_action.triggered.connect(self._next_keyframe)
        toolbar.addAction(next_key_action)

    def _setup_connections(self):
        """Set up signal connections between views."""
        # Bar chart selection -> decode and display
        self._barchart_view.frame_selected.connect(self._on_frame_selected)

        # Stream viewer NALU selection -> hex viewer highlight
        self._stream_viewer.nalu_selected.connect(self._on_nalu_selected)

    def _open_file_dialog(self):
        """Open file dialog to select video."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video File",
            "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.m4v *.ts *.m2ts *.264 *.265 *.h264 *.h265 *.hevc);;All Files (*)"
        )
        if file_path:
            self._load_file(file_path)

    def _load_file(self, file_path: str):
        """Load a video file."""
        self._status_bar.showMessage(f"Loading: {file_path}")
        QApplication.processEvents()

        # Show progress dialog
        progress = QProgressDialog("Loading video file...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(500)
        progress.show()
        QApplication.processEvents()

        try:
            # Open with demuxer
            if not self._demuxer.open(file_path):
                progress.close()
                QMessageBox.critical(self, "Error", "Failed to open video file")
                return

            # Open with decoder
            if not self._decoder.open(file_path):
                progress.close()
                QMessageBox.warning(self, "Warning", "Failed to open decoder. Frame display will be unavailable.")

            self._current_file = file_path

            # Configure stream viewer for codec
            stream_info = self._demuxer.stream_info
            is_h265 = stream_info.codec_name.lower() in ('hevc', 'h265')
            self._stream_viewer.set_codec(
                stream_info.codec_name,
                is_avc=stream_info.is_avc,
                nal_length_size=stream_info.nal_length_size
            )

            # Parse extradata for SPS/PPS
            extradata = self._demuxer.get_extradata()
            if extradata:
                self._stream_viewer.set_extradata(extradata)

            # Refine frame types using NAL parsing
            self._refine_frame_types()

            # Update views
            self._stream_view.update_info(stream_info)
            self._barchart_view.set_frames(self._demuxer.frames)

            # Select first frame
            if self._demuxer.frames:
                self._select_frame(0)

            self._status_bar.showMessage(
                f"Loaded: {os.path.basename(file_path)} | "
                f"{stream_info.total_frames} frames | "
                f"{stream_info.codec_name.upper()}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{str(e)}")
            self._status_bar.showMessage("Error loading file")

        finally:
            progress.close()

    def _refine_frame_types(self):
        """Refine frame types by parsing NAL units."""
        for frame in self._demuxer.frames:
            if not frame.is_keyframe:
                frame_type = self._stream_viewer.get_frame_type_from_nalus(frame.packet_data)
                if frame_type != FrameType.UNKNOWN:
                    frame.frame_type = frame_type

    def _select_frame(self, index: int):
        """Select and display a frame."""
        if not self._demuxer.is_open:
            return

        frames = self._demuxer.frames
        if not 0 <= index < len(frames):
            return

        frame = frames[index]

        # Update bar chart selection
        self._barchart_view.select_frame(index)

        # Decode and display frame
        if self._decoder.is_open:
            rgb_array = self._decoder.decode_frame(index)
            if rgb_array is not None:
                self._decoded_view.display_frame(rgb_array, index)

        # Update stream viewer
        self._stream_viewer.display_frame(frame)

        # Update hex viewer
        self._hex_viewer.set_data(frame.packet_data)

        self._status_bar.showMessage(
            f"Frame {index} | {frame.frame_type.value}-frame | "
            f"{frame.size:,} bytes"
        )

    def _on_frame_selected(self, index: int):
        """Handle frame selection from bar chart."""
        self._select_frame(index)

    def _on_nalu_selected(self, offset: int, size: int):
        """Handle NALU selection from stream viewer."""
        self._hex_viewer.set_highlight(offset, offset + size)
        self._hex_viewer.scroll_to_offset(offset)

        # Raise hex viewer dock
        self._docks['hex'].raise_()

    def _prev_frame(self):
        """Go to previous frame."""
        current = self._barchart_view.selected_index
        if current > 0:
            self._select_frame(current - 1)

    def _next_frame(self):
        """Go to next frame."""
        current = self._barchart_view.selected_index
        if current < len(self._demuxer.frames) - 1:
            self._select_frame(current + 1)

    def _prev_keyframe(self):
        """Go to previous keyframe."""
        current = self._barchart_view.selected_index
        frames = self._demuxer.frames

        for i in range(current - 1, -1, -1):
            if frames[i].is_keyframe:
                self._select_frame(i)
                return

    def _next_keyframe(self):
        """Go to next keyframe."""
        current = self._barchart_view.selected_index
        frames = self._demuxer.frames

        for i in range(current + 1, len(frames)):
            if frames[i].is_keyframe:
                self._select_frame(i)
                return

    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About VideoEye",
            "VideoEye - Video Analysis Tool\n\n"
            "A video stream analyzer similar to Elecard StreamEye.\n\n"
            "Supports H.264/AVC and H.265/HEVC analysis with:\n"
            "• Frame visualization bar chart\n"
            "• NAL unit syntax parsing\n"
            "• Hex dump viewing\n"
            "• Decoded frame display\n\n"
            "Built with Python, PyQt6, and PyAV"
        )

    def dragEnterEvent(self, event):
        """Handle drag enter for file drop."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle file drop."""
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            self._load_file(file_path)

    def closeEvent(self, event):
        """Handle window close."""
        self._demuxer.close()
        self._decoder.close()
        event.accept()
