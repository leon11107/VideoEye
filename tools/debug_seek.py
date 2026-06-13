"""Debug: what packets/frames come back after seeking."""

import sys

import av

path = sys.argv[1]
target = int(sys.argv[2])

c = av.open(path)
s = c.streams.video[0]
s.thread_type = "AUTO"
print(f"== {path} seek pts={target}")
c.seek(target, stream=s)
n_pkt = n_frm = 0
for pkt in c.demux(s):
    n_pkt += 1
    frames = pkt.decode()
    n_frm += len(frames)
    print(f"pkt {n_pkt:3d} pts={pkt.pts} dts={pkt.dts} size={pkt.size}"
          f" key={pkt.is_keyframe} -> {len(frames)} frames {[f.pts for f in frames]}")
    if n_pkt >= 40:
        break
print(f"packets={n_pkt} frames={n_frm}")
c.close()
