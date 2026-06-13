"""Offscreen UI smoke test: load stream, toggle overlays, capture screenshots."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from src.app import MainWindow

stream = sys.argv[1] if len(sys.argv) > 1 else r"tests\streams\bball_1080p_x264.mp4"
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_out")
os.makedirs(out_dir, exist_ok=True)

app = QApplication([])
win = MainWindow()
win.resize(1700, 1000)
win.show()

win._load_file(stream)
win._select_frame(2)  # B/P frame with MVs
app.processEvents()
win.grab().save(os.path.join(out_dir, "1_base.png"))

cbs = win._block_info_view._checkboxes
cbs["qp"].setChecked(True)
app.processEvents()
win.grab().save(os.path.join(out_dir, "2_qp.png"))

cbs["qp"].setChecked(False)
cbs["mv"].setChecked(True)
app.processEvents()
win.grab().save(os.path.join(out_dir, "3_mv.png"))

cbs["mv"].setChecked(False)
cbs["partition"].setChecked(True)
app.processEvents()
win.grab().save(os.path.join(out_dir, "4_partition.png"))

# Hover simulation at the center of the displayed image
label = win._decoded_view._image_label
shown = label.pixmap()
if shown:
    win._decoded_view._on_mouse_moved(
        QPoint(shown.width() // 2, shown.height() // 2)
    )
    app.processEvents()

print("=== Frame Statistics ===")
print(win._block_info_view._stats_label.text())
print("=== Block at Cursor ===")
print(win._block_info_view._hover_text())

# Quick sanity over several frames including seeks
for idx in (0, 5, 1, 30, 10, 59):
    win._select_frame(idx)
    app.processEvents()
    a = win._decoder.get_analysis(idx)
    qp = a.qp_stats() if a else None
    nmv = len(a.mvs) if a is not None and a.mvs is not None else 0
    print(f"frame {idx}: pict={a.pict_type if a else '?'} qp={qp} mvs={nmv}")

win.close()
print("smoke OK")
