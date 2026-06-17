"""PyInstaller runtime hook: register the bundled PyQt6 Qt6 DLL directory so the
Qt extension modules find Qt6*.dll when frozen."""
import os
import sys

base = getattr(sys, "_MEIPASS", None)
if base:
    qt_bin = os.path.join(base, "PyQt6", "Qt6", "bin")
    if os.path.isdir(qt_bin):
        try:
            os.add_dll_directory(qt_bin)
        except (OSError, AttributeError):
            pass
