"""Report the AV1 tile_info() of a stream's first frame (used by
make_boundary_streams.sh to confirm a generated AV1 stream actually has tiles)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.demuxer import Demuxer  # noqa: E402
from src.parsers.av1_parser import Av1Parser  # noqa: E402


def find_tile_info(d):
    for k, v in d.items():
        if k == "tile_info()" and isinstance(v, dict):
            return v
        if isinstance(v, dict):
            r = find_tile_info(v)
            if r is not None:
                return r
    return None


def main(path):
    dmx = Demuxer()
    assert dmx.open(path), f"cannot open {path}"
    parser = Av1Parser()
    ex = dmx.get_extradata()
    if ex and len(ex) > 4:
        parser.parse(ex[4:])  # seed sequence header from av1C
    ti = None
    for i in range(min(len(dmx.frames), 3)):
        pkt = dmx.read_packet_data(i)
        for o in parser.parse(pkt):
            ti = find_tile_info(o["syntax"])
            if ti:
                break
        if ti:
            break
    dmx.close()
    if ti is None:
        print("  tile_info: NOT FOUND")
        sys.exit(1)
    print("  tile_info:")
    for k, v in ti.items():
        print(f"    {k} = {v}")
    uniform = ti.get("uniform_tile_spacing_flag", 1)
    if uniform:
        col_inc = ti.get("increment_tile_cols_log2 (equal 1 count)", 0)
        row_inc = ti.get("increment_tile_rows_log2 (equal 1 count)", 0)
        cols, rows = 1 << col_inc, 1 << row_inc
    else:
        cols = sum(1 for k in ti if k.startswith("width_in_sbs_minus_1"))
        rows = sum(1 for k in ti if k.startswith("height_in_sbs_minus_1"))
    print(f"  => ~{cols} tile col(s) x {rows} tile row(s)")
    if cols * rows <= 1:
        print("  WARNING: stream has only a single tile")
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1])
