"""H.264/AVC bitstream parser."""

from typing import Optional
from collections import OrderedDict

from .nalu_parser import NALUnit, H264NaluType
from ._common import read_sei_payload_header
from ..utils.bitstream_reader import BitstreamReader


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

            # High profile extensions
            if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138):
                chroma_format_idc = reader.read_ue()
                chroma_names = {0: "monochrome", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
                syntax["chroma_format_idc"] = f"{chroma_format_idc} ({chroma_names.get(chroma_format_idc, 'unknown')})"

                if chroma_format_idc == 3:
                    syntax["separate_colour_plane_flag"] = reader.read_flag()

                syntax["bit_depth_luma_minus8"] = reader.read_ue()
                syntax["bit_depth_chroma_minus8"] = reader.read_ue()
                syntax["qpprime_y_zero_transform_bypass_flag"] = reader.read_flag()

                seq_scaling_matrix_present_flag = reader.read_flag()
                syntax["seq_scaling_matrix_present_flag"] = seq_scaling_matrix_present_flag
                if seq_scaling_matrix_present_flag:
                    # Skip scaling matrices
                    n_scaling_list = 12 if chroma_format_idc == 3 else 8
                    for i in range(n_scaling_list):
                        if reader.read_flag():  # scaling_list_present_flag
                            size = 16 if i < 6 else 64
                            self._skip_scaling_list(reader, size)

            log2_max_frame_num_minus4 = reader.read_ue()
            syntax["log2_max_frame_num_minus4"] = log2_max_frame_num_minus4

            pic_order_cnt_type = reader.read_ue()
            syntax["pic_order_cnt_type"] = pic_order_cnt_type

            if pic_order_cnt_type == 0:
                syntax["log2_max_pic_order_cnt_lsb_minus4"] = reader.read_ue()
            elif pic_order_cnt_type == 1:
                syntax["delta_pic_order_always_zero_flag"] = reader.read_flag()
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

            entropy_coding_mode_flag = reader.read_flag()
            syntax["entropy_coding_mode_flag"] = f"{entropy_coding_mode_flag} ({'CABAC' if entropy_coding_mode_flag else 'CAVLC'})"

            syntax["bottom_field_pic_order_in_frame_present_flag"] = reader.read_flag()

            num_slice_groups_minus1 = reader.read_ue()
            syntax["num_slice_groups_minus1"] = num_slice_groups_minus1

            if num_slice_groups_minus1 > 0:
                slice_group_map_type = reader.read_ue()
                syntax["slice_group_map_type"] = slice_group_map_type
                # Skip slice group map parsing for simplicity

            syntax["num_ref_idx_l0_default_active_minus1"] = reader.read_ue()
            syntax["num_ref_idx_l1_default_active_minus1"] = reader.read_ue()
            syntax["weighted_pred_flag"] = reader.read_flag()
            syntax["weighted_bipred_idc"] = reader.read_u(2)
            syntax["pic_init_qp_minus26"] = reader.read_se()
            syntax["pic_init_qs_minus26"] = reader.read_se()
            syntax["chroma_qp_index_offset"] = reader.read_se()
            syntax["deblocking_filter_control_present_flag"] = reader.read_flag()
            syntax["constrained_intra_pred_flag"] = reader.read_flag()
            syntax["redundant_pic_cnt_present_flag"] = reader.read_flag()

            # Store for later reference
            self.pps_list[pic_parameter_set_id] = syntax

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_slice_header(self, nalu: NALUnit) -> dict:
        """Parse slice header."""
        syntax = OrderedDict()
        is_idr = nalu.nal_unit_type == H264NaluType.SLICE_IDR
        syntax["_name"] = "IDR Slice Header" if is_idr else "Slice Header"

        reader = BitstreamReader.from_rbsp(nalu.data[1:])

        try:
            first_mb_in_slice = reader.read_ue()
            syntax["first_mb_in_slice"] = first_mb_in_slice

            slice_type = reader.read_ue()
            syntax["slice_type"] = f"{slice_type} ({self.SLICE_TYPES.get(slice_type, 'Unknown')})"

            pic_parameter_set_id = reader.read_ue()
            syntax["pic_parameter_set_id"] = pic_parameter_set_id

            # Get SPS/PPS for context
            pps = self.pps_list.get(pic_parameter_set_id, {})
            sps_id = pps.get("seq_parameter_set_id", 0)
            sps = self.sps_list.get(sps_id, {})

            # Get log2_max_frame_num_minus4 from SPS
            log2_max_frame_num = sps.get("log2_max_frame_num_minus4", 0) + 4

            frame_num = reader.read_u(log2_max_frame_num)
            syntax["frame_num"] = frame_num

            frame_mbs_only_flag = sps.get("frame_mbs_only_flag", True)
            if not frame_mbs_only_flag:
                field_pic_flag = reader.read_flag()
                syntax["field_pic_flag"] = field_pic_flag
                if field_pic_flag:
                    syntax["bottom_field_flag"] = reader.read_flag()

            if is_idr:
                idr_pic_id = reader.read_ue()
                syntax["idr_pic_id"] = idr_pic_id

            pic_order_cnt_type = sps.get("pic_order_cnt_type", 0)
            if pic_order_cnt_type == 0:
                log2_max_poc = sps.get("log2_max_pic_order_cnt_lsb_minus4", 0) + 4
                pic_order_cnt_lsb = reader.read_u(log2_max_poc)
                syntax["pic_order_cnt_lsb"] = pic_order_cnt_lsb

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

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
        vui["timing_info_present_flag"] = timing_info_present_flag
        if timing_info_present_flag:
            num_units_in_tick = reader.read_u(32)
            time_scale = reader.read_u(32)
            vui["num_units_in_tick"] = num_units_in_tick
            vui["time_scale"] = time_scale
            vui["fixed_frame_rate_flag"] = reader.read_flag()
            if num_units_in_tick > 0:
                vui["_calculated_fps"] = time_scale / (2 * num_units_in_tick)

        return vui

    def _skip_scaling_list(self, reader: BitstreamReader, size: int) -> None:
        """Skip scaling list."""
        last_scale = 8
        next_scale = 8
        for _ in range(size):
            if next_scale != 0:
                delta_scale = reader.read_se()
                next_scale = (last_scale + delta_scale + 256) % 256
            last_scale = next_scale if next_scale != 0 else last_scale

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
