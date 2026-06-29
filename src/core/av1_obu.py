"""AV1 OBU / temporal-unit parsing for the decode-order frame model.

An AV1 sample in MP4 (one PyAV packet) is a *temporal unit* that can bundle
several coded frames -- hidden alt-ref frames (show_frame = 0) plus the shown
frame, and `show_existing_frame` events that re-display a previously decoded
hidden frame. The block analysis and Elecard both work in decode order, one
entry per coded frame, so this module splits each packet into its coded frames
with per-frame byte size and the header fields (order_hint, frame_type,
show_frame, show_existing) needed to drive that model.

Only the shallow part of the uncompressed header (up to order_hint) is parsed;
that is enough to identify and order every coded frame. Verified bit-exact
against Elecard headers.csv across several clips (see tools/av1_orderhint_eval.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

OBU_SEQUENCE_HEADER = 1
OBU_TEMPORAL_DELIMITER = 2
OBU_FRAME_HEADER = 3
OBU_TILE_GROUP = 4
OBU_METADATA = 5
OBU_FRAME = 6
OBU_REDUNDANT_FRAME_HEADER = 7

KEY_FRAME, INTER_FRAME, INTRA_ONLY_FRAME, SWITCH_FRAME = 0, 1, 2, 3
_SELECT_SCREEN_CONTENT_TOOLS = 2
_SELECT_INTEGER_MV = 2


@dataclass
class Av1CodedFrame:
    """One coded frame inside a temporal unit (decode order)."""
    byte_off: int          # offset of this frame's bytes within the packet
    size: int              # this frame's byte size (bytes partition the packet)
    show_existing: bool
    show_frame: Optional[bool]      # None for show_existing
    frame_type: Optional[int]       # 0..3, None for show_existing
    order_hint: Optional[int]       # None for show_existing
    frame_to_show: Optional[int]    # show_existing only: DPB slot index
    refresh_frame_flags: int = 0    # 8-bit DPB refresh mask
    # The 7 signaled reference DPB-slot indices (ref_frame_idx[0..6] =
    # LAST/LAST2/LAST3/GOLDEN/BWDREF/ALTREF2/ALTREF). None for intra frames and
    # when refs were not parsed (short-signaling / frame-id present).
    ref_frame_idx: Optional[list] = None
    # Display rank (0-based output ordinal). A hidden frame and the
    # show_existing that later outputs it share the same rank. Filled by
    # assign_display_ranks(). None until then (and for never-shown frames).
    display_rank: Optional[int] = None
    # Resolved reference frames as decode indices, split into the forward
    # (LAST..GOLDEN -> l0) and backward (BWDREF..ALTREF -> l1) groups. Filled by
    # assign_display_ranks() from the DPB state. [] for intra; None when refs
    # were not parsed (caller should fall back).
    ref_decode_l0: Optional[list] = None
    ref_decode_l1: Optional[list] = None

    @property
    def displays(self) -> bool:
        """Whether this coded frame produces an output picture (a display
        event): a shown frame or a show_existing_frame."""
        return bool(self.show_existing) or bool(self.show_frame)


class _BitReader:
    """MSB-first bit reader over a bytes buffer."""
    __slots__ = ("d", "byte", "bit")

    def __init__(self, data, pos=0):
        self.d, self.byte, self.bit = data, pos, 0

    def f(self, n: int) -> int:
        v = 0
        d = self.d
        for _ in range(n):
            v = (v << 1) | ((d[self.byte] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.byte += 1
        return v


def _leb128(buf, pos):
    val = 0
    for i in range(8):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7f) << (i * 7)
        if not (b & 0x80):
            break
    return val, pos


def _iter_obus(buf):
    """Yield (obu_type, payload_offset, total_obu_bytes) for each OBU."""
    pos, n = 0, len(buf)
    while pos < n:
        start = pos
        b0 = buf[pos]
        obu_type = (b0 >> 3) & 0xf
        ext = (b0 >> 2) & 1
        has_size = (b0 >> 1) & 1
        pos += 1 + (1 if ext else 0)
        if has_size:
            size, pos = _leb128(buf, pos)
        else:
            size = n - pos
        payload_off = pos
        pos += size
        yield obu_type, payload_off, pos - start


def parse_sequence_header(buf, off) -> dict:
    """Parse the sequence header up to the fields needed for frame-header
    parsing (order_hint bits, force flags, frame-id length). Returns a dict.
    Raises NotImplementedError for the rare decoder-model path (not produced by
    normal encoders)."""
    r = _BitReader(buf, off)
    s = {}
    r.f(3)                                     # seq_profile
    r.f(1)                                     # still_picture
    reduced = r.f(1)
    s["reduced_still"] = reduced
    if reduced:
        r.f(5)                                 # seq_level_idx[0]
    else:
        if r.f(1):                             # timing_info_present_flag
            r.f(32); r.f(32)                   # display tick, time_scale
            if r.f(1):                         # equal_picture_interval
                lead = 0
                while r.f(1) == 0:
                    lead += 1
                    if lead > 31:
                        break
                if lead:
                    r.f(lead)
            if r.f(1):                          # decoder_model_info_present_flag
                raise NotImplementedError("decoder_model_info_present")
        idd = r.f(1)                            # initial_display_delay_present
        opcnt = r.f(5)
        for _ in range(opcnt + 1):
            r.f(12)                             # operating_point_idc
            if r.f(5) > 7:                      # seq_level_idx
                r.f(1)                          # seq_tier
            if idd and r.f(1):
                r.f(4)
    fwb = r.f(4) + 1
    fhb = r.f(4) + 1
    r.f(fwb); r.f(fhb)                          # max frame width/height minus 1
    frame_id = 0 if reduced else r.f(1)
    s["frame_id_present"] = frame_id
    s["id_len"] = 0
    if frame_id:
        s["id_len"] = (r.f(4) + 2) + (r.f(3) + 1)
    s["use_128x128"] = r.f(1)                   # use_128x128_superblock
    r.f(1); r.f(1)                              # filter_intra, intra_edge
    if reduced:
        s["enable_order_hint"] = 0
        s["OrderHintBits"] = 0
        s["force_screen"] = 0
        s["force_intmv"] = _SELECT_INTEGER_MV
    else:
        r.f(1); r.f(1); r.f(1); r.f(1)         # interintra, masked, warped, dual
        eoh = r.f(1)
        s["enable_order_hint"] = eoh
        if eoh:
            r.f(1); r.f(1)                      # jnt_comp, ref_frame_mvs
        s["force_screen"] = (_SELECT_SCREEN_CONTENT_TOOLS
                             if r.f(1) else r.f(1))
        if s["force_screen"] > 0:
            s["force_intmv"] = _SELECT_INTEGER_MV if r.f(1) else r.f(1)
        else:
            s["force_intmv"] = _SELECT_INTEGER_MV
        s["OrderHintBits"] = (r.f(3) + 1) if eoh else 0
    return s


def _parse_frame_header(buf, off, seq) -> dict:
    """Parse the uncompressed header up to order_hint. Returns a dict of
    {show_existing, frame_type, show_frame, order_hint, frame_to_show}."""
    r = _BitReader(buf, off)
    if r.f(1):                                  # show_existing_frame
        return {"show_existing": 1, "frame_to_show": r.f(3),
                "frame_type": None, "show_frame": None, "order_hint": None,
                "ref_frame_idx": None}
    frame_type = r.f(2)
    show_frame = r.f(1)
    if not show_frame:
        r.f(1)                                  # showable_frame
    if frame_type == SWITCH_FRAME or (frame_type == KEY_FRAME and show_frame):
        err_res = 1
    else:
        err_res = r.f(1)                        # error_resilient_mode
    r.f(1)                                       # disable_cdf_update
    if seq["force_screen"] == _SELECT_SCREEN_CONTENT_TOOLS:
        allow_sc = r.f(1)
    else:
        allow_sc = seq["force_screen"]
    if allow_sc and seq["force_intmv"] == _SELECT_INTEGER_MV:
        r.f(1)                                   # force_integer_mv
    if seq["frame_id_present"]:
        r.f(seq["id_len"])                       # current_frame_id
    if frame_type != SWITCH_FRAME and not seq["reduced_still"]:
        r.f(1)                                   # frame_size_override_flag
    order_hint = r.f(seq["OrderHintBits"])
    # primary_ref_frame, then the DPB refresh mask (no decoder-model path).
    intra = frame_type in (KEY_FRAME, INTRA_ONLY_FRAME)
    if not (intra or err_res):
        r.f(3)                                   # primary_ref_frame
    if frame_type == SWITCH_FRAME or (frame_type == KEY_FRAME and show_frame):
        refresh = 0xFF
    else:
        refresh = r.f(8)                         # refresh_frame_flags
    # Reference indices: parse far enough to read ref_frame_idx[0..6] so the
    # caller can resolve this frame's references against the DPB. Only inter
    # frames carry them.
    ref_frame_idx = None
    if not intra:
        # error_resilient + order_hint => 8 ref_order_hint[] precede the refs.
        if err_res and seq["enable_order_hint"]:
            for _ in range(8):
                r.f(seq["OrderHintBits"])
        short = r.f(1) if seq["enable_order_hint"] else 0
        # short signaling needs set_frame_refs() and frame-id present adds
        # delta_frame_id bits we cannot size here; in those rare cases leave
        # refs unresolved (None) so the caller falls back.
        if not short and not seq["frame_id_present"]:
            ref_frame_idx = [r.f(3) for _ in range(7)]
    return {"show_existing": 0, "frame_to_show": None,
            "frame_type": frame_type, "show_frame": show_frame,
            "order_hint": order_hint, "refresh_frame_flags": refresh,
            "ref_frame_idx": ref_frame_idx}


def split_temporal_unit(buf, seq: Optional[dict]):
    """Split a temporal unit (one packet's bytes) into its coded frames.

    Returns (frames, seq) where `frames` is a list of Av1CodedFrame and `seq`
    is the (possibly newly parsed) sequence-header state to carry into the next
    packet. Leading non-frame OBUs (temporal delimiter / sequence header /
    metadata) are attributed to the next coded frame so the per-frame byte sizes
    partition the packet exactly (matching Elecard's per-frame size)."""
    raw = []                 # (fields, size) before assigning byte offsets
    pending = 0
    cur = None
    for obu_type, poff, total in _iter_obus(buf):
        if obu_type == OBU_SEQUENCE_HEADER:
            try:
                seq = parse_sequence_header(buf, poff)
            except (NotImplementedError, IndexError):
                seq = seq  # keep prior state; frame parse will no-op below
            pending += total
        elif obu_type in (OBU_FRAME, OBU_FRAME_HEADER, OBU_REDUNDANT_FRAME_HEADER):
            if obu_type == OBU_REDUNDANT_FRAME_HEADER and cur is not None:
                cur[1] += total
                continue
            if seq is not None:
                try:
                    fh = _parse_frame_header(buf, poff, seq)
                except (IndexError, KeyError):
                    fh = None
            else:
                fh = None
            cur = [fh, total + pending]
            pending = 0
            raw.append(cur)
        elif obu_type == OBU_TILE_GROUP:
            if cur is not None:
                cur[1] += total
            else:
                pending += total
        else:                                    # TD / METADATA / PADDING
            pending += total
    if pending and raw:
        raw[-1][1] += pending

    frames = []
    byte_off = 0
    for fh, size in raw:
        if fh is None:
            fh = {"show_existing": 0, "frame_to_show": None, "frame_type": None,
                  "show_frame": None, "order_hint": None, "ref_frame_idx": None}
        frames.append(Av1CodedFrame(
            byte_off=byte_off, size=size,
            show_existing=bool(fh["show_existing"]),
            show_frame=(None if fh["show_frame"] is None else bool(fh["show_frame"])),
            frame_type=fh["frame_type"], order_hint=fh["order_hint"],
            frame_to_show=fh["frame_to_show"],
            refresh_frame_flags=fh.get("refresh_frame_flags", 0),
            ref_frame_idx=fh.get("ref_frame_idx")))
        byte_off += size
    return frames, seq


def assign_display_ranks(frames):
    """Assign each coded frame its display rank (output ordinal) via DPB
    tracking, resolving show_existing references precisely.

    `frames` is the whole stream's coded frames in decode order. A hidden frame
    (show_frame = 0) is output later by a show_existing_frame; both get the same
    rank. RefSlot[i] tracks which decoded frame currently occupies DPB slot i, so
    a show_existing's frame_to_show_map_idx points at the exact frame it outputs
    (robust to order_hint wrap across GOPs)."""
    ref_slot = [None] * 8        # decode index occupying each DPB slot
    rank = 0
    for d, f in enumerate(frames):
        if f.show_existing:
            shown = (ref_slot[f.frame_to_show]
                     if f.frame_to_show is not None
                     and 0 <= f.frame_to_show < 8 else None)
            f.display_rank = rank
            if shown is not None:
                frames[shown].display_rank = rank
                # show_existing of a KEY frame refreshes every slot with it.
                if frames[shown].frame_type == KEY_FRAME:
                    for i in range(8):
                        ref_slot[i] = shown
            rank += 1
        else:
            if f.show_frame:
                f.display_rank = rank
                rank += 1
            # Resolve this frame's references from the current DPB (the slot
            # contents *before* its own refresh): ref_frame_idx[k] selects a DPB
            # slot, whose occupant is the referenced decode index. Split into the
            # forward (LAST..GOLDEN) and backward (BWDREF..ALTREF) groups.
            if f.ref_frame_idx is not None:
                def occ(k):
                    s = f.ref_frame_idx[k]
                    return ref_slot[s] if 0 <= s < 8 else None
                l0, l1 = [], []
                for k in range(4):           # LAST, LAST2, LAST3, GOLDEN
                    v = occ(k)
                    if v is not None and v not in l0:
                        l0.append(v)
                for k in range(4, 7):        # BWDREF, ALTREF2, ALTREF
                    v = occ(k)
                    if v is not None and v not in l0 and v not in l1:
                        l1.append(v)
                f.ref_decode_l0, f.ref_decode_l1 = l0, l1
            elif f.frame_type in (KEY_FRAME, INTRA_ONLY_FRAME):
                f.ref_decode_l0, f.ref_decode_l1 = [], []
            # else (inter, refs unparsed): leave None so the caller falls back.
            mask = f.refresh_frame_flags
            for i in range(8):
                if mask & (1 << i):
                    ref_slot[i] = d
    return frames
