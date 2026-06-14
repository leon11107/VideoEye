"""H.265/HEVC bitstream parser."""

from typing import Optional
from collections import OrderedDict

from .nalu_parser import NALUnit, H265NaluType
from ._common import read_sei_payload_header
from ..utils.bitstream_reader import BitstreamReader


class H265Parser:
    """Parses H.265/HEVC NAL unit syntax."""

    # Profile IDC values
    PROFILES = {
        1: "Main",
        2: "Main 10",
        3: "Main Still Picture",
        4: "Format Range Extensions",
        5: "High Throughput",
        6: "Multiview Main",
        7: "Scalable Main",
        8: "3D Main",
        9: "Screen Content Coding",
        10: "Scalable Format Range Extensions",
        11: "High Throughput Screen Content Coding",
    }

    # Slice type values
    SLICE_TYPES = {
        0: "B",
        1: "P",
        2: "I",
    }

    def __init__(self):
        # Store parsed parameter sets for reference
        self.vps_list: dict[int, dict] = {}
        self.sps_list: dict[int, dict] = {}
        self.pps_list: dict[int, dict] = {}

    def parse_nalu(self, nalu: NALUnit) -> dict:
        """Parse a NAL unit and return its syntax elements."""
        if not nalu.is_h265:
            return {"error": "Not an H.265 NAL unit"}

        nalu_type = nalu.nal_unit_type

        try:
            if nalu_type == H265NaluType.VPS:
                return self.parse_vps(nalu)
            elif nalu_type == H265NaluType.SPS:
                return self.parse_sps(nalu)
            elif nalu_type == H265NaluType.PPS:
                return self.parse_pps(nalu)
            elif nalu_type in (H265NaluType.PREFIX_SEI, H265NaluType.SUFFIX_SEI):
                return self.parse_sei(nalu)
            elif nalu_type == H265NaluType.AUD:
                return self.parse_aud(nalu)
            elif 0 <= nalu_type <= 21:  # VCL NAL units (slices)
                return self.parse_slice_header(nalu)
            else:
                return {"nal_unit_type": nalu_type, "type_name": nalu.type_name}
        except Exception as e:
            return {"error": str(e), "nal_unit_type": nalu_type}

    def parse_vps(self, nalu: NALUnit) -> dict:
        """Parse Video Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "VPS (Video Parameter Set)"

        # Skip 2-byte NAL header
        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            vps_video_parameter_set_id = reader.read_u(4)
            syntax["vps_video_parameter_set_id"] = vps_video_parameter_set_id

            syntax["vps_base_layer_internal_flag"] = reader.read_flag()
            syntax["vps_base_layer_available_flag"] = reader.read_flag()
            syntax["vps_max_layers_minus1"] = reader.read_u(6)

            vps_max_sub_layers_minus1 = reader.read_u(3)
            syntax["vps_max_sub_layers_minus1"] = vps_max_sub_layers_minus1

            syntax["vps_temporal_id_nesting_flag"] = reader.read_flag()

            # vps_reserved_0xffff_16bits
            reader.read_u(16)

            # profile_tier_level
            ptl = self._parse_profile_tier_level(reader, True, vps_max_sub_layers_minus1)
            syntax["profile_tier_level"] = ptl

            vps_sub_layer_ordering_info_present_flag = reader.read_flag()
            syntax["vps_sub_layer_ordering_info_present_flag"] = vps_sub_layer_ordering_info_present_flag

            start = 0 if vps_sub_layer_ordering_info_present_flag else vps_max_sub_layers_minus1
            for i in range(start, vps_max_sub_layers_minus1 + 1):
                syntax[f"vps_max_dec_pic_buffering_minus1[{i}]"] = reader.read_ue()
                syntax[f"vps_max_num_reorder_pics[{i}]"] = reader.read_ue()
                syntax[f"vps_max_latency_increase_plus1[{i}]"] = reader.read_ue()

            vps_max_layer_id = reader.read_u(6)
            syntax["vps_max_layer_id"] = vps_max_layer_id

            vps_num_layer_sets_minus1 = reader.read_ue()
            syntax["vps_num_layer_sets_minus1"] = vps_num_layer_sets_minus1

            # Store for later reference
            self.vps_list[vps_video_parameter_set_id] = syntax

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_sps(self, nalu: NALUnit) -> dict:
        """Parse Sequence Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "SPS (Sequence Parameter Set)"

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            sps_video_parameter_set_id = reader.read_u(4)
            syntax["sps_video_parameter_set_id"] = sps_video_parameter_set_id

            sps_max_sub_layers_minus1 = reader.read_u(3)
            syntax["sps_max_sub_layers_minus1"] = sps_max_sub_layers_minus1

            syntax["sps_temporal_id_nesting_flag"] = reader.read_flag()

            # profile_tier_level
            ptl = self._parse_profile_tier_level(reader, True, sps_max_sub_layers_minus1)
            syntax["profile_tier_level"] = ptl

            sps_seq_parameter_set_id = reader.read_ue()
            syntax["sps_seq_parameter_set_id"] = sps_seq_parameter_set_id

            chroma_format_idc = reader.read_ue()
            chroma_names = {0: "monochrome", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
            syntax["chroma_format_idc"] = f"{chroma_format_idc} ({chroma_names.get(chroma_format_idc, 'unknown')})"

            if chroma_format_idc == 3:
                syntax["separate_colour_plane_flag"] = reader.read_flag()

            pic_width_in_luma_samples = reader.read_ue()
            pic_height_in_luma_samples = reader.read_ue()
            syntax["pic_width_in_luma_samples"] = pic_width_in_luma_samples
            syntax["pic_height_in_luma_samples"] = pic_height_in_luma_samples

            conformance_window_flag = reader.read_flag()
            syntax["conformance_window_flag"] = conformance_window_flag
            if conformance_window_flag:
                syntax["conf_win_left_offset"] = reader.read_ue()
                syntax["conf_win_right_offset"] = reader.read_ue()
                syntax["conf_win_top_offset"] = reader.read_ue()
                syntax["conf_win_bottom_offset"] = reader.read_ue()

            bit_depth_luma_minus8 = reader.read_ue()
            bit_depth_chroma_minus8 = reader.read_ue()
            syntax["bit_depth_luma_minus8"] = bit_depth_luma_minus8
            syntax["bit_depth_chroma_minus8"] = bit_depth_chroma_minus8

            log2_max_pic_order_cnt_lsb_minus4 = reader.read_ue()
            syntax["log2_max_pic_order_cnt_lsb_minus4"] = log2_max_pic_order_cnt_lsb_minus4

            sps_sub_layer_ordering_info_present_flag = reader.read_flag()
            syntax["sps_sub_layer_ordering_info_present_flag"] = sps_sub_layer_ordering_info_present_flag

            start = 0 if sps_sub_layer_ordering_info_present_flag else sps_max_sub_layers_minus1
            for i in range(start, sps_max_sub_layers_minus1 + 1):
                syntax[f"sps_max_dec_pic_buffering_minus1[{i}]"] = reader.read_ue()
                syntax[f"sps_max_num_reorder_pics[{i}]"] = reader.read_ue()
                syntax[f"sps_max_latency_increase_plus1[{i}]"] = reader.read_ue()

            log2_min_luma_coding_block_size_minus3 = reader.read_ue()
            log2_diff_max_min_luma_coding_block_size = reader.read_ue()
            syntax["log2_min_luma_coding_block_size_minus3"] = log2_min_luma_coding_block_size_minus3
            syntax["log2_diff_max_min_luma_coding_block_size"] = log2_diff_max_min_luma_coding_block_size

            # Calculate CTU size
            min_cb_log2 = log2_min_luma_coding_block_size_minus3 + 3
            ctb_log2 = min_cb_log2 + log2_diff_max_min_luma_coding_block_size
            syntax["_calculated_ctu_size"] = 1 << ctb_log2

            syntax["log2_min_luma_transform_block_size_minus2"] = reader.read_ue()
            syntax["log2_diff_max_min_luma_transform_block_size"] = reader.read_ue()
            syntax["max_transform_hierarchy_depth_inter"] = reader.read_ue()
            syntax["max_transform_hierarchy_depth_intra"] = reader.read_ue()

            scaling_list_enabled_flag = reader.read_flag()
            syntax["scaling_list_enabled_flag"] = scaling_list_enabled_flag
            if scaling_list_enabled_flag:
                sps_scaling_list_data_present_flag = reader.read_flag()
                syntax["sps_scaling_list_data_present_flag"] = sps_scaling_list_data_present_flag
                if sps_scaling_list_data_present_flag:
                    # Skip scaling list data
                    pass

            syntax["amp_enabled_flag"] = reader.read_flag()
            syntax["sample_adaptive_offset_enabled_flag"] = reader.read_flag()

            pcm_enabled_flag = reader.read_flag()
            syntax["pcm_enabled_flag"] = pcm_enabled_flag
            if pcm_enabled_flag:
                syntax["pcm_sample_bit_depth_luma_minus1"] = reader.read_u(4)
                syntax["pcm_sample_bit_depth_chroma_minus1"] = reader.read_u(4)
                syntax["log2_min_pcm_luma_coding_block_size_minus3"] = reader.read_ue()
                syntax["log2_diff_max_min_pcm_luma_coding_block_size"] = reader.read_ue()
                syntax["pcm_loop_filter_disabled_flag"] = reader.read_flag()

            num_short_term_ref_pic_sets = reader.read_ue()
            syntax["num_short_term_ref_pic_sets"] = num_short_term_ref_pic_sets

            # Store for later reference
            self.sps_list[sps_seq_parameter_set_id] = syntax

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_pps(self, nalu: NALUnit) -> dict:
        """Parse Picture Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "PPS (Picture Parameter Set)"

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            pps_pic_parameter_set_id = reader.read_ue()
            syntax["pps_pic_parameter_set_id"] = pps_pic_parameter_set_id

            pps_seq_parameter_set_id = reader.read_ue()
            syntax["pps_seq_parameter_set_id"] = pps_seq_parameter_set_id

            syntax["dependent_slice_segments_enabled_flag"] = reader.read_flag()
            syntax["output_flag_present_flag"] = reader.read_flag()
            syntax["num_extra_slice_header_bits"] = reader.read_u(3)
            syntax["sign_data_hiding_enabled_flag"] = reader.read_flag()
            syntax["cabac_init_present_flag"] = reader.read_flag()

            syntax["num_ref_idx_l0_default_active_minus1"] = reader.read_ue()
            syntax["num_ref_idx_l1_default_active_minus1"] = reader.read_ue()

            syntax["init_qp_minus26"] = reader.read_se()
            syntax["constrained_intra_pred_flag"] = reader.read_flag()
            syntax["transform_skip_enabled_flag"] = reader.read_flag()

            cu_qp_delta_enabled_flag = reader.read_flag()
            syntax["cu_qp_delta_enabled_flag"] = cu_qp_delta_enabled_flag
            if cu_qp_delta_enabled_flag:
                syntax["diff_cu_qp_delta_depth"] = reader.read_ue()

            syntax["pps_cb_qp_offset"] = reader.read_se()
            syntax["pps_cr_qp_offset"] = reader.read_se()
            syntax["pps_slice_chroma_qp_offsets_present_flag"] = reader.read_flag()
            syntax["weighted_pred_flag"] = reader.read_flag()
            syntax["weighted_bipred_flag"] = reader.read_flag()
            syntax["transquant_bypass_enabled_flag"] = reader.read_flag()

            tiles_enabled_flag = reader.read_flag()
            entropy_coding_sync_enabled_flag = reader.read_flag()
            syntax["tiles_enabled_flag"] = tiles_enabled_flag
            syntax["entropy_coding_sync_enabled_flag"] = entropy_coding_sync_enabled_flag

            if tiles_enabled_flag:
                num_tile_columns_minus1 = reader.read_ue()
                num_tile_rows_minus1 = reader.read_ue()
                syntax["num_tile_columns_minus1"] = num_tile_columns_minus1
                syntax["num_tile_rows_minus1"] = num_tile_rows_minus1
                uniform_spacing_flag = reader.read_flag()
                syntax["uniform_spacing_flag"] = uniform_spacing_flag
                if not uniform_spacing_flag:
                    # Clamp tile counts (corrupt input could be enormous and
                    # spin a giant loop / build a huge dict on the UI thread).
                    for i in range(min(num_tile_columns_minus1, 1024)):
                        syntax[f"column_width_minus1[{i}]"] = reader.read_ue()
                    for i in range(min(num_tile_rows_minus1, 1024)):
                        syntax[f"row_height_minus1[{i}]"] = reader.read_ue()
                syntax["loop_filter_across_tiles_enabled_flag"] = reader.read_flag()

            syntax["pps_loop_filter_across_slices_enabled_flag"] = reader.read_flag()

            deblocking_filter_control_present_flag = reader.read_flag()
            syntax["deblocking_filter_control_present_flag"] = deblocking_filter_control_present_flag
            if deblocking_filter_control_present_flag:
                syntax["deblocking_filter_override_enabled_flag"] = reader.read_flag()
                pps_deblocking_filter_disabled_flag = reader.read_flag()
                syntax["pps_deblocking_filter_disabled_flag"] = pps_deblocking_filter_disabled_flag
                if not pps_deblocking_filter_disabled_flag:
                    syntax["pps_beta_offset_div2"] = reader.read_se()
                    syntax["pps_tc_offset_div2"] = reader.read_se()

            # Store for later reference
            self.pps_list[pps_pic_parameter_set_id] = syntax

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_slice_header(self, nalu: NALUnit) -> dict:
        """Parse slice segment header."""
        syntax = OrderedDict()

        is_idr = nalu.nal_unit_type in (H265NaluType.IDR_W_RADL, H265NaluType.IDR_N_LP)
        is_irap = 16 <= nalu.nal_unit_type <= 23

        if is_idr:
            syntax["_name"] = "IDR Slice Segment Header"
        elif is_irap:
            syntax["_name"] = "IRAP Slice Segment Header"
        else:
            syntax["_name"] = "Slice Segment Header"

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            first_slice_segment_in_pic_flag = reader.read_flag()
            syntax["first_slice_segment_in_pic_flag"] = first_slice_segment_in_pic_flag

            if is_irap:
                syntax["no_output_of_prior_pics_flag"] = reader.read_flag()

            slice_pic_parameter_set_id = reader.read_ue()
            syntax["slice_pic_parameter_set_id"] = slice_pic_parameter_set_id

            # Get PPS/SPS for context
            pps = self.pps_list.get(slice_pic_parameter_set_id, {})
            sps_id = pps.get("pps_seq_parameter_set_id", 0)
            sps = self.sps_list.get(sps_id, {})

            dependent_slice_segments_enabled_flag = pps.get("dependent_slice_segments_enabled_flag", False)

            if not first_slice_segment_in_pic_flag:
                if dependent_slice_segments_enabled_flag:
                    dependent_slice_segment_flag = reader.read_flag()
                    syntax["dependent_slice_segment_flag"] = dependent_slice_segment_flag

                # Calculate slice_segment_address bits
                pic_width = sps.get("pic_width_in_luma_samples", 0)
                pic_height = sps.get("pic_height_in_luma_samples", 0)
                ctu_size = sps.get("_calculated_ctu_size", 64)
                if pic_width > 0 and pic_height > 0:
                    pic_width_in_ctbs = (pic_width + ctu_size - 1) // ctu_size
                    pic_height_in_ctbs = (pic_height + ctu_size - 1) // ctu_size
                    pic_size_in_ctbs = pic_width_in_ctbs * pic_height_in_ctbs
                    addr_bits = (pic_size_in_ctbs - 1).bit_length()
                    if addr_bits > 0:
                        slice_segment_address = reader.read_u(addr_bits)
                        syntax["slice_segment_address"] = slice_segment_address

            num_extra_slice_header_bits = pps.get("num_extra_slice_header_bits", 0)
            for i in range(num_extra_slice_header_bits):
                reader.read_flag()  # slice_reserved_flag

            slice_type = reader.read_ue()
            syntax["slice_type"] = f"{slice_type} ({self.SLICE_TYPES.get(slice_type, 'Unknown')})"

            output_flag_present_flag = pps.get("output_flag_present_flag", False)
            if output_flag_present_flag:
                syntax["pic_output_flag"] = reader.read_flag()

            # POC for non-IDR
            if not is_idr:
                log2_max_poc = sps.get("log2_max_pic_order_cnt_lsb_minus4", 0) + 4
                slice_pic_order_cnt_lsb = reader.read_u(log2_max_poc)
                syntax["slice_pic_order_cnt_lsb"] = slice_pic_order_cnt_lsb

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def parse_sei(self, nalu: NALUnit) -> dict:
        """Parse SEI message (basic)."""
        syntax = OrderedDict()
        is_prefix = nalu.nal_unit_type == H265NaluType.PREFIX_SEI
        syntax["_name"] = "Prefix SEI" if is_prefix else "Suffix SEI"

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

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
                129: "active_parameter_sets",
                130: "decoding_unit_info",
                131: "temporal_sub_layer_zero_index",
                132: "decoded_picture_hash",
                133: "scalable_nesting",
                134: "region_refresh_info",
                137: "mastering_display_colour_volume",
                144: "content_light_level_info",
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

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            pic_type = reader.read_u(3)
            pic_types = {
                0: "I",
                1: "P, I",
                2: "B, P, I",
            }
            syntax["pic_type"] = f"{pic_type} ({pic_types.get(pic_type, 'unknown')})"

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def _parse_profile_tier_level(self, reader: BitstreamReader,
                                   profile_present_flag: bool,
                                   max_sub_layers_minus1: int) -> dict:
        """Parse profile_tier_level structure."""
        ptl = OrderedDict()

        if profile_present_flag:
            general_profile_space = reader.read_u(2)
            ptl["general_profile_space"] = general_profile_space
            ptl["general_tier_flag"] = reader.read_flag()

            general_profile_idc = reader.read_u(5)
            ptl["general_profile_idc"] = f"{general_profile_idc} ({self.PROFILES.get(general_profile_idc, 'Unknown')})"

            # general_profile_compatibility_flag[32]
            profile_compat = reader.read_u(32)
            ptl["general_profile_compatibility_flags"] = f"0x{profile_compat:08x}"

            ptl["general_progressive_source_flag"] = reader.read_flag()
            ptl["general_interlaced_source_flag"] = reader.read_flag()
            ptl["general_non_packed_constraint_flag"] = reader.read_flag()
            ptl["general_frame_only_constraint_flag"] = reader.read_flag()

            # Skip 44 bits of reserved/constraint flags
            reader.read_u(32)
            reader.read_u(12)

        general_level_idc = reader.read_u(8)
        ptl["general_level_idc"] = f"{general_level_idc} ({general_level_idc // 30}.{(general_level_idc % 30) // 3})"

        # Sub-layer flags
        sub_layer_profile_present = []
        sub_layer_level_present = []
        for i in range(max_sub_layers_minus1):
            sub_layer_profile_present.append(reader.read_flag())
            sub_layer_level_present.append(reader.read_flag())

        if max_sub_layers_minus1 > 0:
            for i in range(max_sub_layers_minus1, 8):
                reader.read_u(2)  # reserved_zero_2bits

        return ptl

    def get_slice_type(self, nalu: NALUnit) -> Optional[str]:
        """Get slice type from slice NAL unit."""
        if not nalu.is_slice():
            return None

        try:
            reader = BitstreamReader.from_rbsp(nalu.data[2:])

            first_slice_flag = reader.read_flag()
            is_irap = 16 <= nalu.nal_unit_type <= 23

            if is_irap:
                reader.read_flag()  # no_output_of_prior_pics_flag

            pps_id = reader.read_ue()
            pps = self.pps_list.get(pps_id, {})

            if not first_slice_flag:
                if pps.get("dependent_slice_segments_enabled_flag", False):
                    reader.read_flag()  # dependent_slice_segment_flag
                # Skip slice_segment_address - need SPS for this
                # For simplicity, we'll try to read slice_type anyway

            # Skip extra slice header bits
            for _ in range(pps.get("num_extra_slice_header_bits", 0)):
                reader.read_flag()

            slice_type = reader.read_ue()
            return self.SLICE_TYPES.get(slice_type, "?")
        except:
            return None
