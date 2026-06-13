"""Debug: Demuxer frame extraction for a single file."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.demuxer import Demuxer

d = Demuxer()
ok = d.open(sys.argv[1])
print(f"open={ok} frames={len(d.frames)}")
if d.frames:
    for f in d.frames[:5]:
        print(f"  idx={f.index} pts={f.pts} dts={f.dts} size={f.size} key={f.is_keyframe}")
    data = d.read_packet_data(0)
    print(f"read_packet_data(0) -> {len(data)} bytes")
    data = d.read_packet_data(3)
    print(f"read_packet_data(3) -> {len(data)} bytes")
d.close()
