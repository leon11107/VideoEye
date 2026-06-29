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
_PRIMARY_REF_NONE = 7

_KEY, _INTER, _INTRA_ONLY, _SWITCH = 0, 1, 2, 3
_FRAME_TYPE_NAMES = {0: "KEY_FRAME", 1: "INTER_FRAME",
                     2: "INTRA_ONLY_FRAME", 3: "SWITCH_FRAME"}

# Segmentation feature magnitude bits / signedness (SEG_LVL_* order).
_SEG_FEATURE_BITS = (8, 6, 6, 6, 6, 3, 0, 0)
_SEG_FEATURE_SIGNED = (1, 1, 1, 1, 1, 0, 0, 0)


def _tile_log2(blk_size: int, target: int) -> int:
    """Smallest k such that (blk_size << k) >= target."""
    k = 0
    while (blk_size << k) < target:
        k += 1
    return k


def _inverse_recenter(r: int, v: int) -> int:
    if v > 2 * r:
        return v
    if v & 1:
        return r - ((v + 1) >> 1)
    return r + (v >> 1)

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
            "max_frame_width": s["max_frame_width_minus_1"] + 1,
            "max_frame_height": s["max_frame_height_minus_1"] + 1,
            "use_128x128": s["use_128x128_superblock"],
            "enable_warped_motion": s.get("enable_warped_motion", 0),
            "enable_ref_frame_mvs": s.get("enable_ref_frame_mvs", 0),
            "film_grain_present": s["film_grain_params_present"],
            "mono_chrome": mono,
            "num_planes": num_planes,
            "subsampling_x": subx,
            "subsampling_y": suby,
            "separate_uv_delta_q": cc.get("separate_uv_delta_q", 0),
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

    # ---- frame header (uncompressed_header) ----------------------------- #

    def _frame_header(self, r: _Reader, s: dict) -> None:
        if self._seq is None:
            s["_note"] = "frame header not parsed (sequence header not seen yet)"
            return
        seq = self._seq

        if r.f(1):                                       # show_existing_frame
            s["show_existing_frame"] = 1
            s["frame_to_show_map_idx"] = r.f(3)
            if seq["frame_id_present"]:
                s["display_frame_id"] = r.f(seq["id_len"])
            return
        s["show_existing_frame"] = 0

        frame_type = r.f(2)
        s["frame_type"] = f"{frame_type} ({_FRAME_TYPE_NAMES[frame_type]})"
        intra = frame_type in (_KEY, _INTRA_ONLY)
        show_frame = r.f(1)
        s["show_frame"] = show_frame
        if show_frame:
            s["showable_frame"] = 0 if frame_type == _KEY else 1
        else:
            s["showable_frame"] = r.f(1)

        if frame_type == _SWITCH or (frame_type == _KEY and show_frame):
            err = 1
        else:
            err = r.f(1)
        s["error_resilient_mode"] = err
        s["disable_cdf_update"] = r.f(1)

        if seq["force_screen"] == _SELECT_SCREEN_CONTENT_TOOLS:
            asct = r.f(1)
        else:
            asct = seq["force_screen"]
        s["allow_screen_content_tools"] = asct
        if asct:
            if seq["force_intmv"] == _SELECT_INTEGER_MV:
                fimv = r.f(1)
            else:
                fimv = seq["force_intmv"]
        else:
            fimv = 0
        s["force_integer_mv"] = fimv          # shown before the FrameIsIntra override
        if intra:
            fimv = 1

        if seq["frame_id_present"]:
            s["current_frame_id"] = r.f(seq["id_len"])

        if frame_type == _SWITCH:
            override = 1
        elif seq["reduced"]:
            override = 0
        else:
            override = r.f(1)
        s["frame_size_override_flag"] = override

        s["order_hint"] = r.f(seq["OrderHintBits"])

        if intra or err:
            primary_ref = _PRIMARY_REF_NONE
        else:
            primary_ref = r.f(3)
        s["primary_ref_frame"] = primary_ref
        self._primary_ref = primary_ref

        if frame_type == _SWITCH or (frame_type == _KEY and show_frame):
            refresh = 0xFF
        else:
            refresh = r.f(8)
        s["refresh_frame_flags"] = refresh

        if not (intra and refresh == 0xFF):
            if err and seq["enable_order_hint"]:
                for i in range(8):
                    s[f"ref_order_hint[{i}]"] = r.f(seq["OrderHintBits"])

        allow_high_prec = 0
        if intra:
            self._frame_size(r, s, override)
            self._render_size(r, s)
            if asct and self._upscaled_width == self._frame_width:
                s["allow_intrabc"] = r.f(1)
            self._allow_intrabc = s.get("allow_intrabc", 0)
        else:
            self._allow_intrabc = 0
            if seq["enable_order_hint"]:
                short = r.f(1)
                s["frame_refs_short_signaling"] = short
                if short:
                    s["last_frame_idx"] = r.f(3)
                    s["gold_frame_idx"] = r.f(3)
            else:
                short = 0
            for i in range(7):
                if not short:
                    s[f"ref_frame_idx[{i}]"] = r.f(3)
                if seq["frame_id_present"]:
                    s[f"delta_frame_id_minus_1[{i}]"] = r.f(seq["delta_frame_id_len"])
            if override and not err:
                self._frame_size_with_refs(r, s)
            else:
                self._frame_size(r, s, override)
                self._render_size(r, s)
            if fimv:
                allow_high_prec = 0
            else:
                allow_high_prec = r.f(1)
                s["allow_high_precision_mv"] = allow_high_prec
            self._read_interpolation_filter(r, s)
            s["is_motion_mode_switchable"] = r.f(1)
            if err or not seq["enable_ref_frame_mvs"]:
                s["use_ref_frame_mvs"] = 0
            else:
                s["use_ref_frame_mvs"] = r.f(1)
        self._allow_high_prec = allow_high_prec

        if seq["reduced"] or s["disable_cdf_update"]:
            s["disable_frame_end_update_cdf"] = 1
        else:
            s["disable_frame_end_update_cdf"] = r.f(1)

        self._tile_info(r, s)
        self._quantization_params(r, s)
        self._segmentation_params(r, s, primary_ref)
        delta_q_present = self._delta_q_params(r, s)
        self._delta_lf_params(r, s, delta_q_present)

        coded_lossless = self._coded_lossless
        all_lossless = coded_lossless and (self._upscaled_width == self._frame_width)
        self._loop_filter_params(r, s, coded_lossless, primary_ref)
        self._cdef_params(r, s, coded_lossless)
        self._lr_params(r, s, all_lossless)
        self._read_tx_mode(r, s, coded_lossless)
        s["frame_reference_mode()"] = OrderedDict(
            reference_select=(0 if intra else r.f(1)))
        ref_select = s["frame_reference_mode()"]["reference_select"]
        self._skip_mode_params(r, s, intra, ref_select)
        if intra or err or not seq["enable_warped_motion"]:
            s["allow_warped_motion"] = 0
        else:
            s["allow_warped_motion"] = r.f(1)
        s["reduced_tx_set"] = r.f(1)
        if not intra:
            self._global_motion_params(r, s)
        if seq["film_grain_present"] and (show_frame or s["showable_frame"]):
            self._film_grain_params(r, s, frame_type)

    # ---- frame-header sub-functions ------------------------------------- #

    def _frame_size(self, r: _Reader, s: dict, override: int) -> None:
        seq = self._seq
        fs = OrderedDict()
        if override:
            fw = r.f(seq["frame_width_bits"]) + 1
            fh = r.f(seq["frame_height_bits"]) + 1
            fs["frame_width_minus_1"] = fw - 1
            fs["frame_height_minus_1"] = fh - 1
        else:
            fw, fh = seq["max_frame_width"], seq["max_frame_height"]
        self._frame_width, self._frame_height = fw, fh
        fs["superres_params()"] = self._superres_params(r)
        self._render_w = fw                              # may be set by render_size
        s["frame_size()"] = fs

    def _superres_params(self, r: _Reader) -> dict:
        sp = OrderedDict()
        use = r.f(1) if self._seq["enable_superres"] else 0
        sp["use_superres"] = use
        if use:
            sp["coded_denom"] = r.f(3)                   # SUPERRES_DENOM_BITS
            denom = sp["coded_denom"] + 9                # SUPERRES_DENOM_MIN
            self._upscaled_width = self._frame_width
            self._frame_width = (self._upscaled_width * 8 + (denom // 2)) // denom
        else:
            self._upscaled_width = self._frame_width
        return sp

    def _frame_size_with_refs(self, r: _Reader, s: dict) -> None:
        fs = OrderedDict()
        found = 0
        for i in range(7):
            fr = r.f(1)
            fs[f"found_ref[{i}]"] = fr
            if fr:
                found = 1
                break
        if not found:
            # frame_size() + render_size() inline
            self._frame_size(r, s, self._seq.get("_override_for_refs", 0))
            self._render_size(r, s)
        else:
            fs["superres_params()"] = self._superres_params(r)
        s["frame_size_with_refs()"] = fs

    def _render_size(self, r: _Reader, s: dict) -> None:
        rs = OrderedDict()
        diff = r.f(1)
        rs["render_and_frame_size_different"] = diff
        if diff:
            rs["render_width_minus_1"] = r.f(16)
            rs["render_height_minus_1"] = r.f(16)
        s["render_size()"] = rs

    def _read_interpolation_filter(self, r: _Reader, s: dict) -> None:
        f_ = OrderedDict()
        sw = r.f(1)
        f_["is_filter_switchable"] = sw
        if not sw:
            f_["interpolation_filter"] = r.f(2)
        s["read_interpolation_filter()"] = f_

    def _tile_info(self, r: _Reader, s: dict) -> None:
        seq = self._seq
        ti = OrderedDict()
        use128 = seq["use_128x128"]
        mi_cols = 2 * ((self._frame_width + 7) >> 3)
        mi_rows = 2 * ((self._frame_height + 7) >> 3)
        if use128:
            sb_cols = (mi_cols + 31) >> 5
            sb_rows = (mi_rows + 31) >> 5
            sb_shift = 5
        else:
            sb_cols = (mi_cols + 15) >> 4
            sb_rows = (mi_rows + 15) >> 4
            sb_shift = 4
        sb_size = sb_shift + 2
        max_tile_width_sb = 4096 >> sb_size
        max_tile_area_sb = (4096 * 2304) >> (2 * sb_size)
        min_log2_tile_cols = _tile_log2(max_tile_width_sb, sb_cols)
        max_log2_tile_cols = _tile_log2(1, min(sb_cols, 64))
        max_log2_tile_rows = _tile_log2(1, min(sb_rows, 64))
        min_log2_tiles = max(min_log2_tile_cols,
                             _tile_log2(max_tile_area_sb, sb_rows * sb_cols))

        uniform = r.f(1)
        ti["uniform_tile_spacing_flag"] = uniform
        if uniform:
            cols_log2 = min_log2_tile_cols
            cnt = 0
            while cols_log2 < max_log2_tile_cols:
                if r.f(1):
                    cols_log2 += 1
                    cnt += 1
                else:
                    break
            ti["increment_tile_cols_log2 (equal 1 count)"] = cnt
            min_log2_tile_rows = max(min_log2_tiles - cols_log2, 0)
            rows_log2 = min_log2_tile_rows
            cnt = 0
            while rows_log2 < max_log2_tile_rows:
                if r.f(1):
                    rows_log2 += 1
                    cnt += 1
                else:
                    break
            ti["increment_tile_rows_log2 (equal 1 count)"] = cnt
        else:
            widest = 0
            start = 0
            i = 0
            while start < sb_cols:
                w = r.ns(min(max_tile_width_sb, sb_cols - start)) + 1
                ti[f"width_in_sbs_minus_1[{i}]"] = w - 1
                widest = max(widest, w)
                start += w
                i += 1
            cols_log2 = _tile_log2(1, i)
            if min_log2_tiles > 0:
                max_tile_area_sb = (sb_rows * sb_cols) >> (min_log2_tiles + 1)
            max_tile_height_sb = max(max_tile_area_sb // widest, 1)
            start = 0
            i = 0
            while start < sb_rows:
                h = r.ns(min(max_tile_height_sb, sb_rows - start)) + 1
                ti[f"height_in_sbs_minus_1[{i}]"] = h - 1
                start += h
                i += 1
            rows_log2 = _tile_log2(1, i)
        if cols_log2 > 0 or rows_log2 > 0:
            ti["context_update_tile_id"] = r.f(rows_log2 + cols_log2)
            ti["tile_size_bytes_minus_1"] = r.f(2)
        s["tile_info()"] = ti

    def _read_delta_q(self, r: _Reader, parent: dict, name: str) -> int:
        d = OrderedDict()
        coded = r.f(1)
        d["delta_coded"] = coded
        val = r.su(6) if coded else 0
        d["delta_q"] = val
        parent[f"read_delta_q({name})"] = d
        return val

    def _quantization_params(self, r: _Reader, s: dict) -> None:
        seq = self._seq
        q = OrderedDict()
        base = r.f(8)
        q["base_q_idx"] = base
        self._base_q_idx = base
        ydc = self._read_delta_q(r, q, "DeltaQYDc")
        udc = uac = vdc = vac = 0
        if seq["num_planes"] > 1:
            diff = r.f(1) if seq["separate_uv_delta_q"] else 0
            q["diff_uv_delta"] = diff
            udc = self._read_delta_q(r, q, "DeltaQUDc")
            uac = self._read_delta_q(r, q, "DeltaQUAc")
            if diff:
                vdc = self._read_delta_q(r, q, "DeltaQVDc")
                vac = self._read_delta_q(r, q, "DeltaQVAc")
        using_qm = r.f(1)
        q["using_qmatrix"] = using_qm
        if using_qm:
            q["qm_y"] = r.f(4)
            q["qm_u"] = r.f(4)
            if seq["separate_uv_delta_q"]:
                q["qm_v"] = r.f(4)
        s["quantization_params()"] = q
        self._lossless0 = (base == 0 and ydc == 0 and udc == 0 and uac == 0
                           and vdc == 0 and vac == 0)

    def _segmentation_params(self, r: _Reader, s: dict, primary_ref: int) -> None:
        seg = OrderedDict()
        enabled = r.f(1)
        seg["segmentation_enabled"] = enabled
        if enabled:
            if primary_ref == _PRIMARY_REF_NONE:
                update_map, update_data = 1, 1
            else:
                update_map = r.f(1)
                seg["segmentation_update_map"] = update_map
                if update_map:
                    seg["segmentation_temporal_update"] = r.f(1)
                update_data = r.f(1)
                seg["segmentation_update_data"] = update_data
            if update_data:
                for i in range(8):                       # MAX_SEGMENTS
                    for j in range(8):                   # SEG_LVL_MAX
                        fe = r.f(1)
                        seg[f"feature_enabled[{i}][{j}]"] = fe
                        if fe:
                            bits = _SEG_FEATURE_BITS[j]
                            if bits:
                                if _SEG_FEATURE_SIGNED[j]:
                                    seg[f"feature_value[{i}][{j}]"] = r.su(bits)
                                else:
                                    seg[f"feature_value[{i}][{j}]"] = r.f(bits)
        s["segmentation_params()"] = seg
        # CodedLossless: every active segment lossless. With segmentation off the
        # single segment uses base_q_idx; otherwise we approximate (lossless is a
        # rare edge case and only gates loop_filter/cdef/lr display).
        self._coded_lossless = (self._lossless0 and not enabled)

    def _delta_q_params(self, r: _Reader, s: dict) -> int:
        d = OrderedDict()
        present = r.f(1) if self._base_q_idx > 0 else 0
        d["delta_q_present"] = present
        if present:
            d["delta_q_res"] = r.f(2)
        s["delta_q_params()"] = d
        return present

    def _delta_lf_params(self, r: _Reader, s: dict, delta_q_present: int) -> None:
        d = OrderedDict()
        present = 0
        if delta_q_present:
            if not self._allow_intrabc:
                present = r.f(1)
            d["delta_lf_present"] = present
            if present:
                d["delta_lf_res"] = r.f(2)
                d["delta_lf_multi"] = r.f(1)
        s["delta_lf_params()"] = d

    def _loop_filter_params(self, r: _Reader, s: dict, coded_lossless: int,
                            primary_ref: int) -> None:
        lf = OrderedDict()
        if coded_lossless or self._allow_intrabc:
            s["loop_filter_params()"] = lf
            return
        l0 = r.f(6)
        l1 = r.f(6)
        lf["loop_filter_level[0]"] = l0
        lf["loop_filter_level[1]"] = l1
        if self._seq["num_planes"] > 1 and (l0 or l1):
            lf["loop_filter_level[2]"] = r.f(6)
            lf["loop_filter_level[3]"] = r.f(6)
        lf["loop_filter_sharpness"] = r.f(3)
        en = r.f(1)
        lf["loop_filter_delta_enabled"] = en
        if en:
            upd = r.f(1)
            lf["loop_filter_delta_update"] = upd
            if upd:
                for i in range(8):                       # TOTAL_REFS_PER_FRAME
                    u = r.f(1)
                    lf[f"update_ref_delta[{i}]"] = u
                    if u:
                        lf[f"loop_filter_ref_deltas[{i}]"] = r.su(6)
                for i in range(2):
                    u = r.f(1)
                    lf[f"update_mode_delta[{i}]"] = u
                    if u:
                        lf[f"loop_filter_mode_deltas[{i}]"] = r.su(6)
        s["loop_filter_params()"] = lf

    def _cdef_params(self, r: _Reader, s: dict, coded_lossless: int) -> None:
        cd = OrderedDict()
        if coded_lossless or self._allow_intrabc or not self._seq["enable_cdef"]:
            s["cdef_params()"] = cd
            return
        cd["cdef_damping_minus_3"] = r.f(2)
        bits = r.f(2)
        cd["cdef_bits"] = bits
        for i in range(1 << bits):
            cd[f"cdef_y_pri_strength[{i}]"] = r.f(4)
            cd[f"cdef_y_sec_strength[{i}]"] = r.f(2)
            if self._seq["num_planes"] > 1:
                cd[f"cdef_uv_pri_strength[{i}]"] = r.f(4)
                cd[f"cdef_uv_sec_strength[{i}]"] = r.f(2)
        s["cdef_params()"] = cd

    def _lr_params(self, r: _Reader, s: dict, all_lossless: int) -> None:
        lr = OrderedDict()
        if all_lossless or self._allow_intrabc or not self._seq["enable_restoration"]:
            s["lr_params()"] = lr
            return
        uses_lr = uses_chroma_lr = False
        np = self._seq["num_planes"]
        for i in range(np):
            t = r.f(2)
            lr[f"lr_type[{i}]"] = t
            if t != 0:                                   # RESTORE_NONE
                uses_lr = True
                if i > 0:
                    uses_chroma_lr = True
        if uses_lr:
            # lr_unit_shift is shown as the raw syntax element (Elecard convention);
            # the +1 for 128x128 SBs is a derived value, not the read element.
            shift = r.f(1)
            lr["lr_unit_shift"] = shift
            if not self._seq["use_128x128"] and shift:
                lr["lr_unit_extra_shift"] = r.f(1)
            if self._seq["subsampling_x"] and self._seq["subsampling_y"] and uses_chroma_lr:
                lr["lr_uv_shift"] = r.f(1)
            else:
                lr["lr_uv_shift"] = 0
        s["lr_params()"] = lr

    def _read_tx_mode(self, r: _Reader, s: dict, coded_lossless: int) -> None:
        d = OrderedDict()
        if coded_lossless:
            d["tx_mode"] = "ONLY_4X4"
        else:
            d["tx_mode_select"] = r.f(1)
        s["read_tx_mode()"] = d

    def _skip_mode_params(self, r: _Reader, s: dict, intra: int,
                          ref_select: int) -> None:
        d = OrderedDict()
        # skipModeAllowed needs forward+backward refs; approximate as not allowed
        # for intra / single-ref / no order hint (the common cases). When in doubt
        # the bit isn't present, matching these streams.
        allowed = (not intra) and ref_select and self._seq["enable_order_hint"]
        if allowed:
            d["skip_mode_present"] = r.f(1)
        else:
            d["skip_mode_present"] = 0
        s["skip_mode_params()"] = d

    def _global_motion_params(self, r: _Reader, s: dict) -> None:
        gm = OrderedDict()
        for ref in range(1, 8):                          # LAST_FRAME..ALTREF_FRAME
            is_global = r.f(1)
            gm[f"is_global[{ref}]"] = is_global
            if is_global:
                is_rot_zoom = r.f(1)
                gm[f"is_rot_zoom[{ref}]"] = is_rot_zoom
                if is_rot_zoom:
                    gm_type = 2                          # ROTZOOM
                else:
                    is_trans = r.f(1)
                    gm[f"is_translation[{ref}]"] = is_trans
                    gm_type = 1 if is_trans else 3       # TRANSLATION / AFFINE
                self._read_global_motion(r, gm, gm_type, ref)
        s["global_motion_params()"] = gm

    def _read_global_motion(self, r: _Reader, gm: dict, gm_type: int,
                            ref: int) -> None:
        if gm_type in (2, 3):                            # ROTZOOM / AFFINE
            self._read_global_param(r, gm, gm_type, ref, 2)
            self._read_global_param(r, gm, gm_type, ref, 3)
            if gm_type == 3:                             # AFFINE
                self._read_global_param(r, gm, gm_type, ref, 4)
                self._read_global_param(r, gm, gm_type, ref, 5)
            else:
                pass
        if gm_type in (1, 2, 3):
            self._read_global_param(r, gm, gm_type, ref, 0)
            self._read_global_param(r, gm, gm_type, ref, 1)

    def _read_global_param(self, r: _Reader, gm: dict, gm_type: int, ref: int,
                           idx: int) -> None:
        abs_bits = 12                                    # GM_ABS_ALPHA_BITS
        prec_bits = 15                                   # GM_ALPHA_PREC_BITS
        if idx < 2:
            if gm_type == 1:                             # TRANSLATION
                abs_bits = 9 - (not self._allow_high_prec)
                prec_bits = 3 - (not self._allow_high_prec)
            else:
                abs_bits = 12
                prec_bits = 6
        mx = 1 << abs_bits
        # PrevGmParams default to identity; only affects the recentered value,
        # never the number of bits consumed, so bit position stays correct.
        r_ref = 0
        val = self._decode_signed_subexp_with_ref(r, -mx, mx + 1, r_ref)
        gm[f"gm_params[{ref}][{idx}]"] = val

    def _decode_signed_subexp_with_ref(self, r, low, high, ref):
        x = self._decode_unsigned_subexp_with_ref(r, high - low, ref - low)
        return x + low

    def _decode_unsigned_subexp_with_ref(self, r, mx, ref):
        v = self._decode_subexp(r, mx)
        if (ref << 1) <= mx:
            return _inverse_recenter(ref, v)
        return mx - 1 - _inverse_recenter(mx - 1 - ref, v)

    def _decode_subexp(self, r, num_syms):
        i = 0
        mk = 0
        k = 3
        while True:
            b2 = k + i - 1 if i else k
            a = 1 << b2
            if num_syms <= mk + 3 * a:
                return r.ns(num_syms - mk) + mk
            if r.f(1):
                i += 1
                mk += a
            else:
                return r.f(b2) + mk

    def _film_grain_params(self, r: _Reader, s: dict, frame_type: int) -> None:
        seq = self._seq
        fg = OrderedDict()
        apply_grain = r.f(1)
        fg["apply_grain"] = apply_grain
        if not apply_grain:
            s["film_grain_params()"] = fg
            return
        fg["grain_seed"] = r.f(16)
        update_grain = r.f(1) if frame_type == _INTER else 1
        if frame_type == _INTER:
            fg["update_grain"] = update_grain
        if not update_grain:
            fg["film_grain_params_ref_idx"] = r.f(3)
            s["film_grain_params()"] = fg
            return
        num_y = r.f(4)
        fg["num_y_points"] = num_y
        for i in range(num_y):
            fg[f"point_y_value[{i}]"] = r.f(8)
            fg[f"point_y_scaling[{i}]"] = r.f(8)
        mono = seq["mono_chrome"]
        chroma_from_luma = 0 if mono else r.f(1)
        if not mono:
            fg["chroma_scaling_from_luma"] = chroma_from_luma
        if (mono or chroma_from_luma or
                (seq["subsampling_x"] == 1 and seq["subsampling_y"] == 1 and num_y == 0)):
            num_cb = num_cr = 0
        else:
            num_cb = r.f(4)
            fg["num_cb_points"] = num_cb
            for i in range(num_cb):
                fg[f"point_cb_value[{i}]"] = r.f(8)
                fg[f"point_cb_scaling[{i}]"] = r.f(8)
            num_cr = r.f(4)
            fg["num_cr_points"] = num_cr
            for i in range(num_cr):
                fg[f"point_cr_value[{i}]"] = r.f(8)
                fg[f"point_cr_scaling[{i}]"] = r.f(8)
        fg["grain_scaling_minus_8"] = r.f(2)
        lag = r.f(2)
        fg["ar_coeff_lag"] = lag
        num_pos_luma = 2 * lag * (lag + 1)
        num_pos_chroma = num_pos_luma
        if num_y:
            num_pos_chroma = num_pos_luma + 1
            for i in range(num_pos_luma):
                fg[f"ar_coeffs_y_plus_128[{i}]"] = r.f(8)
        if chroma_from_luma or num_cb:
            for i in range(num_pos_chroma):
                fg[f"ar_coeffs_cb_plus_128[{i}]"] = r.f(8)
        if chroma_from_luma or num_cr:
            for i in range(num_pos_chroma):
                fg[f"ar_coeffs_cr_plus_128[{i}]"] = r.f(8)
        fg["ar_coeff_shift_minus_6"] = r.f(2)
        fg["grain_scale_shift"] = r.f(2)
        if num_cb:
            fg["cb_mult"] = r.f(8)
            fg["cb_luma_mult"] = r.f(8)
            fg["cb_offset"] = r.f(9)
        if num_cr:
            fg["cr_mult"] = r.f(8)
            fg["cr_luma_mult"] = r.f(8)
            fg["cr_offset"] = r.f(9)
        fg["overlap_flag"] = r.f(1)
        fg["clip_to_restricted_range"] = r.f(1)
        s["film_grain_params()"] = fg
