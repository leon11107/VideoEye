#!/usr/bin/env bash
# Build the veye_probe helper against the patched FFmpeg static libs.
# Run from an MSYS2 UCRT64 shell:  bash native/build_probe.sh
set -e

FFMPEG_PREFIX="${FFMPEG_PREFIX:-/c/Users/llw/Desktop/ffmpeg-veye/install}"
HERE="$(cd "$(dirname "$0")" && pwd)"

export PKG_CONFIG_PATH="$FFMPEG_PREFIX/lib/pkgconfig"
PKG="pkg-config --static"

CFLAGS="$($PKG --cflags libavformat libavcodec libavutil)"
LIBS="$($PKG --libs libavformat libavcodec libavutil)"

echo "CFLAGS=$CFLAGS"
echo "LIBS=$LIBS"

gcc -O2 -o "$HERE/veye_probe.exe" "$HERE/veye_probe.c" $CFLAGS $LIBS
echo "built: $HERE/veye_probe.exe"

# Copy MinGW runtime DLLs next to the exe so it runs without MSYS2 on PATH.
BINDIR="$(dirname "$(command -v gcc)")"
for dll in libbz2-1 libiconv-2 liblzma-5 zlib1 libwinpthread-1 libgcc_s_seh-1; do
    [ -f "$BINDIR/$dll.dll" ] && cp -f "$BINDIR/$dll.dll" "$HERE/" || true
done
echo "copied runtime DLLs into $HERE"
