"""Deterministic dump of all parser output for a stream, for refactor
equivalence checks. Prints a stable text representation of every NAL unit's
parsed syntax dict (plus get_slice_type), so a refactor can be proven to
produce byte-identical output via diff.

Run: py -3.14 tools/golden_parser.py <stream> [maxframes]
"""

import sys

sys.path.insert(0, ".")
from src.core.demuxer import Demuxer
from src.parsers.nalu_parser import NALUParser, H264NaluType, H265NaluType
from src.parsers.h264_parser import H264Parser
from src.parsers.h265_parser import H265Parser

path = sys.argv[1]
maxf = int(sys.argv[2]) if len(sys.argv) > 2 else 40


def fmt(d):
    return "{" + ", ".join(f"{k}={d[k]!r}" for k in d) + "}"


dem = Demuxer()
if not dem.open(path):
    print("OPEN FAILED")
    sys.exit(1)
si = dem.stream_info
is_h265 = si.codec_name.lower() in ("hevc", "h265")
npars = NALUParser(is_h265=is_h265, is_avc=si.is_avc,
                   nal_length_size=si.nal_length_size)
h264 = H264Parser()
h265 = H265Parser()

extradata = dem.get_extradata()
if extradata:
    for n in npars.parse(extradata):
        pass  # extradata NALUs parsed below via packets if present

lines = [f"codec={si.codec_name} h265={is_h265} avc={si.is_avc} nls={si.nal_length_size}"]
for idx in range(min(maxf, len(dem.frames))):
    data = dem.read_packet_data(idx)
    if not data:
        continue
    for nalu in npars.parse(data):
        t = nalu.nal_unit_type
        if is_h265:
            d = h265.parse(nalu)
            st = h265.get_slice_type(nalu) if nalu.is_slice() else None
        else:
            if t == H264NaluType.SPS:
                d = h264.parse_sps(nalu)
            elif t == H264NaluType.PPS:
                d = h264.parse_pps(nalu)
            elif t in (H264NaluType.SLICE_NON_IDR, H264NaluType.SLICE_IDR):
                d = h264.parse_slice_header(nalu)
            elif t == H264NaluType.SEI:
                d = h264.parse_sei(nalu)
            elif t == H264NaluType.AUD:
                d = h264.parse_aud(nalu)
            else:
                d = {"nal_unit_type": t}
            st = h264.get_slice_type(nalu) if nalu.is_slice() else None
        lines.append(f"f{idx} nal={t} slice_type={st} {fmt(d)}")

dem.close()
print("\n".join(lines))
