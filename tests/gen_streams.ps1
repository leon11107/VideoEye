# Generate the H.264/HEVC/AV1 test stream matrix from raw YUV sources.
# Usage: powershell -File tests\gen_streams.ps1

$ErrorActionPreference = "Continue"
$yuv1080 = "C:\Users\llw\Desktop\dataset\pattern\classB\BasketballDrive_1920x1080_25fps_500frm_yuv420.dat"
$yuv2160 = "C:\Users\llw\Desktop\dataset\pattern\classA\BridgeViewTraffic_3840x2160_30_8b_709_30fps_300frm_yuv420.dat"
$out = Join-Path $PSScriptRoot "streams"
New-Item -ItemType Directory -Force -Path $out | Out-Null

$in1080 = "-f rawvideo -pix_fmt yuv420p -s 1920x1080 -r 25 -i `"$yuv1080`""

$jobs = @(
    # name, input, encode args
    @("h264_1080p_default.mp4",   $in1080, "-frames:v 60 -c:v libx264 -preset medium -crf 26 -g 25 -bf 3"),
    @("h264_1078_unaligned.mp4",  $in1080, "-frames:v 30 -vf crop=1918:1078:0:0 -c:v libx264 -crf 28"),
    @("h264_176x144_tiny.mp4",    $in1080, "-frames:v 30 -vf scale=176:144 -c:v libx264 -crf 24"),
    @("h264_baseline_cavlc.mp4",  $in1080, "-frames:v 30 -c:v libx264 -profile:v baseline -crf 28"),
    @("h264_intra_only.mp4",      $in1080, "-frames:v 20 -c:v libx264 -g 1 -crf 28"),
    @("h264_slices4.mp4",         $in1080, "-frames:v 30 -c:v libx264 -crf 28 -x264-params slices=4"),
    @("h264_aq_off.mp4",          $in1080, "-frames:v 30 -c:v libx264 -crf 28 -x264-params aq-mode=0"),
    @("h264_qp0_lossless.mp4",    $in1080, "-frames:v 10 -vf scale=640:360 -c:v libx264 -qp 0"),
    @("h264_qp51_worst.mp4",      $in1080, "-frames:v 30 -c:v libx264 -qp 51"),
    @("h264_annexb.264",          $in1080, "-frames:v 30 -c:v libx264 -crf 28 -f h264"),
    @("h264_mpegts.ts",           $in1080, "-frames:v 30 -c:v libx264 -crf 28 -f mpegts"),
    @("h264_high10.mp4",          $in1080, "-frames:v 20 -c:v libx264 -pix_fmt yuv420p10le -crf 28"),
    @("hevc_1080p.mp4",           $in1080, "-frames:v 30 -c:v libx265 -crf 28 -tag:v hvc1"),
    @("hevc_slices4.mp4",         $in1080, "-frames:v 30 -c:v libx265 -crf 28 -tag:v hvc1 -x265-params slices=4"),
    @("av1_1080p.mp4",            $in1080, "-frames:v 30 -c:v libsvtav1 -crf 40")
)

foreach ($j in $jobs) {
    $name, $inArgs, $encArgs = $j
    $dst = Join-Path $out $name
    if (Test-Path $dst) { Write-Host "skip  $name"; continue }
    Write-Host "gen   $name"
    $cmd = "ffmpeg -y $inArgs $encArgs `"$dst`""
    Invoke-Expression "$cmd 2>`$null"
    if (-not (Test-Path $dst)) { Write-Host "FAIL  $name" -ForegroundColor Red }
}

# 4K from UHD source
$dst4k = Join-Path $out "h264_2160p.mp4"
if (-not (Test-Path $dst4k)) {
    Write-Host "gen   h264_2160p.mp4"
    Invoke-Expression "ffmpeg -y -f rawvideo -pix_fmt yuv420p -s 3840x2160 -r 30 -i `"$yuv2160`" -frames:v 20 -c:v libx264 -preset fast -crf 28 `"$dst4k`" 2>`$null"
}

Write-Host "done. files:"
Get-ChildItem $out | Select-Object Name, Length
