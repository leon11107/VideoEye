# -*- mode: python ; coding: utf-8 -*-
#
# Build:  py -3.12 -m PyInstaller --noconfirm VideoEye.spec
# Output: dist/VideoEye/VideoEye.exe  (onedir)
#
# Notes:
#  - Python 3.14 + PyInstaller can't freeze PyQt6 yet; build with Python 3.12.
#  - rthook_qt.py registers PyQt6/Qt6/bin for DLL resolution when frozen.
#  - Qt6Core imports icuuc.dll and requires the Windows System32 ICU. If MSYS2
#    (C:\msys64\...\bin) is on PATH, PyInstaller may bundle MSYS2's icu*.dll,
#    which shadows the system one and breaks Qt with error 127. We strip any
#    bundled icu*.dll so Qt always uses the system ICU.

import os

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('src/assets', 'src/assets'), ('native', 'native')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_qt.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Drop wrong-version ICU DLLs (MSYS2) so Qt6 uses the Windows system ICU.
a.binaries = [b for b in a.binaries
              if not os.path.basename(b[0]).lower().startswith(
                  ('icuuc', 'icudt', 'icuin'))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VideoEye',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VideoEye',
)
