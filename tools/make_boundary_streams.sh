#!/usr/bin/env bash
# Generate streams that exercise slice / tile boundaries, for the boundary
# overlay work (H.264 slices, HEVC slices, HEVC tiles, AV1 tiles).
#
# x265 has NO tile support, so the HEVC tile stream is encoded with the HM
# reference encoder (TAppEncoder). Each stream is verified after generation
# (ffmpeg trace_headers for H.26x; the project's AV1 parser for AV1 tiles).
#
# Output (gitignored): tests/streams/boundaries/
#   bnd_h264_slices.mp4   4 slices/frame   (libx264)
#   bnd_h265_slices.mp4   4 slices/frame   (libx265)
#   bnd_h265_tiles.mp4    2x2 tiles        (HM TAppEncoder)
#   bnd_av1_tiles.mp4     2x2 tiles        (libaom-av1)
set -euo pipefail

# -- tool locations (override via env) --------------------------------------
HM="${HM:-C:/Users/llw/app/TAppEncoder.exe}"
HMCFG="${HMCFG:-C:/Users/llw/Desktop/HM/cfg/encoder_intra_main.cfg}"

W=832; H=480; N=8; FPS=30
OUT="tests/streams/boundaries"
TMP="$OUT/_tmp"
mkdir -p "$TMP"

src() {  # $1=extra testsrc; writes raw frames to stdout sink via lavfi
  ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=${W}x${H}:rate=${FPS}" -frames:v "$N" "$@"
}

echo "== 1/4 H.264 slices =="
# slices=4 fixes 4 rectangular slices/frame; sliced-threads would instead tie
# the slice count to the thread count, so keep it off and single-threaded.
src -threads 1 -c:v libx264 -x264-params "slices=4:sliced-threads=0" \
    "$OUT/bnd_h264_slices.mp4"

echo "== 2/4 H.265 slices =="
src -c:v libx265 -x265-params "slices=4" "$OUT/bnd_h265_slices.mp4"

echo "== 3/4 H.265 tiles (HM, 2x2) =="
ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=${W}x${H}:rate=${FPS}" \
  -frames:v "$N" -pix_fmt yuv420p "$TMP/in.yuv"
"$HM" -c "$HMCFG" \
  --InputFile="$TMP/in.yuv" --SourceWidth=$W --SourceHeight=$H \
  --FrameRate=$FPS --FramesToBeEncoded=$N --InputBitDepth=8 \
  --IntraPeriod=1 --Level=4 \
  --TileUniformSpacing=1 --NumTileColumnsMinus1=1 --NumTileRowsMinus1=1 \
  --BitstreamFile="$TMP/tiles.bin" --ReconFile="$TMP/rec.yuv" >"$TMP/hm.log" 2>&1
ffmpeg -loglevel error -y -i "$TMP/tiles.bin" -c:v copy "$OUT/bnd_h265_tiles.mp4"

echo "== 4/4 AV1 tiles (libaom, 2x2) =="
src -c:v libaom-av1 -tile-columns 1 -tile-rows 1 -cpu-used 8 -crf 40 \
  -strict experimental "$OUT/bnd_av1_tiles.mp4"

rm -rf "$TMP"

echo
echo "===================== verify ====================="
hdr() { ffmpeg -loglevel trace -i "$1" -c:v copy -bsf:v trace_headers -f null - 2>&1; }

echo "-- H.264 slices: first_mb_in_slice per frame (expect 4 distinct) --"
hdr "$OUT/bnd_h264_slices.mp4" | grep -oE "first_mb_in_slice +[0-9]+ = [0-9]+" | head -8

echo "-- H.265 slices: slice_segment_address (expect non-zero addresses) --"
hdr "$OUT/bnd_h265_slices.mp4" | grep -cE "first_slice_segment_in_pic_flag .* = 0" \
  | sed 's/^/  dependent\/non-first slice segments: /'
hdr "$OUT/bnd_h265_slices.mp4" | grep -oE "slice_segment_address +[01]+ = [0-9]+" | head -6

echo "-- H.265 tiles: PPS tile fields (expect tiles_enabled=1, 2x2) --"
hdr "$OUT/bnd_h265_tiles.mp4" | grep -oE "(tiles_enabled_flag|num_tile_columns_minus1|num_tile_rows_minus1|uniform_spacing_flag) +[01]+ = [0-9]+" | head -4

echo "-- AV1 tiles: parsed tile_info from the bitstream --"
QT_QPA_PLATFORM=offscreen py -3.14 tools/_check_av1_tiles.py "$OUT/bnd_av1_tiles.mp4" || true

echo
echo "streams in $OUT:"
ls -la "$OUT"/*.mp4
