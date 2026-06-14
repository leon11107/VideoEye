"""Picture Order Count (POC) derivation for decode<->display mapping.

Raw elementary streams carry no container timestamps, so the only way to map
a decoded (presentation-order) frame back to its decode-order index is the
bitstream's POC. A tracker walks frames in decode order, feeding parameter
sets and slice headers to the existing codec parsers, and derives a globally
monotonic *display key* per frame: sorting frames by this key yields the
decoder's output order.

Handled: HEVC, and H.264 pic_order_cnt_type 0 and 2. Anything else (H.264
poc_type 1, parse errors, missing parameter sets) yields None for that
frame, signalling the caller to fall back to emission-order labelling.
"""

from typing import Optional

from .nalu_parser import NALUParser
from .h264_parser import H264Parser
from .h265_parser import H265Parser


class _PocTracker:
    """Base: derive a globally monotonic display key per frame (decode order).

    The key is base + local_poc, where base is bumped past the previous GOP's
    maximum at each IDR so keys stay monotonic across IDR resets. feed() is
    called once per frame (packet) in decode order and returns its key, or
    None if POC cannot be derived for this stream.
    """

    def __init__(self, is_h265: bool):
        self._nalu = NALUParser(is_h265=is_h265)
        self._prev_lsb = 0
        self._prev_msb = 0
        self._base = 0
        self._max_key: Optional[int] = None
        self._log2: Optional[int] = None

    def _bump_base_for_idr(self) -> int:
        self._base = 0 if self._max_key is None else self._max_key + 1
        self._prev_lsb = 0
        self._prev_msb = 0
        return self._base

    def _emit(self, key: int) -> int:
        self._max_key = key if self._max_key is None else max(self._max_key, key)
        return key

    @staticmethod
    def _derive_msb(lsb: int, prev_lsb: int, prev_msb: int, max_lsb: int) -> int:
        half = max_lsb // 2
        if lsb < prev_lsb and (prev_lsb - lsb) >= half:
            return prev_msb + max_lsb
        if lsb > prev_lsb and (lsb - prev_lsb) > half:
            return prev_msb - max_lsb
        return prev_msb

    def feed(self, packet_bytes: bytes) -> Optional[int]:
        raise NotImplementedError


class _HevcPocTracker(_PocTracker):
    def __init__(self):
        super().__init__(is_h265=True)
        self._p = H265Parser()

    def feed(self, packet_bytes: bytes) -> Optional[int]:
        try:
            nalus = self._nalu.parse(packet_bytes)
        except Exception:
            return None
        for nalu in nalus:
            t = nalu.nal_unit_type
            if t == 33:  # SPS
                try:
                    sps = self._p.parse_sps(nalu)
                    v = sps.get("log2_max_pic_order_cnt_lsb_minus4")
                    if v is not None:
                        self._log2 = v + 4
                except Exception:
                    pass
                continue
            if t == 34:  # PPS (needed so parse_slice_header resolves SPS)
                try:
                    self._p.parse_pps(nalu)
                except Exception:
                    pass
                continue
            if not (0 <= t <= 21):  # not a slice
                continue
            if t in (19, 20):  # IDR_W_RADL / IDR_N_LP
                return self._emit(self._bump_base_for_idr())
            if self._log2 is None:
                return None
            try:
                hdr = self._p.parse_slice_header(nalu)
            except Exception:
                return None
            lsb = hdr.get("slice_pic_order_cnt_lsb")
            if lsb is None:
                return None
            max_lsb = 1 << self._log2
            msb = self._derive_msb(lsb, self._prev_lsb, self._prev_msb, max_lsb)
            key = self._base + msb + lsb
            # prevTid0Pic: previous picture with TemporalId 0 that is a
            # reference and not RASL/RADL. Reference trailing NUTs are odd
            # (1/3/5); IRAP is 16..23. RASL=8/9, RADL=6/7 are excluded.
            tid = (nalu.data[1] & 0x07) - 1 if len(nalu.data) > 1 else 0
            if tid == 0 and (t in (1, 3, 5) or 16 <= t <= 23):
                self._prev_lsb = lsb
                self._prev_msb = msb
            return self._emit(key)
        return None


class _H264PocTracker(_PocTracker):
    def __init__(self):
        super().__init__(is_h265=False)
        self._p = H264Parser()
        self._poc_type: Optional[int] = None
        self._decode_counter = 0

    def feed(self, packet_bytes: bytes) -> Optional[int]:
        try:
            nalus = self._nalu.parse(packet_bytes)
        except Exception:
            return None
        for nalu in nalus:
            t = nalu.nal_unit_type
            if t == 7:  # SPS
                try:
                    sps = self._p.parse_sps(nalu)
                    pt = sps.get("pic_order_cnt_type")
                    if pt is not None:
                        self._poc_type = pt
                    v = sps.get("log2_max_pic_order_cnt_lsb_minus4")
                    if v is not None:
                        self._log2 = v + 4
                except Exception:
                    pass
                continue
            if t == 8:  # PPS
                try:
                    self._p.parse_pps(nalu)
                except Exception:
                    pass
                continue
            if not (1 <= t <= 5):  # not a slice
                continue
            # poc_type 2: decode order == display order.
            if self._poc_type == 2:
                key = self._decode_counter
                self._decode_counter += 1
                return self._emit(key)
            if self._poc_type != 0:
                return None  # poc_type 1 not supported -> fall back
            if t == 5:  # IDR
                return self._emit(self._bump_base_for_idr())
            if self._log2 is None:
                return None
            try:
                hdr = self._p.parse_slice_header(nalu)
            except Exception:
                return None
            lsb = hdr.get("pic_order_cnt_lsb")
            if lsb is None:
                return None
            max_lsb = 1 << self._log2
            msb = self._derive_msb(lsb, self._prev_lsb, self._prev_msb, max_lsb)
            key = self._base + msb + lsb
            # Update the previous reference picture's POC (nal_ref_idc != 0).
            nal_ref_idc = (nalu.data[0] >> 5) & 0x03 if nalu.data else 0
            if nal_ref_idc != 0:
                self._prev_lsb = lsb
                self._prev_msb = msb
            return self._emit(key)
        return None


def create_poc_tracker(codec_name: str) -> Optional[_PocTracker]:
    """Tracker for a codec, or None if POC mapping is not supported for it."""
    name = (codec_name or "").lower()
    if name in ("hevc", "h265", "h.265"):
        return _HevcPocTracker()
    if name in ("h264", "avc", "h.264"):
        return _H264PocTracker()
    return None
