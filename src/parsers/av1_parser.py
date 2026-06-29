"""AV1 OBU syntax parser for the stream viewer.

Parses each OBU's header and payload into a nested syntax dict (the same shape
the H.264/H.265 parsers return), so the viewer's _add_syntax_tree can render the
full element tree the way Elecard's Stream Viewer does. This is the verbose,
display-oriented parser; the lean model-driven parser in core/av1_obu.py stays
separate (it runs on every frame during extraction and only extracts what the
decode-order model needs).

Field names and hierarchy follow the AV1 spec (and Elecard's dump) so the tree
reads identically. Verified element-by-element against Elecard's headers.csv.
"""

from collections import OrderedDict

from ..core.av1_obu import (
    OBU_TYPE_NAMES, OBU_SEQUENCE_HEADER, OBU_FRAME_HEADER, OBU_FRAME,
    OBU_REDUNDANT_FRAME_HEADER, _leb128,
)

# Spec constants.
_SELECT_SCREEN_CONTENT_TOOLS = 2
_SELECT_INTEGER_MV = 2
_CP_BT_709 = 1
_TC_SRGB = 13
_MC_IDENTITY = 0

# OBU type -> spec syntax-function name (matches Elecard's node labels).
_OBU_FUNC = {
    1: "sequence_header_obu()",
    2: "temporal_delimiter_obu()",
    3: "frame_header_obu()",
    4: "tile_group_obu()",
    5: "metadata_obu()",
    6: "frame_obu()",
    7: "frame_header_obu()",
    15: "padding_obu()",
}


class _Reader:
    """MSB-first bit reader over a byte buffer with the AV1 descriptors."""
    __slots__ = ("d", "pos", "bit", "end")

    def __init__(self, data, byte_pos=0, end=None):
        self.d = data
        self.pos = byte_pos
        self.bit = 0
        self.end = len(data) if end is None else end

    def f(self, n: int) -> int:
        v = 0
        d = self.d
        for _ in range(n):
            if self.pos >= self.end:
                raise EOFError
            v = (v << 1) | ((d[self.pos] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.pos += 1
        return v

    def uvlc(self) -> int:
        lz = 0
        while True:
            if self.f(1):
                break
            lz += 1
            if lz >= 32:
                return (1 << 32) - 1
        return self.f(lz) + (1 << lz) - 1 if lz else 0

    def su(self, n: int) -> int:
        value = self.f(n)
        if self.f(1):
            value -= (1 << n)
        return value

    def ns(self, n: int) -> int:
        if n <= 1:
            return 0
        w = (n - 1).bit_length() + 1
        m = (1 << w) - n
        v = self.f(w - 1)
        if v < m:
            return v
        return (v << 1) - m + self.f(1)

    def le(self, n: int) -> int:
        # byte-aligned little-endian
        t = 0
        for i in range(n):
            t |= self.f(8) << (8 * i)
        return t


class Av1Parser:
    """Parses AV1 OBUs (header + payload) into syntax dicts for display."""

    def __init__(self):
        # Sequence-header state carried across OBUs / frames (a frame header is
        # parsed against the active sequence header). Set externally via
        # set_sequence() when the viewer opens a non-keyframe first.
        self._seq = None

    def has_sequence(self) -> bool:
        return self._seq is not None

    def set_sequence(self, seq: dict) -> None:
        """Seed the active sequence-header state (e.g. from the demuxer) so frame
        headers can be parsed even when a non-keyframe is viewed first."""
        if seq is not None:
            self._seq = seq

    def parse(self, buf: bytes) -> list:
        """Parse all OBUs in buf. Returns a list of dicts:
        {type, name, offset, size, syntax} where syntax is the rendered tree."""
        out = []
        pos, n = 0, len(buf)
        while pos < n:
            start = pos
            try:
                r = _Reader(buf, pos)
                hdr = OrderedDict()
                hdr["obu_forbidden_bit"] = r.f(1)
                obu_type = r.f(4)
                hdr["obu_type"] = f"{obu_type} ({OBU_TYPE_NAMES.get(obu_type, 'reserved')})"
                ext = r.f(1)
                hdr["obu_extension_flag"] = ext
                has_size = r.f(1)
                hdr["obu_has_size_field"] = has_size
                hdr["obu_reserved_1bit"] = r.f(1)
                if ext:
                    hdr["temporal_id"] = r.f(3)
                    hdr["spatial_id"] = r.f(2)
                    r.f(3)                      # extension_header_reserved_3bits
                hpos = r.pos                    # header is byte-aligned
                if has_size:
                    size, payload_off = _leb128(buf, hpos)
                else:
                    size, payload_off = n - hpos, hpos
            except (IndexError, EOFError):
                break
            end = payload_off + size
            if end > n or end <= start:
                break

            syntax = OrderedDict()
            syntax["_name"] = _OBU_FUNC.get(obu_type,
                                            OBU_TYPE_NAMES.get(obu_type, "obu()"))
            syntax["obu_header()"] = hdr
            if has_size:
                syntax["obu_size"] = size

            pr = _Reader(buf, payload_off, end)
            try:
                if obu_type == OBU_SEQUENCE_HEADER:
                    self._sequence_header(pr, syntax)
                elif obu_type in (OBU_FRAME, OBU_FRAME_HEADER,
                                  OBU_REDUNDANT_FRAME_HEADER):
                    self._frame_header(pr, syntax)
            except EOFError:
                syntax["_parse_error"] = "Unexpected end of data"
            except Exception as e:                       # never break the viewer
                syntax["_parse_error"] = str(e)

            out.append({
                "type": obu_type,
                "name": OBU_TYPE_NAMES.get(obu_type, f"OBU_RESERVED_{obu_type}"),
                "offset": start, "size": end - start, "syntax": syntax,
            })
            pos = end
        return out

    # ---- sequence header ------------------------------------------------- #

    def _sequence_header(self, r: _Reader, s: dict) -> None:
        seq_profile = r.f(3)
        s["seq_profile"] = seq_profile
        s["still_picture"] = r.f(1)
        reduced = r.f(1)
        s["reduced_still_picture_header"] = reduced

        decoder_model_info_present = 0
        buffer_delay_length = 0
        if reduced:
            s["seq_level_idx[0]"] = r.f(5)
        else:
            timing_present = r.f(1)
            s["timing_info_present_flag"] = timing_present
            if timing_present:
                ti = OrderedDict()
                ti["num_units_in_display_tick"] = r.f(32)
                ti["time_scale"] = r.f(32)
                eqi = r.f(1)
                ti["equal_picture_interval"] = eqi
                if eqi:
                    ti["num_ticks_per_picture_minus_1"] = r.uvlc()
                s["timing_info()"] = ti
                decoder_model_info_present = r.f(1)
                s["decoder_model_info_present_flag"] = decoder_model_info_present
                if decoder_model_info_present:
                    dm = OrderedDict()
                    bdl = r.f(5)
                    dm["buffer_delay_length_minus_1"] = bdl
                    buffer_delay_length = bdl + 1
                    dm["num_units_in_decoding_tick"] = r.f(32)
                    dm["buffer_removal_time_length_minus_1"] = r.f(5)
                    dm["frame_presentation_time_length_minus_1"] = r.f(5)
                    s["decoder_model_info()"] = dm
            idd_present = r.f(1)
            s["initial_display_delay_present_flag"] = idd_present
            opcnt = r.f(5)
            s["operating_points_cnt_minus_1"] = opcnt
            for i in range(opcnt + 1):
                s[f"operating_point_idc[{i}]"] = r.f(12)
                lvl = r.f(5)
                s[f"seq_level_idx[{i}]"] = lvl
                if lvl > 7:
                    s[f"seq_tier[{i}]"] = r.f(1)
                else:
                    s[f"seq_tier[{i}]"] = 0
                if decoder_model_info_present:
                    op = r.f(1)
                    s[f"decoder_model_present_for_this_op[{i}]"] = op
                    if op:
                        s[f"decoder_buffer_delay[{i}]"] = r.f(buffer_delay_length)
                        s[f"encoder_buffer_delay[{i}]"] = r.f(buffer_delay_length)
                        s[f"low_delay_mode_flag[{i}]"] = r.f(1)
                if idd_present:
                    p = r.f(1)
                    s[f"initial_display_delay_present_for_this_op[{i}]"] = p
                    if p:
                        s[f"initial_display_delay_minus_1[{i}]"] = r.f(4)

        fwb = r.f(4)
        s["frame_width_bits_minus_1"] = fwb
        fhb = r.f(4)
        s["frame_height_bits_minus_1"] = fhb
        s["max_frame_width_minus_1"] = r.f(fwb + 1)
        s["max_frame_height_minus_1"] = r.f(fhb + 1)

        frame_id_present = 0 if reduced else r.f(1)
        s["frame_id_numbers_present_flag"] = frame_id_present
        delta_frame_id_len = 0
        id_len = 0
        if frame_id_present:
            dfl = r.f(4)
            s["delta_frame_id_length_minus_2"] = dfl
            afl = r.f(3)
            s["additional_frame_id_length_minus_1"] = afl
            delta_frame_id_len = dfl + 2
            id_len = (afl + 1) + (dfl + 2)

        s["use_128x128_superblock"] = r.f(1)
        s["enable_filter_intra"] = r.f(1)
        s["enable_intra_edge_filter"] = r.f(1)

        enable_order_hint = 0
        order_hint_bits = 0
        force_screen = _SELECT_SCREEN_CONTENT_TOOLS
        force_intmv = _SELECT_INTEGER_MV
        if not reduced:
            s["enable_interintra_compound"] = r.f(1)
            s["enable_masked_compound"] = r.f(1)
            s["enable_warped_motion"] = r.f(1)
            s["enable_dual_filter"] = r.f(1)
            enable_order_hint = r.f(1)
            s["enable_order_hint"] = enable_order_hint
            if enable_order_hint:
                s["enable_jnt_comp"] = r.f(1)
                s["enable_ref_frame_mvs"] = r.f(1)
            choose_sct = r.f(1)
            s["seq_choose_screen_content_tools"] = choose_sct
            force_screen = (_SELECT_SCREEN_CONTENT_TOOLS if choose_sct
                            else r.f(1))
            # Elecard shows the resolved value even when chosen (derived).
            s["seq_force_screen_content_tools"] = force_screen
            if force_screen > 0:
                choose_imv = r.f(1)
                s["seq_choose_integer_mv"] = choose_imv
                if choose_imv:
                    force_intmv = _SELECT_INTEGER_MV
                else:
                    force_intmv = r.f(1)
                    s["seq_force_integer_mv"] = force_intmv
            if enable_order_hint:
                ohb = r.f(3)
                s["order_hint_bits_minus_1"] = ohb
                order_hint_bits = ohb + 1

        s["enable_superres"] = r.f(1)
        s["enable_cdef"] = r.f(1)
        s["enable_restoration"] = r.f(1)

        cc = OrderedDict()
        bit_depth, num_planes, mono, subx, suby = self._color_config(r, cc, seq_profile)
        s["color_config()"] = cc
        s["film_grain_params_present"] = r.f(1)

        # State the frame-header parser needs.
        self._seq = {
            "reduced": reduced,
            "frame_id_present": frame_id_present,
            "id_len": id_len,
            "delta_frame_id_len": delta_frame_id_len,
            "enable_order_hint": enable_order_hint,
            "OrderHintBits": order_hint_bits,
            "force_screen": force_screen,
            "force_intmv": force_intmv,
            "enable_superres": s["enable_superres"],
            "enable_cdef": s["enable_cdef"],
            "enable_restoration": s["enable_restoration"],
            "frame_width_bits": fwb + 1,
            "frame_height_bits": fhb + 1,
            "enable_warped_motion": s.get("enable_warped_motion", 0),
            "enable_ref_frame_mvs": s.get("enable_ref_frame_mvs", 0),
            "film_grain_present": s["film_grain_params_present"],
            "mono_chrome": mono,
            "num_planes": num_planes,
            "subsampling_x": subx,
            "subsampling_y": suby,
        }

    def _color_config(self, r: _Reader, cc: dict, seq_profile: int):
        high = r.f(1)
        cc["high_bitdepth"] = high
        if seq_profile == 2 and high:
            twelve = r.f(1)
            cc["twelve_bit"] = twelve
            bit_depth = 12 if twelve else 10
        elif seq_profile <= 2:
            bit_depth = 10 if high else 8
        else:
            bit_depth = 8

        mono = 0 if seq_profile == 1 else r.f(1)
        if seq_profile != 1:
            cc["mono_chrome"] = mono
        num_planes = 1 if mono else 3

        cdp = r.f(1)
        cc["color_description_present_flag"] = cdp
        if cdp:
            cp = r.f(8)
            cc["color_primaries"] = cp
            tc = r.f(8)
            cc["transfer_characteristics"] = tc
            mc = r.f(8)
            cc["matrix_coefficients"] = mc
        else:
            cp = tc = mc = 2                       # *_UNSPECIFIED

        if mono:
            cc["color_range"] = r.f(1)
            cc["subsampling_x"] = 1
            cc["subsampling_y"] = 1
            return bit_depth, 1, mono, 1, 1
        if cp == _CP_BT_709 and tc == _TC_SRGB and mc == _MC_IDENTITY:
            subx, suby = 0, 0                      # color_range = 1 (inferred)
        else:
            cc["color_range"] = r.f(1)
            if seq_profile == 0:
                subx, suby = 1, 1
            elif seq_profile == 1:
                subx, suby = 0, 0
            elif bit_depth == 12:
                subx = r.f(1)
                suby = r.f(1) if subx else 0
            else:
                subx, suby = 1, 0
        # Elecard shows the resolved subsampling even when derived from profile.
        cc["subsampling_x"] = subx
        cc["subsampling_y"] = suby
        if subx and suby:
            cc["chroma_sample_position"] = r.f(2)
        cc["separate_uv_delta_q"] = r.f(1)
        return bit_depth, num_planes, mono, subx, suby

    # ---- frame header (Phase 2) ----------------------------------------- #

    def _frame_header(self, r: _Reader, s: dict) -> None:
        # Placeholder: the full uncompressed_header parse lands in Phase 2.
        if self._seq is None:
            s["_note"] = "frame header not parsed (sequence header not seen yet)"
            return
        s["_note"] = "frame header parsing pending (Phase 2)"
