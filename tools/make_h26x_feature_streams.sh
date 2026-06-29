#!/usr/bin/env bash
# Generate short H.264 / H.265 clips that each FORCE a specific syntax feature
# our parsers currently skip (scaling matrices, transform_8x8 / PPS extension,
# weighted prediction, HRD + SEI, CAVLC, multi-slice, range extension, scaling
# lists, mastering-display SEI). Each clip is verified with FFmpeg's
# trace_headers bitstream filter so we only keep clips where the target syntax
# element actually appears -- giving an oracle for parser verification and an
# Elecard cross-check target.
#
# Usage: bash tools/make_h26x_feature_streams.sh
set -u
cd "$(dirname "$0")/.."
OUT=tests/streams/h26x_features
mkdir -p "$OUT"
SRC=tests/streams/bball_1080p_x264.mp4
N=16
SCALE="-vf scale=640:360"

pass=0; fail=0

enc264() {  # enc264 <out> "<ffmpeg opts...>"
  local out="$1"; shift
  ffmpeg -hide_banner -loglevel error -y -i "$SRC" -frames:v "$N" $SCALE \
    -c:v libx264 -pix_fmt yuv420p "$@" "$out"
}
enc265() {  # enc265 <out> "<ffmpeg opts...>"
  local out="$1"; shift
  ffmpeg -hide_banner -loglevel error -y -i "$SRC" -frames:v "$N" $SCALE \
    -c:v libx265 "$@" "$out"
}

verify() {  # verify <out> <regex> <label>   (regex matches the trace line)
  local out="$1" rx="$2" label="$3" line
  line=$(ffmpeg -hide_banner -i "$out" -c copy -bsf:v trace_headers -f null - 2>&1 \
         | grep -iE "$rx" | head -1)
  if [ -n "$line" ]; then
    printf "  PASS  %-26s %s\n" "$label" "$(echo "$line" | sed -E 's/^.*\] +//')"
    pass=$((pass+1))
  else
    printf "  FAIL  %-26s (%s not found)\n" "$label" "$rx"
    fail=$((fail+1))
  fi
}

echo "=== H.264 (libx264) ==="
# cqm=jvt puts the (non-flat) scaling matrices in the PPS -> exercises the
# high-profile PPS extension (pic_scaling_matrix) our parser doesn't read.
enc264 "$OUT/h264_scaling_cqm.mp4"   -profile:v high -x264-params "cqm=jvt"
verify "$OUT/h264_scaling_cqm.mp4"   "pic_scaling_matrix_present_flag.* = 1"  "PPS scaling matrices"

enc264 "$OUT/h264_transform8x8.mp4"  -profile:v high -8x8dct 1
verify "$OUT/h264_transform8x8.mp4"  "transform_8x8_mode_flag.* = 1"          "PPS transform_8x8 ext"

enc264 "$OUT/h264_weightp.mp4"       -profile:v high -bf 2 -x264-params "weightp=2:weightb=1"
verify "$OUT/h264_weightp.mp4"       "luma_weight_l0_flag"                    "slice pred_weight_table"

enc264 "$OUT/h264_hrd.mp4"           -profile:v high -b:v 2M -maxrate 2M -bufsize 2M -x264-params "nal-hrd=cbr"
verify "$OUT/h264_hrd.mp4"           "nal_hrd_parameters_present_flag.* = 1"  "VUI HRD + SEI"

enc264 "$OUT/h264_cavlc.mp4"         -profile:v main -coder 0
verify "$OUT/h264_cavlc.mp4"         "entropy_coding_mode_flag.* = 0"         "CAVLC slice"

enc264 "$OUT/h264_slices4.mp4"       -profile:v high -x264-params "slices=4" -bf 2
verify "$OUT/h264_slices4.mp4"       "slice_qp_delta"                         "slice header depth"

echo "=== H.265 (libx265) ==="
# scaling-list=default signals enabled-but-not-present (uses default lists);
# a custom matrix is needed for the actual scaling_list_data() body.
enc265 "$OUT/h265_scaling.mp4"       -x265-params "scaling-list=default"
verify "$OUT/h265_scaling.mp4"       "scaling_list_enabled_flag.* = 1"        "SPS scaling list enabled"

enc265 "$OUT/h265_rext422.mp4"       -pix_fmt yuv422p
verify "$OUT/h265_rext422.mp4"       "chroma_format_idc.* = 2"                "range ext 4:2:2"

enc265 "$OUT/h265_weightp.mp4"       -bf 3 -x265-params "weightp=1:weightb=1"
verify "$OUT/h265_weightp.mp4"       "luma_weight_l0_flag"                    "slice pred_weight_table"

enc265 "$OUT/h265_hrd.mp4"           -b:v 2M -maxrate 2M -bufsize 2M -x265-params "hrd=1:vbv-bufsize=2000:vbv-maxrate=2000"
verify "$OUT/h265_hrd.mp4"           "vui_hrd_parameters_present_flag.* = 1"  "VUI HRD + SEI"

enc265 "$OUT/h265_masterdisplay.mp4" -x265-params "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400"
verify "$OUT/h265_masterdisplay.mp4" "Mastering Display|mastering"            "mastering-display SEI"

echo
echo "RESULT: $pass passed, $fail failed.  Streams in $OUT"
