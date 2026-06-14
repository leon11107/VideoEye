"""Main application window."""

import os
from PyQt6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog, QMessageBox,
    QApplication, QToolBar, QStatusBar, QProgressDialog,
    QSpinBox, QLabel
)
from PyQt6.QtCore import Qt, QTimer, QMutex
from PyQt6.QtGui import QAction, QKeySequence

from .core.demuxer import Demuxer
from .core.decoder import Decoder
from .core.decode_worker import DecodeWorker
from .core.frame_info import FrameType
from .views.stream_view import StreamView
from .views.barchart_view import BarChartView
from .views.decoded_view import DecodedView
from .views.stream_viewer import StreamViewer
from .views.hex_viewer import HexViewer
from .views.block_info_view import (
    OverlayControls, FrameStatsPanel, BlockHoverPanel
)


class MainWindow(QMainWindow):
    """Main application window with dockable panels."""

    def __init__(self):
        super().__init__()

        self._demuxer = Demuxer()
        self._decoder = Decoder()
        self._current_file = ""
        # Guards against re-entrant loads: _load_file pumps the event loop via
        # processEvents (for the progress bar), which could otherwise let a
        # second Open/drop start mid-load and corrupt demuxer/decoder state.
        self._is_loading = False

        # Off-UI-thread decoding. _decode_lock serializes all decoder access
        # (worker decodes; open/close hold it on the UI thread).
        self._decode_lock = QMutex()
        self._decode_worker = DecodeWorker(self._decoder, self._decode_lock)
        self._decode_worker.ready.connect(self._on_frame_decoded)
        self._decode_worker.start()

        # Playback state
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)
        self._is_playing = False
        self._play_fps = 30

        # Background block-analysis progress polling
        self._current_index = -1
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(250)
        self._analysis_timer.timeout.connect(self._on_analysis_tick)
        self._last_ready = -1
        self._analysis_finalized = False

        self._setup_ui()
        self._setup_menus()
        self._setup_toolbar()
        self._setup_connections()

        self.setWindowTitle("VideoEye - Video Analysis Tool")
        self.resize(1400, 900)

    def _setup_ui(self):
        """Set up the UI with dockable panels."""
        # Allow nested docks for flexible side-by-side arrangements
        self.setDockNestingEnabled(True)

        # Central widget - decoded frame view
        self._decoded_view = DecodedView()
        self.setCentralWidget(self._decoded_view)

        all_areas = (Qt.DockWidgetArea.LeftDockWidgetArea |
                     Qt.DockWidgetArea.RightDockWidgetArea |
                     Qt.DockWidgetArea.TopDockWidgetArea |
                     Qt.DockWidgetArea.BottomDockWidgetArea)

        # Left docks, presented as tabs: Stream Info / Overlays / Frame Stats /
        # Block Info. Each is its own dock so the user can pull tabs apart, but
        # they start tabbed together so the panel isn't crowded.
        self._stream_view = StreamView()
        stream_dock = QDockWidget("Stream Info", self)
        stream_dock.setWidget(self._stream_view)
        stream_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, stream_dock)

        self._overlay_controls = OverlayControls()
        overlay_dock = QDockWidget("Overlays", self)
        overlay_dock.setWidget(self._overlay_controls)
        overlay_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, overlay_dock)

        self._frame_stats = FrameStatsPanel()
        stats_dock = QDockWidget("Frame Stats", self)
        stats_dock.setWidget(self._frame_stats)
        stats_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, stats_dock)

        self._block_hover = BlockHoverPanel()
        block_dock = QDockWidget("Block Info", self)
        block_dock.setWidget(self._block_hover)
        block_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, block_dock)

        # Tab them together and show Stream Info first.
        self.tabifyDockWidget(stream_dock, overlay_dock)
        self.tabifyDockWidget(overlay_dock, stats_dock)
        self.tabifyDockWidget(stats_dock, block_dock)
        stream_dock.raise_()

        # Bottom dock - Bar chart
        self._barchart_view = BarChartView()
        barchart_dock = QDockWidget("Frame Chart", self)
        barchart_dock.setWidget(self._barchart_view)
        barchart_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, barchart_dock)

        # Right dock - Stream viewer (NALU tree)
        self._stream_viewer = StreamViewer()
        viewer_dock = QDockWidget("NALU Viewer", self)
        viewer_dock.setWidget(self._stream_viewer)
        viewer_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, viewer_dock)

        # Right dock - Hex viewer (split vertically below NALU viewer)
        self._hex_viewer = HexViewer()
        hex_dock = QDockWidget("Hex Viewer", self)
        hex_dock.setWidget(self._hex_viewer)
        hex_dock.setAllowedAreas(all_areas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, hex_dock)
        # Place NALU and Hex side by side (vertically split) instead of tabbed
        self.splitDockWidget(viewer_dock, hex_dock, Qt.Orientation.Vertical)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")
        # Permanent widget for background block-analysis progress (kept
        # separate from showMessage so frame status isn't clobbered).
        self._analysis_label = QLabel("")
        self._status_bar.addPermanentWidget(self._analysis_label)

        # Store dock references for menu
        self._docks = {
            'stream': stream_dock,
            'overlays': overlay_dock,
            'stats': stats_dock,
            'blockinfo': block_dock,
            'barchart': barchart_dock,
            'viewer': viewer_dock,
            'hex': hex_dock
        }

        # Set initial dock proportions
        # Left (stream) : Center : Right (NALU/Hex) roughly 1:3:2
        self.resizeDocks(
            [stream_dock, viewer_dock],
            [240, 340],
            Qt.Orientation.Horizontal
        )
        # Bottom bar chart height
        self.resizeDocks(
            [barchart_dock],
            [150],
            Qt.Orientation.Vertical
        )

    def _setup_menus(self):
        """Set up menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_file_dialog)
        file_menu.addAction(open_action)

        self._close_action = QAction("&Close", self)
        self._close_action.setShortcut(QKeySequence("Ctrl+W"))
        self._close_action.triggered.connect(self._close_file)
        self._close_action.setEnabled(False)
        file_menu.addAction(self._close_action)

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

        nav_menu.addSeparator()

        play_action = QAction("&Play / Pause", self)
        play_action.setShortcut("Space")
        play_action.triggered.connect(self._toggle_play)
        nav_menu.addAction(play_action)

        stop_action = QAction("&Stop", self)
        stop_action.setShortcut("Escape")
        stop_action.triggered.connect(self._stop_playback)
        nav_menu.addAction(stop_action)

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

        close_action = QAction("Close", self)
        close_action.setToolTip("Close file (Ctrl+W)")
        close_action.triggered.connect(self._close_file)
        toolbar.addAction(close_action)

        toolbar.addSeparator()

        prev_key_action = QAction("◀◀", self)
        prev_key_action.setToolTip("Previous Keyframe (Shift+Left)")
        prev_key_action.triggered.connect(self._prev_keyframe)
        toolbar.addAction(prev_key_action)

        prev_action = QAction("◀", self)
        prev_action.setToolTip("Previous Frame (Left)")
        prev_action.triggered.connect(self._prev_frame)
        toolbar.addAction(prev_action)

        # Play/Pause/Stop
        self._play_action = QAction("▶ Play", self)
        self._play_action.setToolTip("Play (Space)")
        self._play_action.triggered.connect(self._toggle_play)
        toolbar.addAction(self._play_action)

        self._stop_action = QAction("■ Stop", self)
        self._stop_action.setToolTip("Stop (Escape)")
        self._stop_action.triggered.connect(self._stop_playback)
        toolbar.addAction(self._stop_action)

        next_action = QAction("▶", self)
        next_action.setToolTip("Next Frame (Right)")
        next_action.triggered.connect(self._next_frame)
        toolbar.addAction(next_action)

        next_key_action = QAction("▶▶", self)
        next_key_action.setToolTip("Next Keyframe (Shift+Right)")
        next_key_action.triggered.connect(self._next_keyframe)
        toolbar.addAction(next_key_action)

        toolbar.addSeparator()

        # FPS control
        toolbar.addWidget(QLabel(" FPS: "))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 120)
        self._fps_spin.setValue(self._play_fps)
        self._fps_spin.setToolTip("Playback speed (frames per second)")
        self._fps_spin.valueChanged.connect(self._on_fps_changed)
        self._fps_spin.setFixedWidth(60)
        toolbar.addWidget(self._fps_spin)

    def _setup_connections(self):
        """Set up signal connections between views."""
        # Bar chart selection -> decode and display
        self._barchart_view.frame_selected.connect(self._on_frame_selected)

        # Stream viewer NALU selection -> hex viewer highlight
        self._stream_viewer.nalu_selected.connect(self._on_nalu_selected)

        # Overlay toggles -> decoded view layers
        self._overlay_controls.overlays_changed.connect(
            self._decoded_view.set_overlays
        )

        # Hovered block in decoded view -> block info panel
        self._decoded_view.block_hovered.connect(
            self._block_hover.set_hover
        )

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
        # Ignore re-entrant loads triggered while a load is already pumping the
        # event loop (a second Open dialog, a file drop, etc.).
        if self._is_loading:
            return
        self._is_loading = True

        # Stop any ongoing playback
        self._pause_playback()

        self._status_bar.showMessage(f"Loading: {file_path}")
        QApplication.processEvents()

        # Show progress dialog. The two open-time scans (frame indexing and
        # I/P/B classification) drive it via on_progress for a real percentage
        # instead of an indeterminate spinner.
        progress = QProgressDialog("Indexing frames...", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()

        def on_progress(stage: str, current: int, total: int) -> None:
            progress.setLabelText(
                "Indexing frames..." if stage == "index"
                else "Classifying frame types..."
            )
            if total > 0:
                progress.setMaximum(total)
                progress.setValue(min(current, total))
            else:
                progress.setMaximum(0)  # unknown count: stay indeterminate
            QApplication.processEvents()

        try:
            # Open with demuxer first (extracts frame list with keyframe info)
            if not self._demuxer.open(file_path, progress_cb=on_progress):
                progress.close()
                QMessageBox.critical(self, "Error", "Failed to open video file")
                return

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
            self._refine_frame_types(on_progress)

            # Open decoder with frame list for keyframe-aware seeking. Hold the
            # decode lock so it never overlaps a decode running on the worker.
            self._decode_lock.lock()
            try:
                decoder_ok = self._decoder.open(file_path, frames=self._demuxer.frames)
            finally:
                self._decode_lock.unlock()
            if not decoder_ok:
                QMessageBox.warning(self, "Warning", "Failed to open decoder. Frame display will be unavailable.")

            # Update views
            self._stream_view.update_info(stream_info)
            self._barchart_view.set_frames(self._demuxer.frames)

            # Select first frame
            if self._demuxer.frames:
                self._select_frame(0)

            # Begin polling background block-analysis progress. The helper
            # streams frames in decode order; overlays fill in as it advances.
            self._last_ready = -1
            self._analysis_finalized = False
            self._analysis_label.setText("")
            self._analysis_timer.start()

            self._close_action.setEnabled(True)

            hw = self._decoder.hw_accel
            hw_label = f" | Decode: {hw}" if hw != "software" else ""
            self._status_bar.showMessage(
                f"Loaded: {os.path.basename(file_path)} | "
                f"{stream_info.total_frames} frames | "
                f"{stream_info.codec_name.upper()}{hw_label}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{str(e)}")
            self._status_bar.showMessage("Error loading file")

        finally:
            progress.close()
            self._is_loading = False

    def _close_file(self):
        """Close current file and release all memory."""
        self._pause_playback()
        self._analysis_timer.stop()
        self._analysis_label.setText("")
        self._current_index = -1

        # Release decoder (drops frame cache, stops the analysis helper)
        self._decoder.close()

        # Release demuxer (drops frame list + reader)
        self._demuxer.close()

        self._current_file = ""

        # Clear all views
        self._decoded_view.clear()
        self._barchart_view.clear()
        self._stream_viewer.clear()
        self._hex_viewer.clear()
        self._stream_view.clear()
        self._frame_stats.clear()
        self._block_hover.clear()

        self._close_action.setEnabled(False)

        self.setWindowTitle("VideoEye - Video Analysis Tool")
        self._status_bar.showMessage("File closed — memory released")

    def _refine_frame_types(self, progress_cb=None):
        """Refine frame types via single-pass sequential read.

        Only one packet's data is in memory at a time — the classifier
        receives the bytes, classifies I/P/B, and the data is discarded.

        For raw elementary streams (no container timestamps) a POC tracker is
        run in the same pass to derive each frame's display order, so the
        decoder can map decode-order frames to the decoder's output order.
        """
        poc_tracker = None
        frames = self._demuxer.frames
        is_raw = bool(frames) and all(f.pts is None for f in frames[:8])
        # POC only matters when decode order can differ from display order,
        # i.e. there are non-keyframes. An all-intra stream is already in
        # display order, so skip the tracker (and its full-file byte read).
        if is_raw and any(not f.is_keyframe for f in frames):
            from .parsers.poc import create_poc_tracker
            poc_tracker = create_poc_tracker(self._demuxer.codec_name)

        self._demuxer.classify_frame_types(
            self._stream_viewer.get_frame_type_from_nalus,
            progress_cb=progress_cb,
            poc_tracker=poc_tracker,
        )
        # The decoder is opened after this pass (see _load_file), so it builds
        # its order maps from the now poc-bearing frame list automatically.

    def _select_frame(self, index: int):
        """Select a frame (interactive). Decode runs off the UI thread.

        NALU/hex are updated immediately; the picture + block overlay arrive
        from the worker. Playback uses the sequential decode path instead.
        """
        if not self._demuxer.is_open:
            return

        frames = self._demuxer.frames
        if not 0 <= index < len(frames):
            return

        self._current_index = index
        frame = frames[index]

        # Update bar chart selection + mark this frame's reference frames.
        self._barchart_view.select_frame(index)
        self._update_ref_markers(index)

        # NALU/hex views are cheap and independent of decoding; update them
        # immediately for instant feedback. Skip during playback.
        if not self._is_playing:
            self._update_analysis_views(frame)

        self._status_bar.showMessage(
            f"Frame {index} | {frame.frame_type.value}-frame | "
            f"{frame.size:,} bytes"
        )

        # Decode off the UI thread; latest request wins so scrubbing stays snappy.
        if self._decoder.is_open:
            self._decode_worker.request(index)

    def _on_frame_decoded(self, index: int, rgb, analysis):
        """Receive a decoded frame from the worker (UI thread via signal).

        Displayed whatever the index: the worker only decodes the latest
        request, so during playback this shows each frame in real time, and
        when the user settles on a frame the final emission is that frame.
        """
        if rgb is None:
            return
        self._decoded_view.display_frame(rgb, index, analysis)
        self._frame_stats.set_analysis(analysis)

    def _update_analysis_views(self, frame: 'FrameInfo'):
        """Load packet data on demand and update NALU & hex views."""
        # Lazy-load packet data (only one packet in memory at a time)
        packet_data = self._demuxer.read_packet_data(frame.index)
        self._stream_viewer.display_frame(frame, packet_data)
        self._hex_viewer.set_data(packet_data)

    def _update_ref_markers(self, index: int) -> None:
        """Mark frame `index`'s reference frames on the chart (or clear)."""
        refs = self._decoder.refs_for(index) if self._decoder.is_open else None
        if refs is not None:
            self._barchart_view.set_ref_markers(refs[0], refs[1])
        else:
            self._barchart_view.set_ref_markers([], [])

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

    # -- Playback controls --

    def _toggle_play(self):
        """Toggle play/pause."""
        if self._is_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        """Start automatic frame playback (display order, sequential decode)."""
        if not self._demuxer.is_open or not self._demuxer.frames:
            return
        start = self._barchart_view.selected_index
        if start < 0:
            start = 0
        if self._decoder.is_open:
            self._decode_lock.lock()
            try:
                self._decoder.begin_sequential(start)
            finally:
                self._decode_lock.unlock()
        self._is_playing = True
        self._play_action.setText("⏸ Pause")
        self._play_action.setToolTip("Pause (Space)")
        interval = max(1, int(1000 / self._play_fps))
        self._play_timer.start(interval)

    def _pause_playback(self):
        """Pause playback and refresh analysis views for current frame."""
        was_playing = self._is_playing
        self._is_playing = False
        self._play_timer.stop()
        self._play_action.setText("▶ Play")
        self._play_action.setToolTip("Play (Space)")

        # Refresh views skipped during playback: NALU/hex now, and the block
        # analysis (overlay + block panel) via the worker, since playback only
        # decoded the picture.
        if was_playing and self._demuxer.is_open:
            idx = self._barchart_view.selected_index
            if 0 <= idx < len(self._demuxer.frames):
                self._update_analysis_views(self._demuxer.frames[idx])
                if self._decoder.is_open:
                    self._decode_worker.request(idx)

    def _stop_playback(self):
        """Stop playback and return to first frame."""
        self._pause_playback()
        if self._demuxer.is_open and self._demuxer.frames:
            self._select_frame(0)

    def _on_play_tick(self):
        """Advance one frame in display order via the sequential decoder."""
        if not self._decoder.is_open:
            self._pause_playback()
            return

        want_analysis = self._decoded_view.has_overlays()
        self._decode_lock.lock()
        try:
            res = self._decoder.decode_next()
            # Overlay needs sidecar fill; the frame is freshly cached so this
            # is a cache hit + sidecar lookup (only when an overlay is on).
            analysis = (self._decoder.get_analysis(res[0])
                        if res is not None and want_analysis else
                        (res[2] if res is not None else None))
        finally:
            self._decode_lock.unlock()

        if res is None:
            self._pause_playback()  # reached end of stream
            return

        index, rgb, _ = res
        self._current_index = index
        self._barchart_view.select_frame(index)
        self._update_ref_markers(index)  # follow references during playback
        self._decoded_view.display_frame(rgb, index, analysis if want_analysis else None)
        if want_analysis:
            self._frame_stats.set_analysis(analysis)

        frame = self._demuxer.frames[index]
        self._status_bar.showMessage(
            f"Frame {index} | {frame.frame_type.value}-frame | "
            f"{frame.size:,} bytes"
        )

    def _on_analysis_tick(self):
        """Poll background block-analysis progress and stream results in."""
        ready, total, status = self._decoder.analysis_progress()

        if status == "running":
            self._analysis_label.setText(
                f"Analyzing blocks: {ready}/{total}" if total
                else f"Analyzing blocks: {ready}"
            )
        elif status == "done":
            self._analysis_label.setText(f"Blocks ready: {ready}")
        elif status == "failed":
            self._analysis_label.setText("Block analysis unavailable")
        else:  # unavailable
            self._analysis_label.setText("")

        terminal = status in ("done", "failed", "unavailable")

        # Refresh the current frame's overlays/stats as data arrives. The
        # analysis object is shared with the decoder cache and filled in place,
        # so re-querying picks up newly decoded blocks for this frame.
        if (not self._is_playing and self._current_index >= 0
                and (ready != self._last_ready
                     or (terminal and not self._analysis_finalized))):
            # The current frame is cached, so get_analysis only refills its
            # sidecar fields (no container decode); still take the lock so it
            # never races a worker decode touching the shared cache/sidecar.
            self._decode_lock.lock()
            try:
                analysis = self._decoder.get_analysis(self._current_index)
            finally:
                self._decode_lock.unlock()
            if analysis is not None:
                self._decoded_view.refresh_overlays(analysis)
                self._frame_stats.set_analysis(analysis)
        self._last_ready = ready

        if terminal:
            self._analysis_finalized = True
            self._analysis_timer.stop()

    def _on_fps_changed(self, value: int):
        """Handle FPS spinbox change."""
        self._play_fps = value
        if self._is_playing:
            interval = max(1, int(1000 / self._play_fps))
            self._play_timer.setInterval(interval)

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
        self._pause_playback()
        self._analysis_timer.stop()
        self._decode_worker.stop()  # join before tearing down the decoder
        self._demuxer.close()
        self._decode_lock.lock()
        try:
            self._decoder.close()
        finally:
            self._decode_lock.unlock()
        event.accept()
