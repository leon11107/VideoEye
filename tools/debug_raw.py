"""Debug: emulate Demuxer.open step by step on a raw stream."""

import sys

import av

path = sys.argv[1]
c = av.open(path)
s = next(st for st in c.streams if st.type == 'video')
print("opened, codec:", s.codec_context.name)

try:
    c.seek(0)
    print("seek(0) ok")
except Exception as e:
    print(f"seek(0) failed: {e}")

n = 0
for pkt in c.demux(s):
    n += 1
print("packets after seek attempt:", n)
c.close()

# second pass: no seek at all
c = av.open(path)
s = next(st for st in c.streams if st.type == 'video')
n = 0
for pkt in c.demux(s):
    n += 1
print("packets without seek:", n)
c.close()
