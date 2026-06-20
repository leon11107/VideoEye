#!/usr/bin/env bash
# Generate short AV1 clips that each FORCE a specific AV1 feature on, for
# Elecard cross-checking. Every clip is verified with FFmpeg's trace_headers
# bitstream filter so we only hand over clips where the target OBU flag is
# actually set. Requires ffmpeg with libaom-av1 + trace_headers.
#
# Usage: bash tools/make_av1_feature_streams.sh
set -u
cd "$(dirname "$0")/.."
OUT=tests/streams/av1_features
mkdir -p "$OUT"
SRC=tests/streams/bball_1080p_x264.mp4          # natural-motion source
N=32                                            # frames per clip
COMMON="-cpu-used 8 -g 16 -crf 32 -b:v 0 -pix_fmt yuv420p"

enc() {  # enc <out> <aom-params> [extra ffmpeg opts...]
  local out="$1"; local params="$2"; shift 2
  ffmpeg -hide_banner -loglevel error -y -i "$SRC" -frames:v "$N" \
    -c:v libaom-av1 $COMMON "$@" \
    ${params:+-aom-params "$params"} "$out"
}

verify() {  # verify <out> <regex> <label>
  local out="$1"; local rx="$2"; local label="$3"
  local line
  line=$(ffmpeg -hide_banner -i "$out" -c copy -bsf:v trace_headers -f null - 2>&1 \
         | grep -iE "$rx" | head -1)
  if [ -n "$line" ]; then
    printf "  PASS  %-26s %s\n" "$label" "$(echo "$line" | sed -E 's/.*\] +//')"
  else
    printf "  FAIL  %-26s (flag not found)\n" "$label"
  fi
}

# Ground-truth per-block inspector from a CONFIG_INSPECTION libaom build. Used
# to measure actual palette usage (a header flag only says it is *allowed*).
INSP="${VEYE_AOM_INSPECT:-/c/Users/llw/Desktop/aom/aom_build_insp/inspect.exe}"

verify_palette() {  # verify_palette <out> -- report keyframe palette coverage %
  local out="$1"
  [ -x "$INSP" ] || { printf "  SKIP  palette coverage (no inspect.exe)\n"; return; }
  # Repo-relative temps: ffmpeg/inspect/py are all native and resolve them
  # against the same CWD (a bash '>' redirect to /tmp would land in MSYS /tmp,
  # which Windows python cannot read).
  ffmpeg -hide_banner -loglevel error -y -i "$out" -c copy -f ivf ._plt.ivf
  "$INSP" ._plt.ivf -plt --limit=1 > ._plt.json 2>/dev/null
  py -3.14 - <<'PY'
import json
d=json.load(open("._plt.json")); g=d[0]["palette"]; c=[v for r in g for v in r]
nz=sum(1 for v in c if v>0)
print(f"  INFO  keyframe palette coverage: {nz}/{len(c)} = {100*nz/max(1,len(c)):.1f}%")
PY
  rm -f ._plt.ivf ._plt.json
}

echo "### 1. 128x128 superblock"
enc "$OUT/av1_sb128.mp4" "sb-size=128:tile-columns=0:tile-rows=0"
verify "$OUT/av1_sb128.mp4" "use_128x128_superblock +1 += 1" "use_128x128_superblock"

echo "### 2. segmentation (variance AQ -> segment map)"
enc "$OUT/av1_segmentation.mp4" "aq-mode=1:deltaq-mode=0"
verify "$OUT/av1_segmentation.mp4" "segmentation_enabled +1 += 1" "segmentation_enabled"

echo "### 3. screen content + palette (synthetic flat-color source)"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=0xF0F0F0:s=1280x720:r=25:d=1.6" \
  -f lavfi -i "color=c=red:s=220x220:d=1.6" \
  -filter_complex "[0:v]drawbox=0:0:300:720:color=blue:t=fill,drawbox=980:0:300:720:color=lime:t=fill,drawbox=320:240:640:240:color=black:t=fill[bg];[bg][1:v]overlay=x='mod(n*36,1060)':y=250,format=yuv420p" \
  -frames:v "$N" -c:v libaom-av1 $COMMON \
  -aom-params "tune-content=screen:enable-palette=1:enable-intrabc=1" \
  "$OUT/av1_palette_screen.mp4"
verify "$OUT/av1_palette_screen.mp4" "allow_screen_content_tools +1 += 1" "allow_screen_content_tools"
verify_palette "$OUT/av1_palette_screen.mp4"

echo "### 3b. STRONG palette: few-color mosaic (near-lossless, single GOP)"
# A deterministic discrete-color mosaic: every block holds only 3-5 distinct
# colors with hard edges, so palette is the cheapest coding mode almost
# everywhere (~99.9% of keyframe MI cells vs ~0.1% for the natural clip above).
# Single GOP (-g 1000) so order_hints stay unique -> clean frame mapping.
# cpu-used 4 (palette RD is skipped at the fastest presets).
MVF="geq=lum='(mod(floor((X+N*4)/12)+floor(Y/12)\,8))*26+20':cb='(mod(floor(X/24)\,4))*55+40':cr='(mod(floor(Y/24)\,4))*55+40',format=yuv420p"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "color=black:s=1280x720:r=25" \
  -frames:v "$N" -vf "$MVF" -c:v libaom-av1 -cpu-used 4 -g 1000 -crf 12 -b:v 0 \
  -aom-params "tune-content=screen:enable-palette=1:enable-intrabc=1" \
  "$OUT/av1_palette_strong.mp4"
verify "$OUT/av1_palette_strong.mp4" "allow_screen_content_tools +1 += 1" "allow_screen_content_tools"
verify_palette "$OUT/av1_palette_strong.mp4"

echo "### 4. superres (coded smaller, upscaled on output) -- via SVT-AV1"
# libaom in this build exposes no superres option, so use SVT-AV1, which signals
# superres in the same AV1 bitstream syntax Elecard parses.
ffmpeg -hide_banner -loglevel error -y -i "$SRC" -frames:v "$N" \
  -c:v libsvtav1 -preset 8 -crf 35 -g 16 -pix_fmt yuv420p \
  -svtav1-params "superres-mode=2:superres-denom=12:superres-kf-denom=12" \
  "$OUT/av1_superres.mp4"
verify "$OUT/av1_superres.mp4" "use_superres +1 += 1" "use_superres(frame)"

echo "### 5. loop restoration (needs slower preset; cpu-used 8 forces it off)"
enc "$OUT/av1_restoration.mp4" "" -enable-restoration 1 -cpu-used 4
verify "$OUT/av1_restoration.mp4" "enable_restoration +1 += 1" "enable_restoration"

echo "### 6. film grain (synthesis)"
enc "$OUT/av1_filmgrain.mp4" "film-grain-test=1"
verify "$OUT/av1_filmgrain.mp4" "film_grain_params_present +1 += 1" "film_grain_params_present"

echo "### 7. filter-intra"
enc "$OUT/av1_filterintra.mp4" "enable-filter-intra=1" -enable-filter-intra 1
verify "$OUT/av1_filterintra.mp4" "enable_filter_intra +1 += 1" "enable_filter_intra"

echo
echo "Output clips:"
ls -la "$OUT"/*.mp4
