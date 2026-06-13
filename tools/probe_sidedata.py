"""Probe PyAV side data availability: MOTION_VECTORS and VIDEO_ENC_PARAMS (per-block QP)."""

import struct
import sys

import av
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else r"tests\streams\bball_1080p_x264.mp4"

container = av.open(path)
stream = container.streams.video[0]
cc = stream.codec_context
cc.options = {"flags2": "+export_mvs", "export_side_data": "+venc_params"}

print(f"PyAV {av.__version__}, codec={cc.name}")

VENC_BLOCK_FIELDS = struct.Struct("<iiii i")  # src_x, src_y, w, h, delta_qp


def parse_venc_params(raw: bytes):
    # AVVideoEncParams header: nb_blocks(u32) pad blocks_offset(size_t) block_size(size_t)
    #                          type(i32) qp(i32) delta_qp[4][2](i32)
    nb_blocks, blocks_offset, block_size, ptype, qp = struct.unpack_from("<I4xQQiI", raw, 0)
    blocks = []
    for i in range(min(nb_blocks, 5)):
        off = blocks_offset + i * block_size
        blocks.append(VENC_BLOCK_FIELDS.unpack_from(raw, off))
    return nb_blocks, ptype, qp, blocks


for fi, frame in enumerate(container.decode(stream)):
    if fi >= 4:
        break
    print(f"--- frame {fi} pict_type={frame.pict_type} ---")
    for sd in frame.side_data:
        try:
            tname = str(sd.type)
        except Exception as e:
            tname = f"<enum err {e}>"
        raw = bytes(sd)
        print(f"  side_data type={tname} size={len(raw)}")
        if "MOTION" in tname.upper():
            try:
                arr = sd.to_ndarray()
                print(f"    mv ndarray dtype={arr.dtype} count={len(arr)}")
                print(f"    first: {arr[:2]}")
            except Exception as e:
                print(f"    to_ndarray failed: {e}")
        if "ENC_PARAMS" in tname.upper() or "VIDEO_ENC" in tname.upper():
            nb, ptype, qp, blocks = parse_venc_params(raw)
            print(f"    venc nb_blocks={nb} type={ptype} base_qp={qp}")
            print(f"    first blocks (src_x,src_y,w,h,delta_qp): {blocks}")

container.close()
print("done")
