"""H.264/AVC bitstream parser."""

from typing import Optional
from collections import OrderedDict

from .nalu_parser import NALUnit, H264NaluType
from ._common import read_sei_payload_header
from ..utils.bitstream_reader import BitstreamReader


def _more_rbsp_data(reader: BitstreamReader) -> bool:
    """H.264 more_rbsp_data(): true if data remains before the rbsp trailing
    bits (the final rbsp_stop_one_bit + zero padding)."""
    if reader.bits_remaining() <= 0:
        return False
    data, n = reader.data, reader._length
    last = -1
    for i in range(n - 1, -1, -1):
        if data[i]:
            last = i
            break
    if last < 0:
        return False
    b = data[last]
    stop_bit = next(7 - j for j in range(8) if (b >> j) & 1)
    stop_pos = last * 8 + stop_bit
    return reader.byte_offset * 8 + reader.bit_offset < stop_pos


class H264Parser:
    """Parses H.264/AVC NAL unit syntax."""

    # Profile IDC values
    PROFILES = {
        66: "Baseline",
        77: "Main",
        88: "Extended",
        100: "High",
        110: "High 10",
        122: "High 4:2:2",
        244: "High 4:4:4 Predictive",
        44: "CAVLC 4:4:4 Intra",
        83: "Scalable Baseline",
        86: "Scalable High",
        118: "Multiview High",
        128: "Stereo High",
        138: "Multiview Depth High",
    }

    # Slice type values
    SLICE_TYPES = {
        0: "P",
        1: "B",
        2: "I",
        3: "SP",
        4: "SI",
        5: "P",
        6: "B",
        7: "I",
        8: "SP",
        9: "SI",
    }

    def __init__(self):
        # Store parsed parameter sets for reference
        self.sps_list: dict[int, dict] = {}
        self.pps_list: dict[int, dict] = {}
        # Raw integer context per parameter-set id, for slice-header parsing
        # (the display dicts above hold formatted strings for some fields).
        self.sps_ctx: dict[int, dict] = {}
        self.pps_ctx: dict[int, dict] = {}

    def parse_nalu(self, nalu: NALUnit) -> dict:
        """Parse a NAL unit and return its syntax elements."""
        if nalu.is_h265:
            return {"error": "Not an H.264 NAL unit"}

        nalu_type = nalu.nal_unit_type

        try:
            if nalu_type == H264NaluType.SPS:
                return self.parse_sps(nalu)
            elif nalu_type == H264NaluType.PPS:
                return self.parse_pps(nalu)
            elif nalu_type in (H264NaluType.SLICE_NON_IDR, H264NaluType.SLICE_IDR,
                               H264NaluType.SLICE_PART_A):
                return self.parse_slice_header(nalu)
            elif nalu_type == H264NaluType.SEI:
                return self.parse_sei(nalu)
            elif nalu_type == H264NaluType.AUD:
                return self.parse_aud(nalu)
            else:
                return {"nal_unit_type": nalu_type, "type_name": nalu.type_name}
        except Exception as e:
            return {"error": str(e), "nal_unit_type": nalu_type}

    def parse_sps(self, nalu: NALUnit) -> dict:
        """Parse Sequence Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "SPS (Sequence Parameter Set)"

        # Skip NAL header byte
        reader = BitstreamReader.from_rbsp(nalu.data[1:])

        try:
            profile_idc = reader.read_u(8)
            syntax["profile_idc"] = f"{profile_idc} ({self.PROFILES.get(profile_idc, 'Unknown')})"

            constraint_flags = reader.read_u(8)
            syntax["constraint_set0_flag"] = (constraint_flags >> 7) & 1
            syntax["constraint_set1_flag"] = (constraint_flags >> 6) & 1
            syntax["constraint_set2_flag"] = (constraint_flags >> 5) & 1
            syntax["constraint_set3_flag"] = (constraint_flags >> 4) & 1
            syntax["constraint_set4_flag"] = (constraint_flags >> 3) & 1
            syntax["constraint_set5_flag"] = (constraint_flags >> 2) & 1

            level_idc = reader.read_u(8)
            syntax["level_idc"] = f"{level_idc} ({level_idc // 10}.{level_idc % 10})"

            seq_parameter_set_id = reader.read_ue()
            syntax["seq_parameter_set_id"] = seq_parameter_set_id

            # Defaults for non-High profiles (no chroma/scaling syntax present).
            chroma_format_idc = 1
            separate_colour_plane_flag = 0
            delta_pic_order_always_zero_flag = 0

            # High profile extensions
            if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138):
                chroma_format_idc = reader.read_ue()
                chroma_names = {0: "monochrome", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
                syntax["chroma_format_idc"] = f"{chroma_format_idc} ({chroma_names.get(chroma_format_idc, 'unknown')})"

                if chroma_format_idc == 3:
                    separate_colour_plane_flag = int(reader.read_flag())
                    syntax["separate_colour_plane_flag"] = separate_colour_plane_flag

                syntax["bit_depth_luma_minus8"] = reader.read_ue()
                syntax["bit_depth_chroma_minus8"] = reader.read_ue()
                syntax["qpprime_y_zero_transform_bypass_flag"] = reader.read_flag()

                seq_scaling_matrix_present_flag = reader.read_flag()
                syntax["seq_scaling_matrix_present_flag"] = seq_scaling_matrix_present_flag
                if seq_scaling_matrix_present_flag:
                    self._parse_scaling_matrix(reader, syntax, "seq",
                                               chroma_format_idc, 0)

            log2_max_frame_num_minus4 = reader.read_ue()
            syntax["log2_max_frame_num_minus4"] = log2_max_frame_num_minus4

            pic_order_cnt_type = reader.read_ue()
            syntax["pic_order_cnt_type"] = pic_order_cnt_type

            if pic_order_cnt_type == 0:
                syntax["log2_max_pic_order_cnt_lsb_minus4"] = reader.read_ue()
            elif pic_order_cnt_type == 1:
                delta_pic_order_always_zero_flag = int(reader.read_flag())
                syntax["delta_pic_order_always_zero_flag"] = delta_pic_order_always_zero_flag
                syntax["offset_for_non_ref_pic"] = reader.read_se()
                syntax["offset_for_top_to_bottom_field"] = reader.read_se()
                num_ref_frames_in_pic_order_cnt_cycle = reader.read_ue()
                syntax["num_ref_frames_in_pic_order_cnt_cycle"] = num_ref_frames_in_pic_order_cnt_cycle
                # Spec range is 0..255; clamp so corrupt input can't spin a
                # giant loop on the UI thread.
                for i in range(min(num_ref_frames_in_pic_order_cnt_cycle, 256)):
                    reader.read_se()  # offset_for_ref_frame

            syntax["max_num_ref_frames"] = reader.read_ue()
            syntax["gaps_in_frame_num_value_allowed_flag"] = reader.read_flag()

            pic_width_in_mbs_minus1 = reader.read_ue()
            pic_height_in_map_units_minus1 = reader.read_ue()
            syntax["pic_width_in_mbs_minus1"] = pic_width_in_mbs_minus1
            syntax["pic_height_in_map_units_minus1"] = pic_height_in_map_units_minus1

            frame_mbs_only_flag = reader.read_flag()
            syntax["frame_mbs_only_flag"] = frame_mbs_only_flag

            if not frame_mbs_only_flag:
                syntax["mb_adaptive_frame_field_flag"] = reader.read_flag()

            syntax["direct_8x8_inference_flag"] = reader.read_flag()

            frame_cropping_flag = reader.read_flag()
            syntax["frame_cropping_flag"] = frame_cropping_flag
            if frame_cropping_flag:
                syntax["frame_crop_left_offset"] = reader.read_ue()
                syntax["frame_crop_right_offset"] = reader.read_ue()
                syntax["frame_crop_top_offset"] = reader.read_ue()
                syntax["frame_crop_bottom_offset"] = reader.read_ue()

            # Calculate actual dimensions
            width = (pic_width_in_mbs_minus1 + 1) * 16
            height = (pic_height_in_map_units_minus1 + 1) * 16 * (2 - frame_mbs_only_flag)
            syntax["_calculated_width"] = width
            syntax["_calculated_height"] = height

            vui_parameters_present_flag = reader.read_flag()
            syntax["vui_parameters_present_flag"] = vui_parameters_present_flag
            if vui_parameters_present_flag:
                vui = self._parse_vui(reader)
                syntax["vui_parameters"] = vui

            # Store for later reference
            self.sps_list[seq_parameter_set_id] = syntax
            self.sps_ctx[seq_parameter_set_id] = {
                "chroma_format_idc": chroma_format_idc,
                "separate_colour_plane_flag": separate_colour_plane_flag,
                "log2_max_frame_num": log2_max_frame_num_minus4 + 4,
                "pic_order_cnt_type": pic_order_cnt_type,
                "log2_max_poc_lsb": syntax.get("log2_max_pic_order_cnt_lsb_minus4", 0) + 4,
                "delta_pic_order_always_zero_flag": delta_pic_order_always_zero_flag,
                "frame_mbs_only_flag": int(frame_mbs_only_flag),
            }

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_pps(self, nalu: NALUnit) -> dict:
        """Parse Picture Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "PPS (Picture Parameter Set)"

        reader = BitstreamReader.from_rbsp(nalu.data[1:])

        try:
            pic_parameter_set_id = reader.read_ue()
            syntax["pic_parameter_set_id"] = pic_parameter_set_id

            seq_parameter_set_id = reader.read_ue()
            syntax["seq_parameter_set_id"] = seq_parameter_set_id

            entropy_coding_mode_flag = int(reader.read_flag())
            syntax["entropy_coding_mode_flag"] = f"{entropy_coding_mode_flag} ({'CABAC' if entropy_coding_mode_flag else 'CAVLC'})"

            bottom_field_pic_order = int(reader.read_flag())
            syntax["bottom_field_pic_order_in_frame_present_flag"] = bottom_field_pic_order

            num_slice_groups_minus1 = reader.read_ue()
            syntax["num_slice_groups_minus1"] = num_slice_groups_minus1
            slice_group_map_type = 0
            if num_slice_groups_minus1 > 0:
                slice_group_map_type = reader.read_ue()
                syntax["slice_group_map_type"] = slice_group_map_type
                self._parse_slice_group_map(reader, syntax, slice_group_map_type,
                                            num_slice_groups_minus1)

            num_ref_idx_l0_default = reader.read_ue()
            num_ref_idx_l1_default = reader.read_ue()
            syntax["num_ref_idx_l0_default_active_minus1"] = num_ref_idx_l0_default
            syntax["num_ref_idx_l1_default_active_minus1"] = num_ref_idx_l1_default
            weighted_pred_flag = int(reader.read_flag())
            weighted_bipred_idc = reader.read_u(2)
            syntax["weighted_pred_flag"] = weighted_pred_flag
            syntax["weighted_bipred_idc"] = weighted_bipred_idc
            syntax["pic_init_qp_minus26"] = reader.read_se()
            syntax["pic_init_qs_minus26"] = reader.read_se()
            syntax["chroma_qp_index_offset"] = reader.read_se()
            deblocking_present = int(reader.read_flag())
            syntax["deblocking_filter_control_present_flag"] = deblocking_present
            syntax["constrained_intra_pred_flag"] = int(reader.read_flag())
            redundant_pic_cnt_present = int(reader.read_flag())
            syntax["redundant_pic_cnt_present_flag"] = redundant_pic_cnt_present

            # High-profile PPS extension (present when more RBSP data follows).
            transform_8x8_mode_flag = 0
            if _more_rbsp_data(reader):
                transform_8x8_mode_flag = int(reader.read_flag())
                syntax["transform_8x8_mode_flag"] = transform_8x8_mode_flag
                pic_scaling_matrix_present = int(reader.read_flag())
                syntax["pic_scaling_matrix_present_flag"] = pic_scaling_matrix_present
                if pic_scaling_matrix_present:
                    sps_chroma = self.sps_ctx.get(
                        seq_parameter_set_id, {}).get("chroma_format_idc", 1)
                    self._parse_scaling_matrix(reader, syntax, "pic", sps_chroma,
                                               transform_8x8_mode_flag)
                syntax["second_chroma_qp_index_offset"] = reader.read_se()

            # Store for later reference
            self.pps_list[pic_parameter_set_id] = syntax
            self.pps_ctx[pic_parameter_set_id] = {
                "seq_parameter_set_id": seq_parameter_set_id,
                "entropy_coding_mode_flag": entropy_coding_mode_flag,
                "bottom_field_pic_order": bottom_field_pic_order,
                "num_slice_groups_minus1": num_slice_groups_minus1,
                "slice_group_map_type": slice_group_map_type,
                "num_ref_idx_l0_default": num_ref_idx_l0_default,
                "num_ref_idx_l1_default": num_ref_idx_l1_default,
                "weighted_pred_flag": weighted_pred_flag,
                "weighted_bipred_idc": weighted_bipred_idc,
                "deblocking_present": deblocking_present,
                "redundant_pic_cnt_present": redundant_pic_cnt_present,
            }

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_slice_header(self, nalu: NALUnit) -> dict:
        """Parse the full slice header (slice_header())."""
        syntax = OrderedDict()
        is_idr = nalu.nal_unit_type == H264NaluType.SLICE_IDR
        syntax["_name"] = "IDR Slice Header" if is_idr else "Slice Header"

        reader = BitstreamReader.from_rbsp(nalu.data[1:])

        try:
            syntax["first_mb_in_slice"] = reader.read_ue()

            slice_type = reader.read_ue()
            syntax["slice_type"] = f"{slice_type} ({self.SLICE_TYPES.get(slice_type, 'Unknown')})"
            st = slice_type % 5            # 0 P, 1 B, 2 I, 3 SP, 4 SI

            pps_id = reader.read_ue()
            syntax["pic_parameter_set_id"] = pps_id
            pps = self.pps_ctx.get(pps_id, {})
            sps = self.sps_ctx.get(pps.get("seq_parameter_set_id", 0), {})

            if sps.get("separate_colour_plane_flag"):
                syntax["colour_plane_id"] = reader.read_u(2)

            syntax["frame_num"] = reader.read_u(sps.get("log2_max_frame_num", 4))

            field_pic_flag = 0
            if not sps.get("frame_mbs_only_flag", 1):
                field_pic_flag = int(reader.read_flag())
                syntax["field_pic_flag"] = field_pic_flag
                if field_pic_flag:
                    syntax["bottom_field_flag"] = int(reader.read_flag())

            if is_idr:
                syntax["idr_pic_id"] = reader.read_ue()

            if sps.get("pic_order_cnt_type", 0) == 0:
                syntax["pic_order_cnt_lsb"] = reader.read_u(sps.get("log2_max_poc_lsb", 4))
                if pps.get("bottom_field_pic_order") and not field_pic_flag:
                    syntax["delta_pic_order_cnt_bottom"] = reader.read_se()
            elif (sps.get("pic_order_cnt_type") == 1
                  and not sps.get("delta_pic_order_always_zero_flag")):
                syntax["delta_pic_order_cnt[0]"] = reader.read_se()
                if pps.get("bottom_field_pic_order") and not field_pic_flag:
                    syntax["delta_pic_order_cnt[1]"] = reader.read_se()

            if pps.get("redundant_pic_cnt_present"):
                syntax["redundant_pic_cnt"] = reader.read_ue()

            if st == 1:                   # B
                syntax["direct_spatial_mv_pred_flag"] = int(reader.read_flag())

            num_l0 = pps.get("num_ref_idx_l0_default", 0)
            num_l1 = pps.get("num_ref_idx_l1_default", 0)
            if st in (0, 1, 3):           # P, B, SP
                override = int(reader.read_flag())
                syntax["num_ref_idx_active_override_flag"] = override
                if override:
                    num_l0 = reader.read_ue()
                    syntax["num_ref_idx_l0_active_minus1"] = num_l0
                    if st == 1:
                        num_l1 = reader.read_ue()
                        syntax["num_ref_idx_l1_active_minus1"] = num_l1

            self._parse_ref_pic_list_modification(reader, syntax, st)

            chroma_array_type = (0 if sps.get("separate_colour_plane_flag")
                                 else sps.get("chroma_format_idc", 1))
            wp = pps.get("weighted_pred_flag")
            wbi = pps.get("weighted_bipred_idc")
            if (wp and st in (0, 3)) or (wbi == 1 and st == 1):
                self._parse_pred_weight_table(reader, syntax, st, num_l0, num_l1,
                                              chroma_array_type)

            if nalu.nal_ref_idc != 0:
                self._parse_dec_ref_pic_marking(reader, syntax, is_idr)

            if pps.get("entropy_coding_mode_flag") and st not in (2, 4):
                syntax["cabac_init_idc"] = reader.read_ue()

            syntax["slice_qp_delta"] = reader.read_se()

            if st in (3, 4):              # SP, SI
                if st == 3:
                    syntax["sp_for_switch_flag"] = int(reader.read_flag())
                syntax["slice_qs_delta"] = reader.read_se()

            if pps.get("deblocking_present"):
                idc = reader.read_ue()
                syntax["disable_deblocking_filter_idc"] = idc
                if idc != 1:
                    syntax["slice_alpha_c0_offset_div2"] = reader.read_se()
                    syntax["slice_beta_offset_div2"] = reader.read_se()

            ng = pps.get("num_slice_groups_minus1", 0)
            if ng > 0 and pps.get("slice_group_map_type", 0) in (3, 4, 5):
                syntax["slice_group_change_cycle"] = "(present)"

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    # ---- slice-header sub-structures ------------------------------------ #

    def _parse_ref_pic_list_modification(self, reader, syntax, st) -> None:
        def one(suffix):
            flag = int(reader.read_flag())
            syntax[f"ref_pic_list_modification_flag_{suffix}"] = flag
            if flag:
                i = 0
                while True:
                    idc = reader.read_ue()
                    syntax[f"modification_of_pic_nums_idc[{suffix}][{i}]"] = idc
                    if idc in (0, 1):
                        syntax[f"abs_diff_pic_num_minus1[{suffix}][{i}]"] = reader.read_ue()
                    elif idc == 2:
                        syntax[f"long_term_pic_num[{suffix}][{i}]"] = reader.read_ue()
                    if idc == 3 or i > 64:
                        break
                    i += 1
        if st not in (2, 4):              # not I, not SI
            one("l0")
        if st == 1:                       # B
            one("l1")

    def _parse_pred_weight_table(self, reader, syntax, st, num_l0, num_l1,
                                 chroma_array_type) -> None:
        syntax["luma_log2_weight_denom"] = reader.read_ue()
        if chroma_array_type != 0:
            syntax["chroma_log2_weight_denom"] = reader.read_ue()

        def weights(suffix, count):
            for i in range(count + 1):
                lf = int(reader.read_flag())
                syntax[f"luma_weight_{suffix}_flag[{i}]"] = lf
                if lf:
                    syntax[f"luma_weight_{suffix}[{i}]"] = reader.read_se()
                    syntax[f"luma_offset_{suffix}[{i}]"] = reader.read_se()
                if chroma_array_type != 0:
                    cf = int(reader.read_flag())
                    syntax[f"chroma_weight_{suffix}_flag[{i}]"] = cf
                    if cf:
                        for j in range(2):
                            syntax[f"chroma_weight_{suffix}[{i}][{j}]"] = reader.read_se()
                            syntax[f"chroma_offset_{suffix}[{i}][{j}]"] = reader.read_se()
        weights("l0", num_l0)
        if st == 1:
            weights("l1", num_l1)

    def _parse_dec_ref_pic_marking(self, reader, syntax, is_idr) -> None:
        if is_idr:
            syntax["no_output_of_prior_pics_flag"] = int(reader.read_flag())
            syntax["long_term_reference_flag"] = int(reader.read_flag())
        else:
            adaptive = int(reader.read_flag())
            syntax["adaptive_ref_pic_marking_mode_flag"] = adaptive
            if adaptive:
                i = 0
                while True:
                    mmco = reader.read_ue()
                    syntax[f"memory_management_control_operation[{i}]"] = mmco
                    if mmco in (1, 3):
                        syntax[f"difference_of_pic_nums_minus1[{i}]"] = reader.read_ue()
                    if mmco == 2:
                        syntax[f"long_term_pic_num[{i}]"] = reader.read_ue()
                    if mmco in (3, 6):
                        syntax[f"long_term_frame_idx[{i}]"] = reader.read_ue()
                    if mmco == 4:
                        syntax[f"max_long_term_frame_idx_plus1[{i}]"] = reader.read_ue()
                    if mmco == 0 or i > 64:
                        break
                    i += 1

    def parse_sei(self, nalu: NALUnit) -> dict:
        """Parse SEI message (basic)."""
        syntax = OrderedDict()
        syntax["_name"] = "SEI (Supplemental Enhancement Information)"

        reader = BitstreamReader.from_rbsp(nalu.data[1:])

        try:
            payload_type, payload_size = read_sei_payload_header(reader)

            sei_types = {
                0: "buffering_period",
                1: "pic_timing",
                2: "pan_scan_rect",
                3: "filler_payload",
                4: "user_data_registered_itu_t_t35",
                5: "user_data_unregistered",
                6: "recovery_point",
            }

            syntax["payload_type"] = f"{payload_type} ({sei_types.get(payload_type, 'unknown')})"
            syntax["payload_size"] = payload_size

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_aud(self, nalu: NALUnit) -> dict:
        """Parse Access Unit Delimiter."""
        syntax = OrderedDict()
        syntax["_name"] = "AUD (Access Unit Delimiter)"

        reader = BitstreamReader.from_rbsp(nalu.data[1:])

        try:
            primary_pic_type = reader.read_u(3)
            pic_types = {
                0: "I",
                1: "I, P",
                2: "I, P, B",
                3: "SI",
                4: "SI, SP",
                5: "I, SI",
                6: "I, SI, P, SP",
                7: "I, SI, P, SP, B",
            }
            syntax["primary_pic_type"] = f"{primary_pic_type} ({pic_types.get(primary_pic_type, 'unknown')})"

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def _parse_vui(self, reader: BitstreamReader) -> dict:
        """Parse VUI parameters."""
        vui = OrderedDict()

        aspect_ratio_info_present_flag = reader.read_flag()
        vui["aspect_ratio_info_present_flag"] = aspect_ratio_info_present_flag
        if aspect_ratio_info_present_flag:
            aspect_ratio_idc = reader.read_u(8)
            vui["aspect_ratio_idc"] = aspect_ratio_idc
            if aspect_ratio_idc == 255:  # Extended_SAR
                vui["sar_width"] = reader.read_u(16)
                vui["sar_height"] = reader.read_u(16)

        overscan_info_present_flag = reader.read_flag()
        if overscan_info_present_flag:
            vui["overscan_appropriate_flag"] = reader.read_flag()

        video_signal_type_present_flag = reader.read_flag()
        if video_signal_type_present_flag:
            vui["video_format"] = reader.read_u(3)
            vui["video_full_range_flag"] = reader.read_flag()
            colour_description_present_flag = reader.read_flag()
            if colour_description_present_flag:
                vui["colour_primaries"] = reader.read_u(8)
                vui["transfer_characteristics"] = reader.read_u(8)
                vui["matrix_coefficients"] = reader.read_u(8)

        chroma_loc_info_present_flag = reader.read_flag()
        if chroma_loc_info_present_flag:
            vui["chroma_sample_loc_type_top_field"] = reader.read_ue()
            vui["chroma_sample_loc_type_bottom_field"] = reader.read_ue()

        timing_info_present_flag = reader.read_flag()
        vui["timing_info_present_flag"] = int(timing_info_present_flag)
        if timing_info_present_flag:
            num_units_in_tick = reader.read_u(32)
            time_scale = reader.read_u(32)
            vui["num_units_in_tick"] = num_units_in_tick
            vui["time_scale"] = time_scale
            vui["fixed_frame_rate_flag"] = int(reader.read_flag())
            if num_units_in_tick > 0:
                vui["_calculated_fps"] = time_scale / (2 * num_units_in_tick)

        nal_hrd = int(reader.read_flag())
        vui["nal_hrd_parameters_present_flag"] = nal_hrd
        if nal_hrd:
            self._parse_hrd_parameters(reader, vui, "nal")
        vcl_hrd = int(reader.read_flag())
        vui["vcl_hrd_parameters_present_flag"] = vcl_hrd
        if vcl_hrd:
            self._parse_hrd_parameters(reader, vui, "vcl")
        if nal_hrd or vcl_hrd:
            vui["low_delay_hrd_flag"] = int(reader.read_flag())
        vui["pic_struct_present_flag"] = int(reader.read_flag())

        bitstream_restriction = int(reader.read_flag())
        vui["bitstream_restriction_flag"] = bitstream_restriction
        if bitstream_restriction:
            vui["motion_vectors_over_pic_boundaries_flag"] = int(reader.read_flag())
            vui["max_bytes_per_pic_denom"] = reader.read_ue()
            vui["max_bits_per_mb_denom"] = reader.read_ue()
            vui["log2_max_mv_length_horizontal"] = reader.read_ue()
            vui["log2_max_mv_length_vertical"] = reader.read_ue()
            vui["max_num_reorder_frames"] = reader.read_ue()
            vui["max_dec_frame_buffering"] = reader.read_ue()

        return vui

    def _parse_hrd_parameters(self, reader: BitstreamReader, parent: dict,
                              prefix: str) -> None:
        """hrd_parameters() (shared by NAL and VCL HRD)."""
        cpb_cnt_minus1 = reader.read_ue()
        parent[f"{prefix}_cpb_cnt_minus1"] = cpb_cnt_minus1
        parent[f"{prefix}_bit_rate_scale"] = reader.read_u(4)
        parent[f"{prefix}_cpb_size_scale"] = reader.read_u(4)
        for i in range(min(cpb_cnt_minus1 + 1, 32)):
            reader.read_ue()              # bit_rate_value_minus1[i]
            reader.read_ue()              # cpb_size_value_minus1[i]
            reader.read_flag()            # cbr_flag[i]
        parent[f"{prefix}_initial_cpb_removal_delay_length_minus1"] = reader.read_u(5)
        parent[f"{prefix}_cpb_removal_delay_length_minus1"] = reader.read_u(5)
        parent[f"{prefix}_dpb_output_delay_length_minus1"] = reader.read_u(5)
        parent[f"{prefix}_time_offset_length"] = reader.read_u(5)

    def _parse_scaling_list(self, reader: BitstreamReader, size: int):
        """scaling_list(): returns the delta_scale list, or 'use_default'."""
        last, nxt = 8, 8
        deltas = []
        use_default = False
        for j in range(size):
            if nxt != 0:
                delta = reader.read_se()
                deltas.append(delta)
                nxt = (last + delta + 256) % 256
                if j == 0 and nxt == 0:
                    use_default = True
            last = nxt if nxt != 0 else last
        return "use_default" if use_default else deltas

    def _parse_scaling_matrix(self, reader: BitstreamReader, syntax: dict,
                              prefix: str, chroma_format_idc: int,
                              transform_8x8: int) -> None:
        """seq/pic scaling matrix: the present flags and each present list."""
        if prefix == "seq":
            count = 12 if chroma_format_idc == 3 else 8
        else:                              # pic
            count = 6 + (6 if chroma_format_idc == 3 else 2) * transform_8x8
        for i in range(count):
            present = int(reader.read_flag())
            syntax[f"{prefix}_scaling_list_present_flag[{i}]"] = present
            if present:
                lst = self._parse_scaling_list(reader, 16 if i < 6 else 64)
                syntax[f"{prefix}_scaling_list[{i}]"] = (
                    lst if lst == "use_default" else f"{len(lst)} delta_scale")

    def _parse_slice_group_map(self, reader: BitstreamReader, syntax: dict,
                               map_type: int, num_groups_minus1: int) -> None:
        """PPS FMO slice_group_map (rare; x264 doesn't emit it)."""
        if map_type == 0:
            for i in range(num_groups_minus1 + 1):
                syntax[f"run_length_minus1[{i}]"] = reader.read_ue()
        elif map_type == 2:
            for i in range(num_groups_minus1):
                syntax[f"top_left[{i}]"] = reader.read_ue()
                syntax[f"bottom_right[{i}]"] = reader.read_ue()
        elif map_type in (3, 4, 5):
            syntax["slice_group_change_direction_flag"] = int(reader.read_flag())
            syntax["slice_group_change_rate_minus1"] = reader.read_ue()
        elif map_type == 6:
            n = reader.read_ue()
            syntax["pic_size_in_map_units_minus1"] = n
            bits = max(1, (num_groups_minus1).bit_length())
            for i in range(min(n + 1, 4096)):
                reader.read_u(bits)        # slice_group_id[i]

    def get_slice_type(self, nalu: NALUnit) -> Optional[str]:
        """Get slice type from slice NAL unit."""
        if not nalu.is_slice():
            return None

        try:
            reader = BitstreamReader.from_rbsp(nalu.data[1:])
            reader.read_ue()  # first_mb_in_slice
            slice_type = reader.read_ue()
            return self.SLICE_TYPES.get(slice_type % 5, "?")
        except:
            return None
