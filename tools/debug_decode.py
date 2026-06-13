"""Debug: packet/frame flow for problem streams."""

import sys

import av

path = sys.argv[1]
container = av.open(path)
stream = container.streams.video[0]
stream.codec_context.options = {
    "flags2": "+export_mvs", "export_side_data": "+venc_params",
}
stream.thread_type = "AUTO"

print(f"== {path}")
print(f"codec={stream.codec_context.name} threads default")

n_pkt = n_frm = 0
for packet in container.demux(stream):
    n_pkt += 1
    frames = packet.decode()
    flush = packet.dts is None and packet.pts is None and packet.size == 0
    print(f"pkt {n_pkt:3d} pts={packet.pts} dts={packet.dts} size={packet.size}"
          f" key={packet.is_keyframe} flush={flush} -> {len(frames)} frames"
          f" {[f.pts for f in frames]}")
    n_frm += len(frames)
print(f"total packets={n_pkt} frames={n_frm}")

# Now test seek-back after EOF
print("-- seek back to 0 after EOF --")
try:
    container.seek(0, stream=stream)
    cnt = 0
    for packet in container.demux(stream):
        for frame in packet.decode():
            cnt += 1
            if cnt >= 3:
                break
        if cnt >= 3:
            break
    print(f"re-decoded {cnt} frames after seek")
except Exception as e:
    print(f"seek-back failed: {type(e).__name__}: {e}")
container.close()
