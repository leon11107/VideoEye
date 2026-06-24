"""Read-only prototype: split AV1 mp4 packets (temporal units) into individual
coded frames by walking the OBU structure, and compare the per-coded-frame byte
sizes against an Elecard index.csv (decode order).

Goal: prove we can reconstruct Elecard's per-frame sizes (e.g. a TU that bundles
hidden altref frames -> several coded frames) WITHOUT touching the app.

Usage: py -3.14 tools/av1_obu_split.py <clip.mp4> [elecard_index.csv]
"""
import sys
import av

OBU_SEQUENCE_HEADER = 1
OBU_TEMPORAL_DELIMITER = 2
OBU_FRAME_HEADER = 3
OBU_TILE_GROUP = 4
OBU_METADATA = 5
OBU_FRAME = 6
OBU_REDUNDANT_FRAME_HEADER = 7
_NAMES = {1: "SEQ_HDR", 2: "TD", 3: "FRAME_HDR", 4: "TILE_GROUP", 5: "META",
          6: "FRAME", 7: "REDUNDANT_FH", 15: "PADDING"}


def _leb128(buf, pos):
    val = 0
    for i in range(8):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7f) << (i * 7)
        if not (b & 0x80):
            break
    return val, pos


def parse_obus(buf):
    """Yield (obu_type, obu_total_size, payload_offset, payload_size) for each
    OBU in a temporal unit. obu_total_size includes the header + size field."""
    pos = 0
    n = len(buf)
    while pos < n:
        start = pos
        b0 = buf[pos]
        obu_type = (b0 >> 3) & 0xf
        ext = (b0 >> 2) & 1
        has_size = (b0 >> 1) & 1
        pos += 1
        if ext:
            pos += 1
        if has_size:
            size, pos = _leb128(buf, pos)
        else:
            size = n - pos
        payload_off = pos
        pos += size
        yield obu_type, pos - start, payload_off, size


def show_existing_bit(buf, payload_off):
    """First bit of the frame header (show_existing_frame), reduced-still-picture
    assumed off (true for normal streams)."""
    return (buf[payload_off] >> 7) & 1


def split_packet(buf):
    """Group a TU's OBUs into coded frames. A coded frame is an OBU_FRAME, or an
    OBU_FRAME_HEADER (+ following tile groups). Returns a list of dicts with the
    coded-frame byte size and show_existing flag. Leading non-frame OBUs
    (temporal delimiter / sequence header / metadata) are attributed to the
    next coded frame, matching how the bytes sit in the stream."""
    frames = []
    pending = 0  # bytes of leading non-frame OBUs (TD/SEQ/META) for next frame
    cur = None
    for obu_type, total, poff, psize in parse_obus(buf):
        if obu_type in (OBU_FRAME, OBU_FRAME_HEADER, OBU_REDUNDANT_FRAME_HEADER):
            if obu_type == OBU_REDUNDANT_FRAME_HEADER and cur is not None:
                cur["size"] += total
                continue
            se = show_existing_bit(buf, poff)
            cur = {"size": total + pending, "show_existing": se,
                   "is_frame_obu": obu_type == OBU_FRAME}
            pending = 0
            frames.append(cur)
        elif obu_type == OBU_TILE_GROUP:
            if cur is not None:
                cur["size"] += total
            else:
                pending += total
        else:  # TD / SEQ_HDR / METADATA / PADDING -> attribute to next frame
            pending += total
    if pending and frames:
        frames[-1]["size"] += pending
    return frames


def main():
    clip = sys.argv[1]
    csv = sys.argv[2] if len(sys.argv) > 2 else None
    cont = av.open(clip)
    st = next(s for s in cont.streams if s.type == "video")
    coded = []
    n_pkt = 0
    for pkt in cont.demux(st):
        if pkt.size == 0:
            continue
        n_pkt += 1
        buf = bytes(pkt)
        fs = split_packet(buf)
        for f in fs:
            coded.append((pkt.size, f["size"], f["show_existing"], f["is_frame_obu"]))
    print(f"packets (TUs): {n_pkt}   coded frames: {len(coded)}")
    print(f"{'dec':>3} {'size':>8} {'show_exist':>10} {'frameOBU':>8}")
    for i, (psz, sz, se, fo) in enumerate(coded[:20]):
        print(f"{i:>3} {sz:>8} {se:>10} {fo:>8}")

    if csv:
        el = []
        with open(csv, encoding="utf-8", errors="replace") as fh:
            hdr = next(fh).split(",")
            zi = next(i for i, c in enumerate(hdr) if c.strip() == "size")
            for line in fh:
                p = line.split(",")
                if len(p) > zi and p[0].strip().isdigit():
                    el.append(int(p[zi]))
        print(f"\nElecard frames: {len(el)}   ours: {len(coded)}")
        ok = sum(1 for i in range(min(len(el), len(coded))) if el[i] == coded[i][1])
        print(f"size match (decode order): {ok}/{min(len(el), len(coded))}")
        for i in range(min(12, len(el), len(coded))):
            mark = "" if el[i] == coded[i][1] else "  <-- DIFF"
            print(f"  dec {i}: Elecard {el[i]:>8}  ours {coded[i][1]:>8}{mark}")


if __name__ == "__main__":
    main()
