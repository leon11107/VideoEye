#!/usr/bin/env bash
# Generate streams that exercise slice / tile boundaries, for the boundary
# overlay work. Two groups:
#   base    -- row-aligned slices, uniform tiles (the simple, clean-line cases)
#   corner  -- the cases easy to miss when writing the renderer:
#     * mid-row slice starts  -> STAIRCASE / L-shaped boundary (H.264/HEVC slices
#       are raster/tile-scan order and may start in the middle of a CTB/MB row,
#       not just at a row start)
#     * non-uniform tiles     -> unevenly spaced grid lines (must read real widths)
#     * tiles + slices        -> slice addresses in tile-scan order
#     * asymmetric AV1 tiles  -> non-square, unequal tile widths
#
# x265 has NO tile support, so every HEVC tile stream is encoded with the HM
# reference encoder (TAppEncoder). Each stream is verified after generation.
#
# Output (gitignored): tests/streams/boundaries/
set -euo pipefail

HM="${HM:-C:/Users/llw/app/TAppEncoder.exe}"
HMCFG="${HMCFG:-C:/Users/llw/Desktop/HM/cfg/encoder_intra_main.cfg}"
W=832; H=480; N=8; FPS=30
ROW_MB=$((W / 16)); ROW_CTB=$(((W + 63) / 64))   # 52 MBs / 13 CTBs per row
OUT="tests/streams/boundaries"; TMP="$OUT/_tmp"
mkdir -p "$TMP"

# raw YUV420 for every HM encode
ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=${W}x${H}:rate=${FPS}" \
  -frames:v "$N" -pix_fmt yuv420p "$TMP/in.yuv"

testsrc() {  # ffmpeg testsrc2 -> encoder ($@ = codec args + output)
  ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=${W}x${H}:rate=${FPS}" \
    -frames:v "$N" "$@"
}
hm() {  # hm <out.mp4> <extra HM args...>
  local out="$1"; shift
  "$HM" -c "$HMCFG" --InputFile="$TMP/in.yuv" --SourceWidth=$W --SourceHeight=$H \
    --FrameRate=$FPS --FramesToBeEncoded=$N --InputBitDepth=8 --IntraPeriod=1 --Level=4 \
    "$@" --BitstreamFile="$TMP/hm.bin" --ReconFile="$TMP/hm_rec.yuv" >"$TMP/hm.log" 2>&1
  ffmpeg -loglevel error -y -i "$TMP/hm.bin" -c:v copy "$out"
}

echo "########## base (simple, row-aligned / uniform) ##########"
testsrc -threads 1 -c:v libx264 -x264-params "slices=4:sliced-threads=0" "$OUT/bnd_h264_slices.mp4"
testsrc -c:v libx265 -x265-params "slices=4" "$OUT/bnd_h265_slices.mp4"
hm "$OUT/bnd_h265_tiles.mp4" --TileUniformSpacing=1 --NumTileColumnsMinus1=1 --NumTileRowsMinus1=1
testsrc -c:v libaom-av1 -tile-columns 1 -tile-rows 1 -cpu-used 8 -crf 40 -strict experimental "$OUT/bnd_av1_tiles.mp4"

echo "########## corner cases ##########"
# mid-row slice starts (staircase boundary)
testsrc -threads 1 -c:v libx264 -x264-params "slice-max-mbs=200" "$OUT/bnd_h264_slices_midrow.mp4"
hm "$OUT/bnd_h265_slices_midrow.mp4" --SliceMode=1 --SliceArgument=20
# non-uniform tile grid: cols [5,8] CTBs, rows [3,5] CTBs
hm "$OUT/bnd_h265_tiles_nonuniform.mp4" --TileUniformSpacing=0 \
   --NumTileColumnsMinus1=1 --TileColumnWidthArray="5" \
   --NumTileRowsMinus1=1 --TileRowHeightArray="3"
# tiles (2x2) + slices (2 tiles/slice) -> slice address in tile-scan order
hm "$OUT/bnd_h265_tiles_slices.mp4" --TileUniformSpacing=1 \
   --NumTileColumnsMinus1=1 --NumTileRowsMinus1=1 --SliceMode=3 --SliceArgument=2
# asymmetric AV1 tiles: 4 cols x 1 row (unequal uniform widths)
testsrc -c:v libaom-av1 -tile-columns 2 -tile-rows 0 -cpu-used 8 -crf 40 -strict experimental "$OUT/bnd_av1_tiles_asym.mp4"

rm -rf "$TMP"

echo
echo "===================== verify ====================="
hdr() { ffmpeg -loglevel trace -i "$1" -c:v copy -bsf:v trace_headers -f null - 2>&1; }
mb_starts()  { hdr "$1" | grep -oE "first_mb_in_slice +[0-9]+ = [0-9]+"   | grep -oE "[0-9]+$" | head -5 \
               | awk -v r=$ROW_MB  '{printf " %d[r%dc%d%s]",$1,int($1/r),$1%r,($1%r?"*":"")}'; }
ctb_starts() { hdr "$1" | grep -oE "slice_segment_address +[01]+ = [0-9]+" | grep -oE "[0-9]+$" | head -5 \
               | awk -v r=$ROW_CTB '{printf " %d[r%dc%d%s]",$1,int($1/r),$1%r,($1%r?"*":"")}'; }
tilefields() { hdr "$1" | grep -oE "(tiles_enabled_flag|uniform_spacing_flag|num_tile_columns_minus1|num_tile_rows_minus1|column_width_minus1\[0\]|row_height_minus1\[0\]) +[01]+ = [0-9]+" | sort -u | sed 's/^/    /'; }

echo "[base] h264 slices  (all col0 = row-aligned):$(mb_starts "$OUT/bnd_h264_slices.mp4")"
echo "[base] h265 slices  (all col0 = row-aligned):$(ctb_starts "$OUT/bnd_h265_slices.mp4")"
echo "[base] h265 tiles   (uniform 2x2):";           tilefields "$OUT/bnd_h265_tiles.mp4"
echo "[base] av1  tiles   (uniform 2x2):";           QT_QPA_PLATFORM=offscreen py -3.14 tools/_check_av1_tiles.py "$OUT/bnd_av1_tiles.mp4" | grep "=>"
echo "[corner] h264 slices mid-row (*=MID-ROW):$(mb_starts "$OUT/bnd_h264_slices_midrow.mp4")"
echo "[corner] h265 slices mid-row (*=MID-ROW):$(ctb_starts "$OUT/bnd_h265_slices_midrow.mp4")"
echo "[corner] h265 tiles non-uniform:";             tilefields "$OUT/bnd_h265_tiles_nonuniform.mp4"
echo "[corner] h265 tiles+slices:";                  tilefields "$OUT/bnd_h265_tiles_slices.mp4"
echo "[corner] av1 tiles asymmetric:";               QT_QPA_PLATFORM=offscreen py -3.14 tools/_check_av1_tiles.py "$OUT/bnd_av1_tiles_asym.mp4" | grep "=>"
echo
ls -la "$OUT"/*.mp4
