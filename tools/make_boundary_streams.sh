#!/usr/bin/env bash
# Generate slice/tile boundary test streams with the STANDARD REFERENCE encoders:
#   AVC  -> JM  lencod        (H.264/AVC reference software)
#   HEVC -> HM  TAppEncoder   (H.265/HEVC reference software; x265 has no tiles)
#   AV1  -> aomenc            (AOM AV1 reference encoder)
# ffmpeg is used only to synthesize the raw YUV input and to mux each encoder's
# elementary stream (.264 / .bin / .ivf) into .mp4.
#
# Two groups per codec:
#   base    -- row-aligned slices, uniform tiles (clean straight-line boundaries)
#   corner  -- cases easy to miss in the renderer:
#     * mid-row slice starts  -> STAIRCASE / L boundary (H.264/HEVC slices are
#       raster/tile-scan order and may start mid CTB/MB row, not just at a row)
#     * non-uniform tiles     -> unevenly spaced grid (must read real widths)
#     * tiles + slices        -> slice addresses in tile-scan order
#     * asymmetric AV1 tiles  -> non-square, unequal tile widths
#
# Slice/tile config knobs:
#   JM      SliceMode=1 (fixed #MB/slice) + SliceArgument; multiple of the MB row
#           width (SourceWidth/16) => row-aligned, otherwise mid-row.
#   HM      Tiles: TileUniformSpacing / NumTile{Columns,Rows}Minus1 /
#           Tile{Column,Row}{Width,Height}Array. Slices: SliceMode=1 (fixed #CTU)
#           + SliceArgument; SliceMode=3 + SliceArgument = max tiles per slice.
#           (HM asserts a minimum tile width, so tiny tile columns are rejected.)
#   aomenc  --tile-columns / --tile-rows are log2 counts.
#
# Output (gitignored): tests/streams/boundaries/
set -euo pipefail

LENCOD="${LENCOD:-C:/Users/llw/Desktop/JM/bin/vs17/msvc-19.44/x86_64/release/lencod.exe}"
JMCFG="${JMCFG:-C:/Users/llw/Desktop/JM/cfg/encoder_main.cfg}"
HM="${HM:-C:/Users/llw/app/TAppEncoder.exe}"
HMCFG="${HMCFG:-C:/Users/llw/Desktop/HM/cfg/encoder_intra_main.cfg}"
AOMENC="${AOMENC:-C:/Users/llw/Desktop/aom_build/aomenc.exe}"
AOM_DLLS="${AOM_DLLS:-/c/msys64/ucrt64/bin}"   # aomenc is MSYS2-linked; needs its DLLs on PATH

W=832; H=480; N=8; FPS=30
ROW_MB=$((W / 16)); ROW_CTB=$(((W + 63) / 64))   # 52 MBs / 13 CTBs per row
OUT="tests/streams/boundaries"; TMP="$OUT/_tmp"
mkdir -p "$TMP"
TMP="$(cd "$TMP" && pwd)"   # absolute: jm() cd's into a subshell to corral JM's stray files
ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=${W}x${H}:rate=${FPS}" \
  -frames:v "$N" -pix_fmt yuv420p "$TMP/in.yuv"

jm() {  # jm <out.mp4> <SliceArgument>
  local out="$1" arg="$2"
  ( cd "$TMP" && "$LENCOD" -d "$JMCFG" -p InputFile="$TMP/in.yuv" \
      -p SourceWidth=$W -p SourceHeight=$H -p FramesToBeEncoded=$N -p FrameRate=$FPS \
      -p OutputFile="$TMP/jm.264" -p ReconFile="$TMP/jm_rec.yuv" -p OutFileMode=0 \
      -p SliceMode=1 -p SliceArgument=$arg >"$TMP/jm.log" 2>&1 )
  ffmpeg -loglevel error -y -i "$TMP/jm.264" -c:v copy "$out"
}
hm() {  # hm <out.mp4> <extra HM args...>
  local out="$1"; shift
  "$HM" -c "$HMCFG" --InputFile="$TMP/in.yuv" --SourceWidth=$W --SourceHeight=$H \
    --FrameRate=$FPS --FramesToBeEncoded=$N --InputBitDepth=8 --IntraPeriod=1 --Level=4 \
    "$@" --BitstreamFile="$TMP/hm.bin" --ReconFile="$TMP/hm_rec.yuv" >"$TMP/hm.log" 2>&1
  ffmpeg -loglevel error -y -i "$TMP/hm.bin" -c:v copy "$out"
}
aom() {  # aom <out.mp4> <extra aomenc args...>
  local out="$1"; shift
  PATH="$AOM_DLLS:$PATH" "$AOMENC" --codec=av1 --i420 --width=$W --height=$H \
    --fps=${FPS}/1 --limit=$N --cpu-used=8 --end-usage=q --cq-level=40 --passes=1 \
    "$@" --ivf -o "$TMP/aom.ivf" "$TMP/in.yuv" >"$TMP/aom.log" 2>&1
  ffmpeg -loglevel error -y -i "$TMP/aom.ivf" -c:v copy "$out"
}

echo "########## base (row-aligned / uniform) ##########"
jm  "$OUT/bnd_h264_slices.mp4" $((ROW_MB * 8))                                    # 8 MB-rows/slice
hm  "$OUT/bnd_h265_slices.mp4" --SliceMode=1 --SliceArgument=$((ROW_CTB * 2))     # 2 CTB-rows/slice
hm  "$OUT/bnd_h265_tiles.mp4"  --TileUniformSpacing=1 --NumTileColumnsMinus1=1 --NumTileRowsMinus1=1
aom "$OUT/bnd_av1_tiles.mp4"   --tile-columns=1 --tile-rows=1

echo "########## corner cases ##########"
jm  "$OUT/bnd_h264_slices_midrow.mp4" 200                                         # 200 MBs (not x52) -> mid-row
hm  "$OUT/bnd_h265_slices_midrow.mp4" --SliceMode=1 --SliceArgument=20            # 20 CTUs (not x13) -> mid-row
hm  "$OUT/bnd_h265_tiles_nonuniform.mp4" --TileUniformSpacing=0 \
    --NumTileColumnsMinus1=1 --TileColumnWidthArray="5" \
    --NumTileRowsMinus1=1 --TileRowHeightArray="3"
hm  "$OUT/bnd_h265_tiles_slices.mp4" --TileUniformSpacing=1 \
    --NumTileColumnsMinus1=1 --NumTileRowsMinus1=1 --SliceMode=3 --SliceArgument=2
aom "$OUT/bnd_av1_tiles_asym.mp4" --tile-columns=2 --tile-rows=0                  # 4 cols x 1 row

rm -rf "$TMP"

echo
echo "===================== verify ====================="
hdr() { ffmpeg -loglevel trace -i "$1" -c:v copy -bsf:v trace_headers -f null - 2>&1; }
mb_starts()  { hdr "$1" | grep -oE "first_mb_in_slice +[0-9]+ = [0-9]+"   | grep -oE "[0-9]+$" | head -5 \
               | awk -v r=$ROW_MB  '{printf " %d[r%dc%d%s]",$1,int($1/r),$1%r,($1%r?"*":"")}'; }
ctb_starts() { hdr "$1" | grep -oE "slice_segment_address +[01]+ = [0-9]+" | grep -oE "[0-9]+$" | head -5 \
               | awk -v r=$ROW_CTB '{printf " %d[r%dc%d%s]",$1,int($1/r),$1%r,($1%r?"*":"")}'; }
tilefields() { hdr "$1" | grep -oE "(tiles_enabled_flag|uniform_spacing_flag|num_tile_columns_minus1|num_tile_rows_minus1|column_width_minus1\[0\]|row_height_minus1\[0\]) +[01]+ = [0-9]+" | sort -u | sed 's/^/    /'; }
avtiles() { QT_QPA_PLATFORM=offscreen py -3.14 tools/_check_av1_tiles.py "$1" | grep "=>"; }

echo "[base] JM  h264 slices  :$(mb_starts  "$OUT/bnd_h264_slices.mp4")   (col0=row-aligned)"
echo "[base] HM  h265 slices  :$(ctb_starts "$OUT/bnd_h265_slices.mp4")   (col0=row-aligned)"
echo "[base] HM  h265 tiles   :"; tilefields "$OUT/bnd_h265_tiles.mp4"
echo "[base] AOM av1  tiles   :$(avtiles "$OUT/bnd_av1_tiles.mp4")"
echo "[corner] JM  h264 mid-row (*=mid-row):$(mb_starts  "$OUT/bnd_h264_slices_midrow.mp4")"
echo "[corner] HM  h265 mid-row (*=mid-row):$(ctb_starts "$OUT/bnd_h265_slices_midrow.mp4")"
echo "[corner] HM  h265 tiles non-uniform:"; tilefields "$OUT/bnd_h265_tiles_nonuniform.mp4"
echo "[corner] HM  h265 tiles+slices     :"; tilefields "$OUT/bnd_h265_tiles_slices.mp4"
echo "[corner] AOM av1 tiles asymmetric  :$(avtiles "$OUT/bnd_av1_tiles_asym.mp4")"
echo
ls -la "$OUT"/*.mp4
