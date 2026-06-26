"""Read-only EVALUATION: can we extract per-coded-frame order_hint / frame_type
/ show_frame in pure Python (no native change), to drive a decode-order frame
model? Parses the AV1 sequence header once, then each coded frame's
uncompressed header up to order_hint (shallow -- before frame size / refs), and
verifies against an Elecard headers.csv. show_existing frames carry no
order_hint in the header (they reference a DPB slot); the app will instead map
them by display-event order to the sidecar (own_poc), so here we only check
real coded frames' order_hint.

Usage: py -3.14 tools/av1_orderhint_eval.py <clip.mp4> <headers.csv>
"""
import re
import sys
import av

OBU_SEQUENCE_HEADER, OBU_FRAME_HEADER, OBU_FRAME, OBU_REDUNDANT_FH = 1, 3, 6, 7
KEY_FRAME, INTER_FRAME, INTRA_ONLY, SWITCH_FRAME = 0, 1, 2, 3
SELECT_SCREEN_CONTENT_TOOLS = 2
SELECT_INTEGER_MV = 2
_FT = {0: "KEY_FRAME", 1: "INTER_FRAME", 2: "INTRA_ONLY_FRAME", 3: "SWITCH_FRAME"}


class BR:
    """MSB-first bit reader."""
    def __init__(self, data, pos=0):
        self.d, self.byte, self.bit = data, pos, 0

    def f(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.byte] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit, self.byte = 0, self.byte + 1
        return v


def _leb128(buf, pos):
    val = 0
    for i in range(8):
        b = buf[pos]; pos += 1
        val |= (b & 0x7f) << (i * 7)
        if not (b & 0x80):
            break
    return val, pos


def iter_obus(buf):
    pos, n = 0, len(buf)
    while pos < n:
        b0 = buf[pos]
        otype = (b0 >> 3) & 0xf
        ext = (b0 >> 2) & 1
        has_size = (b0 >> 1) & 1
        pos += 1 + (1 if ext else 0)
        if has_size:
            size, pos = _leb128(buf, pos)
        else:
            size = n - pos
        yield otype, pos, size
        pos += size


def parse_seq_header(buf, off):
    """Parse only the fields needed to reach order_hint / force flags."""
    r = BR(buf, off)
    s = {}
    r.f(3)                                    # seq_profile
    r.f(1)                                    # still_picture
    reduced = r.f(1)
    s["reduced_still"] = reduced
    decoder_model = 0
    if reduced:
        r.f(5)                                # seq_level_idx[0]
    else:
        timing = r.f(1)
        if timing:
            r.f(32); r.f(32)                  # num_units_in_display_tick, time_scale
            equal = r.f(1)
            if equal:
                # uvlc num_ticks_per_picture_minus_1
                lead = 0
                while r.f(1) == 0:
                    lead += 1
                if lead:
                    r.f(lead)
            decoder_model = r.f(1)
            if decoder_model:
                raise NotImplementedError("decoder_model_info_present")
        idd = r.f(1)                          # initial_display_delay_present_flag
        opcnt = r.f(5)
        for i in range(opcnt + 1):
            r.f(12)                           # operating_point_idc
            lvl = r.f(5)
            if lvl > 7:
                r.f(1)                        # seq_tier
            if decoder_model:
                pass
            if idd:
                if r.f(1):
                    r.f(4)
    fwb = r.f(4) + 1
    fhb = r.f(4) + 1
    r.f(fwb)                                  # max_frame_width_minus_1
    r.f(fhb)                                  # max_frame_height_minus_1
    frame_id = 0 if reduced else r.f(1)
    s["frame_id_present"] = frame_id
    s["id_len"] = 0
    if frame_id:
        delta = r.f(4) + 2
        addl = r.f(3) + 1
        s["id_len"] = delta + addl
    r.f(1)                                    # use_128x128_superblock
    r.f(1)                                    # enable_filter_intra
    r.f(1)                                    # enable_intra_edge_filter
    if reduced:
        s["enable_order_hint"] = 0
        s["OrderHintBits"] = 0
        s["force_screen"] = 0
        s["force_intmv"] = 2
    else:
        r.f(1); r.f(1); r.f(1); r.f(1)        # interintra, masked, warped, dual
        eoh = r.f(1)
        s["enable_order_hint"] = eoh
        if eoh:
            r.f(1); r.f(1)                    # jnt_comp, ref_frame_mvs
        if r.f(1):                            # seq_choose_screen_content_tools
            s["force_screen"] = SELECT_SCREEN_CONTENT_TOOLS
        else:
            s["force_screen"] = r.f(1)
        if s["force_screen"] > 0:
            if r.f(1):                        # seq_choose_integer_mv
                s["force_intmv"] = SELECT_INTEGER_MV
            else:
                s["force_intmv"] = r.f(1)
        else:
            s["force_intmv"] = SELECT_INTEGER_MV
        if eoh:
            s["OrderHintBits"] = r.f(3) + 1
        else:
            s["OrderHintBits"] = 0
    return s


def parse_frame_header(buf, off, seq):
    """Parse uncompressed_header up to order_hint. Returns dict or None for
    show_existing. (reduced_still not handled -- not used by normal clips.)"""
    r = BR(buf, off)
    show_existing = r.f(1)
    if show_existing:
        idx = r.f(3)
        return {"show_existing": 1, "frame_to_show": idx}
    frame_type = r.f(2)
    show_frame = r.f(1)
    intra = frame_type in (KEY_FRAME, INTRA_ONLY)
    if not show_frame:
        r.f(1)                                # showable_frame
    if frame_type == SWITCH_FRAME or (frame_type == KEY_FRAME and show_frame):
        pass                                  # error_resilient_mode = 1 (implicit)
    else:
        r.f(1)                                # error_resilient_mode
    r.f(1)                                     # disable_cdf_update
    if seq["force_screen"] == SELECT_SCREEN_CONTENT_TOOLS:
        allow_sc = r.f(1)
    else:
        allow_sc = seq["force_screen"]
    if allow_sc:
        if seq["force_intmv"] == SELECT_INTEGER_MV:
            r.f(1)                            # force_integer_mv
    if seq["frame_id_present"]:
        r.f(seq["id_len"])                    # current_frame_id
    if frame_type == SWITCH_FRAME:
        frame_size_override = 1
    else:
        frame_size_override = r.f(1)
    order_hint = r.f(seq["OrderHintBits"])
    return {"show_existing": 0, "frame_type": frame_type,
            "show_frame": show_frame, "order_hint": order_hint}


def main():
    clip, csv = sys.argv[1], sys.argv[2]
    cont = av.open(clip)
    st = next(s for s in cont.streams if s.type == "video")
    seq = None
    coded = []
    for pkt in cont.demux(st):
        if pkt.size == 0:
            continue
        buf = bytes(pkt)
        for otype, poff, psize in iter_obus(buf):
            if otype == OBU_SEQUENCE_HEADER:
                seq = parse_seq_header(buf, poff)
            elif otype in (OBU_FRAME, OBU_FRAME_HEADER):
                fh = parse_frame_header(buf, poff, seq)
                coded.append(fh)

    # Elecard ground truth from headers.csv (real coded frames in decode order).
    el = []
    cur = None
    for line in open(csv, encoding="utf-8", errors="replace"):
        p = line.split(",")
        if len(p) < 3:
            continue
        name, val = p[1].strip(), p[2].strip()
        if name == "show_existing_frame":
            cur = {"se": int(val), "ft": None, "sf": None, "oh": None}
            el.append(cur)
        elif cur is None:
            continue
        elif name == "frame_type":
            cur["ft"] = int(re.match(r"\s*(\d+)", val).group(1))
        elif name == "show_frame" and cur["sf"] is None:
            cur["sf"] = int(val)
        elif name == "order_hint" and cur["oh"] is None:
            cur["oh"] = int(val)

    print(f"seq: {seq}")
    print(f"our coded frames: {len(coded)}   Elecard header frames: {len(el)}")
    oh_ok = oh_n = ft_ok = sf_ok = se_ok = 0
    n = min(len(coded), len(el))
    for i in range(n):
        o, e = coded[i], el[i]
        se_ok += (o["show_existing"] == e["se"])
        if not o["show_existing"] and not e["se"]:
            oh_n += 1
            oh_ok += (o["order_hint"] == e["oh"])
            ft_ok += (o["frame_type"] == e["ft"])
            sf_ok += (o["show_frame"] == e["sf"])
    print(f"show_existing match: {se_ok}/{n}")
    print(f"order_hint match (real frames): {oh_ok}/{oh_n}")
    print(f"frame_type match: {ft_ok}/{oh_n}    show_frame match: {sf_ok}/{oh_n}")
    print("\nfirst 12 (ours vs Elecard):")
    for i in range(min(12, n)):
        o, e = coded[i], el[i]
        if o["show_existing"]:
            print(f"  {i:>2} show_existing -> map_idx {o['frame_to_show']}  "
                  f"(Elecard se={e['se']})")
        else:
            mk = "" if o["order_hint"] == e["oh"] else "  <-- DIFF"
            print(f"  {i:>2} {_FT[o['frame_type']]:>16} show={o['show_frame']} "
                  f"oh={o['order_hint']}   E: oh={e['oh']} ft={e['ft']} "
                  f"sf={e['sf']}{mk}")


if __name__ == "__main__":
    main()
