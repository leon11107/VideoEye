"""H.265/HEVC bitstream parser."""

from typing import Optional
from collections import OrderedDict

from .nalu_parser import NALUnit, H265NaluType
from ._common import read_sei_payload_header, more_rbsp_data
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

    VIDEO_FORMATS = {
        0: "Component",
        1: "PAL",
        2: "NTSC",
        3: "SECAM",
        4: "MAC",
        5: "Unspecified video format",
    }

    def __init__(self):
        # Store parsed parameter sets for display reference
        self.vps_list: dict[int, dict] = {}
        self.sps_list: dict[int, dict] = {}
        self.pps_list: dict[int, dict] = {}
        # Raw integer context needed to decode the slice header (the display
        # dicts above hold formatted strings that can't be computed against).
        self.sps_ctx: dict[int, dict] = {}
        self.pps_ctx: dict[int, dict] = {}

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

    # ------------------------------------------------------------------ VPS

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
            syntax["vps_max_sub_layers_minus1"] = f"{vps_max_sub_layers_minus1} ({vps_max_sub_layers_minus1 + 1})"

            syntax["vps_temporal_id_nesting_flag"] = reader.read_flag()

            syntax["vps_reserved_0xffff_16bits"] = f"0x{reader.read_u(16):04x}"

            # profile_tier_level
            syntax["profile_tier_level"] = self._parse_profile_tier_level(
                reader, True, vps_max_sub_layers_minus1)

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

            for i in range(1, min(vps_num_layer_sets_minus1, 1023) + 1):
                for j in range(vps_max_layer_id + 1):
                    syntax[f"layer_id_included_flag[{i}][{j}]"] = reader.read_flag()

            vps_timing_info_present_flag = reader.read_flag()
            syntax["vps_timing_info_present_flag"] = vps_timing_info_present_flag
            if vps_timing_info_present_flag:
                syntax["vps_num_units_in_tick"] = reader.read_u(32)
                syntax["vps_time_scale"] = reader.read_u(32)
                vps_poc_proportional_to_timing_flag = reader.read_flag()
                syntax["vps_poc_proportional_to_timing_flag"] = vps_poc_proportional_to_timing_flag
                if vps_poc_proportional_to_timing_flag:
                    syntax["vps_num_ticks_poc_diff_one_minus1"] = reader.read_ue()
                vps_num_hrd_parameters = reader.read_ue()
                syntax["vps_num_hrd_parameters"] = vps_num_hrd_parameters
                for i in range(min(vps_num_hrd_parameters, 16)):
                    syntax[f"hrd_layer_set_idx[{i}]"] = reader.read_ue()
                    cprms = True if i == 0 else reader.read_flag()
                    if i > 0:
                        syntax[f"cprms_present_flag[{i}]"] = cprms
                    syntax[f"hrd_parameters[{i}]"] = self._parse_hrd_parameters(
                        reader, cprms, vps_max_sub_layers_minus1)

            syntax["vps_extension_flag"] = reader.read_flag()

            self.vps_list[vps_video_parameter_set_id] = syntax

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    # ------------------------------------------------------------------ SPS

    def parse_sps(self, nalu: NALUnit) -> dict:
        """Parse Sequence Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "SPS (Sequence Parameter Set)"
        ctx: dict = {}

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            sps_video_parameter_set_id = reader.read_u(4)
            syntax["sps_video_parameter_set_id"] = sps_video_parameter_set_id

            sps_max_sub_layers_minus1 = reader.read_u(3)
            syntax["sps_max_sub_layers_minus1"] = f"{sps_max_sub_layers_minus1} ({sps_max_sub_layers_minus1 + 1})"

            syntax["sps_temporal_id_nesting_flag"] = reader.read_flag()

            syntax["profile_tier_level"] = self._parse_profile_tier_level(
                reader, True, sps_max_sub_layers_minus1)

            sps_seq_parameter_set_id = reader.read_ue()
            syntax["sps_seq_parameter_set_id"] = sps_seq_parameter_set_id

            chroma_format_idc = reader.read_ue()
            chroma_names = {0: "monochrome", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
            syntax["chroma_format_idc"] = f"{chroma_format_idc} ({chroma_names.get(chroma_format_idc, 'unknown')})"

            separate_colour_plane_flag = False
            if chroma_format_idc == 3:
                separate_colour_plane_flag = reader.read_flag()
                syntax["separate_colour_plane_flag"] = separate_colour_plane_flag
            chroma_array_type = 0 if separate_colour_plane_flag else chroma_format_idc

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
            syntax["bit_depth_luma_minus8"] = f"{bit_depth_luma_minus8} ({bit_depth_luma_minus8 + 8})"
            syntax["bit_depth_chroma_minus8"] = f"{bit_depth_chroma_minus8} ({bit_depth_chroma_minus8 + 8})"

            log2_max_pic_order_cnt_lsb_minus4 = reader.read_ue()
            syntax["log2_max_pic_order_cnt_lsb_minus4"] = \
                f"{log2_max_pic_order_cnt_lsb_minus4} ({log2_max_pic_order_cnt_lsb_minus4 + 4})"

            sps_sub_layer_ordering_info_present_flag = reader.read_flag()
            syntax["sps_sub_layer_ordering_info_present_flag"] = sps_sub_layer_ordering_info_present_flag

            start = 0 if sps_sub_layer_ordering_info_present_flag else sps_max_sub_layers_minus1
            for i in range(start, sps_max_sub_layers_minus1 + 1):
                syntax[f"sps_max_dec_pic_buffering_minus1[{i}]"] = reader.read_ue()
                syntax[f"sps_max_num_reorder_pics[{i}]"] = reader.read_ue()
                syntax[f"sps_max_latency_increase_plus1[{i}]"] = reader.read_ue()

            log2_min_luma_coding_block_size_minus3 = reader.read_ue()
            log2_diff_max_min_luma_coding_block_size = reader.read_ue()
            syntax["log2_min_luma_coding_block_size_minus3"] = \
                f"{log2_min_luma_coding_block_size_minus3} ({log2_min_luma_coding_block_size_minus3 + 3})"
            syntax["log2_diff_max_min_luma_coding_block_size"] = log2_diff_max_min_luma_coding_block_size

            # Calculate CTU size
            min_cb_log2 = log2_min_luma_coding_block_size_minus3 + 3
            ctb_log2 = min_cb_log2 + log2_diff_max_min_luma_coding_block_size
            ctb_size = 1 << ctb_log2
            syntax["_calculated_ctu_size"] = ctb_size

            log2_min_tb_minus2 = reader.read_ue()
            syntax["log2_min_luma_transform_block_size_minus2"] = f"{log2_min_tb_minus2} ({log2_min_tb_minus2 + 2})"
            syntax["log2_diff_max_min_luma_transform_block_size"] = reader.read_ue()
            syntax["max_transform_hierarchy_depth_inter"] = reader.read_ue()
            syntax["max_transform_hierarchy_depth_intra"] = reader.read_ue()

            scaling_list_enabled_flag = reader.read_flag()
            syntax["scaling_list_enabled_flag"] = scaling_list_enabled_flag
            if scaling_list_enabled_flag:
                sps_scaling_list_data_present_flag = reader.read_flag()
                syntax["sps_scaling_list_data_present_flag"] = sps_scaling_list_data_present_flag
                if sps_scaling_list_data_present_flag:
                    syntax["scaling_list_data"] = self._parse_scaling_list_data(reader)

            syntax["amp_enabled_flag"] = reader.read_flag()
            sample_adaptive_offset_enabled_flag = reader.read_flag()
            syntax["sample_adaptive_offset_enabled_flag"] = sample_adaptive_offset_enabled_flag

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

            # st_ref_pic_set() x N -- also record NumDeltaPocs[] for slice headers
            st_rps_num_delta_pocs: list[int] = []
            for i in range(min(num_short_term_ref_pic_sets, 64)):
                rps = OrderedDict()
                nd = self._parse_st_ref_pic_set(
                    reader, rps, i, num_short_term_ref_pic_sets, st_rps_num_delta_pocs)
                st_rps_num_delta_pocs.append(nd)
                syntax[f"short_term_ref_pic_set({i})"] = rps

            long_term_ref_pics_present_flag = reader.read_flag()
            syntax["long_term_ref_pics_present_flag"] = long_term_ref_pics_present_flag
            num_long_term_ref_pics_sps = 0
            if long_term_ref_pics_present_flag:
                num_long_term_ref_pics_sps = reader.read_ue()
                syntax["num_long_term_ref_pics_sps"] = num_long_term_ref_pics_sps
                for i in range(min(num_long_term_ref_pics_sps, 64)):
                    syntax[f"lt_ref_pic_poc_lsb_sps[{i}]"] = reader.read_u(log2_max_pic_order_cnt_lsb_minus4 + 4)
                    syntax[f"used_by_curr_pic_lt_sps_flag[{i}]"] = reader.read_flag()

            sps_temporal_mvp_enabled_flag = reader.read_flag()
            syntax["sps_temporal_mvp_enabled_flag"] = sps_temporal_mvp_enabled_flag
            syntax["strong_intra_smoothing_enabled_flag"] = reader.read_flag()

            vui_parameters_present_flag = reader.read_flag()
            syntax["vui_parameters_present_flag"] = vui_parameters_present_flag
            if vui_parameters_present_flag:
                syntax["vui_parameters"] = self._parse_vui_parameters(
                    reader, sps_max_sub_layers_minus1)

            sps_extension_present_flag = reader.read_flag()
            syntax["sps_extension_present_flag"] = sps_extension_present_flag
            if sps_extension_present_flag:
                sps_range_extension_flag = reader.read_flag()
                sps_multilayer_extension_flag = reader.read_flag()
                sps_3d_extension_flag = reader.read_flag()
                sps_scc_extension_flag = reader.read_flag()
                syntax["sps_range_extension_flag"] = sps_range_extension_flag
                syntax["sps_multilayer_extension_flag"] = sps_multilayer_extension_flag
                syntax["sps_3d_extension_flag"] = sps_3d_extension_flag
                syntax["sps_scc_extension_flag"] = sps_scc_extension_flag
                syntax["sps_extension_4bits"] = reader.read_u(4)
                if sps_range_extension_flag:
                    syntax["sps_range_extension"] = self._parse_sps_range_extension(reader)

            self.sps_list[sps_seq_parameter_set_id] = syntax
            ctx.update(
                chroma_format_idc=chroma_format_idc,
                chroma_array_type=chroma_array_type,
                separate_colour_plane_flag=int(separate_colour_plane_flag),
                log2_max_poc=log2_max_pic_order_cnt_lsb_minus4 + 4,
                sample_adaptive_offset_enabled_flag=int(sample_adaptive_offset_enabled_flag),
                num_short_term_ref_pic_sets=num_short_term_ref_pic_sets,
                st_rps_num_delta_pocs=st_rps_num_delta_pocs,
                long_term_ref_pics_present_flag=int(long_term_ref_pics_present_flag),
                num_long_term_ref_pics_sps=num_long_term_ref_pics_sps,
                sps_temporal_mvp_enabled_flag=int(sps_temporal_mvp_enabled_flag),
                pic_width=pic_width_in_luma_samples,
                pic_height=pic_height_in_luma_samples,
                ctb_size=ctb_size,
            )
            self.sps_ctx[sps_seq_parameter_set_id] = ctx

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    # ------------------------------------------------------------------ PPS

    def parse_pps(self, nalu: NALUnit) -> dict:
        """Parse Picture Parameter Set."""
        syntax = OrderedDict()
        syntax["_name"] = "PPS (Picture Parameter Set)"
        ctx: dict = {}

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            pps_pic_parameter_set_id = reader.read_ue()
            syntax["pps_pic_parameter_set_id"] = pps_pic_parameter_set_id

            pps_seq_parameter_set_id = reader.read_ue()
            syntax["pps_seq_parameter_set_id"] = pps_seq_parameter_set_id

            dependent_slice_segments_enabled_flag = reader.read_flag()
            output_flag_present_flag = reader.read_flag()
            num_extra_slice_header_bits = reader.read_u(3)
            syntax["dependent_slice_segments_enabled_flag"] = dependent_slice_segments_enabled_flag
            syntax["output_flag_present_flag"] = output_flag_present_flag
            syntax["num_extra_slice_header_bits"] = num_extra_slice_header_bits
            syntax["sign_data_hiding_enabled_flag"] = reader.read_flag()
            cabac_init_present_flag = reader.read_flag()
            syntax["cabac_init_present_flag"] = cabac_init_present_flag

            num_ref_idx_l0_default_active_minus1 = reader.read_ue()
            num_ref_idx_l1_default_active_minus1 = reader.read_ue()
            syntax["num_ref_idx_l0_default_active_minus1"] = \
                f"{num_ref_idx_l0_default_active_minus1} ({num_ref_idx_l0_default_active_minus1 + 1})"
            syntax["num_ref_idx_l1_default_active_minus1"] = \
                f"{num_ref_idx_l1_default_active_minus1} ({num_ref_idx_l1_default_active_minus1 + 1})"

            init_qp_minus26 = reader.read_se()
            syntax["init_qp_minus26"] = f"{init_qp_minus26} ({init_qp_minus26 + 26})"
            syntax["constrained_intra_pred_flag"] = reader.read_flag()
            syntax["transform_skip_enabled_flag"] = reader.read_flag()

            cu_qp_delta_enabled_flag = reader.read_flag()
            syntax["cu_qp_delta_enabled_flag"] = cu_qp_delta_enabled_flag
            if cu_qp_delta_enabled_flag:
                syntax["diff_cu_qp_delta_depth"] = reader.read_ue()

            syntax["pps_cb_qp_offset"] = reader.read_se()
            syntax["pps_cr_qp_offset"] = reader.read_se()
            pps_slice_chroma_qp_offsets_present_flag = reader.read_flag()
            syntax["pps_slice_chroma_qp_offsets_present_flag"] = pps_slice_chroma_qp_offsets_present_flag
            weighted_pred_flag = reader.read_flag()
            weighted_bipred_flag = reader.read_flag()
            syntax["weighted_pred_flag"] = weighted_pred_flag
            syntax["weighted_bipred_flag"] = weighted_bipred_flag
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

            pps_loop_filter_across_slices_enabled_flag = reader.read_flag()
            syntax["pps_loop_filter_across_slices_enabled_flag"] = pps_loop_filter_across_slices_enabled_flag

            deblocking_filter_control_present_flag = reader.read_flag()
            syntax["deblocking_filter_control_present_flag"] = deblocking_filter_control_present_flag
            deblocking_filter_override_enabled_flag = False
            pps_deblocking_filter_disabled_flag = False
            if deblocking_filter_control_present_flag:
                deblocking_filter_override_enabled_flag = reader.read_flag()
                syntax["deblocking_filter_override_enabled_flag"] = deblocking_filter_override_enabled_flag
                pps_deblocking_filter_disabled_flag = reader.read_flag()
                syntax["pps_deblocking_filter_disabled_flag"] = pps_deblocking_filter_disabled_flag
                if not pps_deblocking_filter_disabled_flag:
                    syntax["pps_beta_offset_div2"] = reader.read_se()
                    syntax["pps_tc_offset_div2"] = reader.read_se()

            pps_scaling_list_data_present_flag = reader.read_flag()
            syntax["pps_scaling_list_data_present_flag"] = pps_scaling_list_data_present_flag
            if pps_scaling_list_data_present_flag:
                syntax["scaling_list_data"] = self._parse_scaling_list_data(reader)

            lists_modification_present_flag = reader.read_flag()
            syntax["lists_modification_present_flag"] = lists_modification_present_flag
            log2_parallel_merge_level_minus2 = reader.read_ue()
            syntax["log2_parallel_merge_level_minus2"] = \
                f"{log2_parallel_merge_level_minus2} ({log2_parallel_merge_level_minus2 + 2})"
            slice_segment_header_extension_present_flag = reader.read_flag()
            syntax["slice_segment_header_extension_present_flag"] = slice_segment_header_extension_present_flag

            pps_extension_present_flag = reader.read_flag()
            syntax["pps_extension_present_flag"] = pps_extension_present_flag
            if pps_extension_present_flag:
                pps_range_extension_flag = reader.read_flag()
                pps_multilayer_extension_flag = reader.read_flag()
                pps_3d_extension_flag = reader.read_flag()
                pps_scc_extension_flag = reader.read_flag()
                syntax["pps_range_extension_flag"] = pps_range_extension_flag
                syntax["pps_multilayer_extension_flag"] = pps_multilayer_extension_flag
                syntax["pps_3d_extension_flag"] = pps_3d_extension_flag
                syntax["pps_scc_extension_flag"] = pps_scc_extension_flag
                syntax["pps_extension_4bits"] = reader.read_u(4)
                if pps_range_extension_flag:
                    syntax["pps_range_extension"] = self._parse_pps_range_extension(
                        reader, bool(syntax.get("transform_skip_enabled_flag")))

            self.pps_list[pps_pic_parameter_set_id] = syntax
            self.pps_ctx[pps_pic_parameter_set_id] = dict(
                seq_parameter_set_id=pps_seq_parameter_set_id,
                dependent_slice_segments_enabled_flag=int(dependent_slice_segments_enabled_flag),
                output_flag_present_flag=int(output_flag_present_flag),
                num_extra_slice_header_bits=num_extra_slice_header_bits,
                cabac_init_present_flag=int(cabac_init_present_flag),
                num_ref_idx_l0_default_active_minus1=num_ref_idx_l0_default_active_minus1,
                num_ref_idx_l1_default_active_minus1=num_ref_idx_l1_default_active_minus1,
                pps_slice_chroma_qp_offsets_present_flag=int(pps_slice_chroma_qp_offsets_present_flag),
                weighted_pred_flag=int(weighted_pred_flag),
                weighted_bipred_flag=int(weighted_bipred_flag),
                tiles_enabled_flag=int(tiles_enabled_flag),
                entropy_coding_sync_enabled_flag=int(entropy_coding_sync_enabled_flag),
                pps_loop_filter_across_slices_enabled_flag=int(pps_loop_filter_across_slices_enabled_flag),
                deblocking_filter_override_enabled_flag=int(deblocking_filter_override_enabled_flag),
                pps_deblocking_filter_disabled_flag=int(pps_deblocking_filter_disabled_flag),
                lists_modification_present_flag=int(lists_modification_present_flag),
                slice_segment_header_extension_present_flag=int(slice_segment_header_extension_present_flag),
            )

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    # ----------------------------------------------------------- Slice header

    def parse_slice_header(self, nalu: NALUnit) -> dict:
        """Parse slice segment header."""
        syntax = OrderedDict()

        nut = nalu.nal_unit_type
        is_idr = nut in (H265NaluType.IDR_W_RADL, H265NaluType.IDR_N_LP)
        is_bla = 16 <= nut <= 18
        is_irap = 16 <= nut <= 23

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

            pps = self.pps_ctx.get(slice_pic_parameter_set_id, {})
            sps = self.sps_ctx.get(pps.get("seq_parameter_set_id", 0), {})

            dependent_slice_segment_flag = False
            if not first_slice_segment_in_pic_flag:
                if pps.get("dependent_slice_segments_enabled_flag"):
                    dependent_slice_segment_flag = reader.read_flag()
                    syntax["dependent_slice_segment_flag"] = dependent_slice_segment_flag

                pic_width = sps.get("pic_width", 0)
                pic_height = sps.get("pic_height", 0)
                ctb_size = sps.get("ctb_size", 64)
                if pic_width > 0 and pic_height > 0:
                    pic_w_ctbs = (pic_width + ctb_size - 1) // ctb_size
                    pic_h_ctbs = (pic_height + ctb_size - 1) // ctb_size
                    pic_size_in_ctbs = pic_w_ctbs * pic_h_ctbs
                    addr_bits = max(1, (pic_size_in_ctbs - 1).bit_length())
                    syntax["slice_segment_address"] = reader.read_u(addr_bits)

            # The remaining fields belong to an independent slice segment only.
            if dependent_slice_segment_flag:
                return syntax

            for i in range(pps.get("num_extra_slice_header_bits", 0)):
                syntax[f"slice_reserved_flag[{i}]"] = reader.read_flag()

            slice_type = reader.read_ue()
            syntax["slice_type"] = f"{slice_type} ({self.SLICE_TYPES.get(slice_type, 'Unknown')} slice)"

            if pps.get("output_flag_present_flag"):
                syntax["pic_output_flag"] = reader.read_flag()
            if sps.get("separate_colour_plane_flag"):
                syntax["colour_plane_id"] = reader.read_u(2)

            num_pic_total_curr = 0
            slice_temporal_mvp_enabled_flag = 0
            if not is_idr:
                slice_pic_order_cnt_lsb = reader.read_u(sps.get("log2_max_poc", 4))
                syntax["slice_pic_order_cnt_lsb"] = slice_pic_order_cnt_lsb

                num_st_rps = sps.get("num_short_term_ref_pic_sets", 0)
                short_term_ref_pic_set_sps_flag = reader.read_flag()
                syntax["short_term_ref_pic_set_sps_flag"] = short_term_ref_pic_set_sps_flag
                if not short_term_ref_pic_set_sps_flag:
                    rps = OrderedDict()
                    nd = self._parse_st_ref_pic_set(
                        reader, rps, num_st_rps, num_st_rps,
                        sps.get("st_rps_num_delta_pocs", []))
                    syntax[f"short_term_ref_pic_set({num_st_rps})"] = rps
                    num_pic_total_curr += rps.get("_num_used_curr", nd)
                elif num_st_rps > 1:
                    idx_bits = max(1, (num_st_rps - 1).bit_length())
                    idx = reader.read_u(idx_bits)
                    syntax["short_term_ref_pic_set_idx"] = idx

                if sps.get("long_term_ref_pics_present_flag"):
                    self._parse_slice_long_term(reader, syntax, sps)

                if sps.get("sps_temporal_mvp_enabled_flag"):
                    slice_temporal_mvp_enabled_flag = int(reader.read_flag())
                    syntax["slice_temporal_mvp_enabled_flag"] = bool(slice_temporal_mvp_enabled_flag)

            slice_sao_luma_flag = 0
            slice_sao_chroma_flag = 0
            if sps.get("sample_adaptive_offset_enabled_flag"):
                slice_sao_luma_flag = int(reader.read_flag())
                syntax["slice_sao_luma_flag"] = bool(slice_sao_luma_flag)
                if sps.get("chroma_array_type", 0) != 0:
                    slice_sao_chroma_flag = int(reader.read_flag())
                    syntax["slice_sao_chroma_flag"] = bool(slice_sao_chroma_flag)

            slice_deblocking_filter_disabled_flag = pps.get("pps_deblocking_filter_disabled_flag", 0)

            if slice_type in (0, 1):  # P or B
                is_b = (slice_type == 0)
                nb_l0 = pps.get("num_ref_idx_l0_default_active_minus1", 0)
                nb_l1 = pps.get("num_ref_idx_l1_default_active_minus1", 0)

                num_ref_idx_active_override_flag = reader.read_flag()
                syntax["num_ref_idx_active_override_flag"] = num_ref_idx_active_override_flag
                if num_ref_idx_active_override_flag:
                    nb_l0 = reader.read_ue()
                    syntax["num_ref_idx_l0_active_minus1"] = f"{nb_l0} ({nb_l0 + 1})"
                    if is_b:
                        nb_l1 = reader.read_ue()
                        syntax["num_ref_idx_l1_active_minus1"] = f"{nb_l1} ({nb_l1 + 1})"

                if pps.get("lists_modification_present_flag") and num_pic_total_curr > 1:
                    syntax["ref_pic_lists_modification"] = self._parse_ref_pic_lists_modification(
                        reader, is_b, nb_l0, nb_l1, num_pic_total_curr)

                if is_b:
                    syntax["mvd_l1_zero_flag"] = reader.read_flag()
                if pps.get("cabac_init_present_flag"):
                    syntax["cabac_init_flag"] = reader.read_flag()

                if slice_temporal_mvp_enabled_flag:
                    collocated_from_l0_flag = 1
                    if is_b:
                        collocated_from_l0_flag = int(reader.read_flag())
                        syntax["collocated_from_l0_flag"] = bool(collocated_from_l0_flag)
                    if (collocated_from_l0_flag and nb_l0 > 0) or \
                       (not collocated_from_l0_flag and nb_l1 > 0):
                        syntax["collocated_ref_idx"] = reader.read_ue()

                weighted = ((pps.get("weighted_pred_flag") and not is_b) or
                            (pps.get("weighted_bipred_flag") and is_b))
                if weighted:
                    syntax["pred_weight_table"] = self._parse_pred_weight_table(
                        reader, is_b, nb_l0, nb_l1, sps.get("chroma_array_type", 0))

                five_minus = reader.read_ue()
                syntax["five_minus_max_num_merge_cand"] = f"{five_minus} ({5 - five_minus})"

            slice_qp_delta = reader.read_se()
            syntax["slice_qp_delta"] = slice_qp_delta
            if pps.get("pps_slice_chroma_qp_offsets_present_flag"):
                syntax["slice_cb_qp_offset"] = reader.read_se()
                syntax["slice_cr_qp_offset"] = reader.read_se()

            if pps.get("deblocking_filter_override_enabled_flag"):
                deblocking_filter_override_flag = reader.read_flag()
                syntax["deblocking_filter_override_flag"] = deblocking_filter_override_flag
                if deblocking_filter_override_flag:
                    slice_deblocking_filter_disabled_flag = int(reader.read_flag())
                    syntax["slice_deblocking_filter_disabled_flag"] = bool(slice_deblocking_filter_disabled_flag)
                    if not slice_deblocking_filter_disabled_flag:
                        syntax["slice_beta_offset_div2"] = reader.read_se()
                        syntax["slice_tc_offset_div2"] = reader.read_se()

            if pps.get("pps_loop_filter_across_slices_enabled_flag") and \
               (slice_sao_luma_flag or slice_sao_chroma_flag or
                    not slice_deblocking_filter_disabled_flag):
                syntax["slice_loop_filter_across_slices_enabled_flag"] = reader.read_flag()

            if pps.get("tiles_enabled_flag") or pps.get("entropy_coding_sync_enabled_flag"):
                num_entry_point_offsets = reader.read_ue()
                syntax["num_entry_point_offsets"] = num_entry_point_offsets
                if num_entry_point_offsets > 0:
                    offset_len_minus1 = reader.read_ue()
                    syntax["offset_len_minus1"] = f"{offset_len_minus1} ({offset_len_minus1 + 1})"
                    for i in range(min(num_entry_point_offsets, 4096)):
                        syntax[f"entry_point_offset_minus1[{i}]"] = reader.read_u(offset_len_minus1 + 1)

            if pps.get("slice_segment_header_extension_present_flag"):
                ext_len = reader.read_ue()
                syntax["slice_segment_header_extension_length"] = ext_len
                for i in range(min(ext_len, 4096)):
                    syntax[f"slice_segment_header_extension_data_byte[{i}]"] = reader.read_u(8)

        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def _parse_slice_long_term(self, reader: BitstreamReader, syntax: dict, sps: dict) -> None:
        """Long-term reference picture portion of the slice header."""
        num_long_term_sps = 0
        num_lt_ref_pics_sps = sps.get("num_long_term_ref_pics_sps", 0)
        if num_lt_ref_pics_sps > 0:
            num_long_term_sps = reader.read_ue()
            syntax["num_long_term_sps"] = num_long_term_sps
        num_long_term_pics = reader.read_ue()
        syntax["num_long_term_pics"] = num_long_term_pics
        log2_max_poc = sps.get("log2_max_poc", 4)
        total = min(num_long_term_sps + num_long_term_pics, 64)
        for i in range(total):
            if i < num_long_term_sps:
                if num_lt_ref_pics_sps > 1:
                    bits = max(1, (num_lt_ref_pics_sps - 1).bit_length())
                    syntax[f"lt_idx_sps[{i}]"] = reader.read_u(bits)
            else:
                syntax[f"poc_lsb_lt[{i}]"] = reader.read_u(log2_max_poc)
                syntax[f"used_by_curr_pic_lt_flag[{i}]"] = reader.read_flag()
            delta_poc_msb_present_flag = reader.read_flag()
            syntax[f"delta_poc_msb_present_flag[{i}]"] = delta_poc_msb_present_flag
            if delta_poc_msb_present_flag:
                syntax[f"delta_poc_msb_cycle_lt[{i}]"] = reader.read_ue()

    # ------------------------------------------------------------------ SEI

    def parse_sei(self, nalu: NALUnit) -> dict:
        """Parse SEI message(s) -- one NAL unit may carry several."""
        syntax = OrderedDict()
        is_prefix = nalu.nal_unit_type == H265NaluType.PREFIX_SEI
        syntax["_name"] = "Prefix SEI" if is_prefix else "Suffix SEI"

        sei_types = {
            0: "buffering_period", 1: "pic_timing", 2: "pan_scan_rect",
            3: "filler_payload", 4: "user_data_registered_itu_t_t35",
            5: "user_data_unregistered", 6: "recovery_point",
            129: "active_parameter_sets", 130: "decoding_unit_info",
            131: "temporal_sub_layer_zero_index", 132: "decoded_picture_hash",
            133: "scalable_nesting", 134: "region_refresh_info",
            137: "mastering_display_colour_volume", 144: "content_light_level_info",
        }

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            idx = 0
            while more_rbsp_data(reader):
                payload_type, payload_size = read_sei_payload_header(reader)
                name = sei_types.get(payload_type, "reserved_sei_message")
                msg = OrderedDict()
                msg["payload_type"] = f"{payload_type} ({name})"
                msg["payload_size"] = payload_size
                end_byte = reader.byte_offset + payload_size
                self._parse_sei_payload(reader, payload_type, payload_size, msg)
                # Re-align to the declared payload end regardless of how much
                # of the payload we decoded.
                if reader.byte_offset < end_byte:
                    reader.set_position(end_byte, 0)
                syntax[f"sei_message[{idx}] ({name})"] = msg
                idx += 1
            if idx == 0:
                syntax["_note"] = "no SEI messages"
        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    def _parse_sei_payload(self, reader: BitstreamReader, payload_type: int,
                           payload_size: int, msg: dict) -> None:
        """Decode the body of known SEI payloads."""
        if payload_type == 137:  # mastering_display_colour_volume
            for c in range(3):
                msg[f"display_primaries_x[{c}]"] = reader.read_u(16)
                msg[f"display_primaries_y[{c}]"] = reader.read_u(16)
            msg["white_point_x"] = reader.read_u(16)
            msg["white_point_y"] = reader.read_u(16)
            msg["max_display_mastering_luminance"] = reader.read_u(32)
            msg["min_display_mastering_luminance"] = reader.read_u(32)
        elif payload_type == 144:  # content_light_level_info
            msg["max_content_light_level"] = reader.read_u(16)
            msg["max_pic_average_light_level"] = reader.read_u(16)
        elif payload_type == 5 and payload_size >= 16:  # user_data_unregistered
            uuid = bytes(reader.read_u(8) for _ in range(16))
            msg["uuid_iso_iec_11578"] = uuid.hex()
            data = bytes(reader.read_u(8) for _ in range(payload_size - 16))
            msg["user_data_payload"] = data.decode("ascii", "replace").rstrip("\x00")

    # -------------------------------------------------------------- AUD / misc

    def parse_aud(self, nalu: NALUnit) -> dict:
        """Parse Access Unit Delimiter."""
        syntax = OrderedDict()
        syntax["_name"] = "AUD (Access Unit Delimiter)"

        reader = BitstreamReader.from_rbsp(nalu.data[2:])

        try:
            pic_type = reader.read_u(3)
            pic_types = {0: "I", 1: "P, I", 2: "B, P, I"}
            syntax["pic_type"] = f"{pic_type} ({pic_types.get(pic_type, 'unknown')})"
        except EOFError:
            syntax["_parse_error"] = "Unexpected end of data"

        return syntax

    # ------------------------------------------------------- profile_tier_level

    def _parse_profile_tier_level(self, reader: BitstreamReader,
                                   profile_present_flag: bool,
                                   max_sub_layers_minus1: int) -> dict:
        """Parse profile_tier_level()."""
        ptl = OrderedDict()

        if profile_present_flag:
            ptl["general_profile_space"] = reader.read_u(2)
            ptl["general_tier_flag"] = reader.read_flag()
            general_profile_idc = reader.read_u(5)
            ptl["general_profile_idc"] = f"{general_profile_idc} ({self.PROFILES.get(general_profile_idc, 'Unknown')})"

            compat = [reader.read_flag() for _ in range(32)]
            for i in range(32):
                ptl[f"general_profile_compatibility_flag[{i}]"] = compat[i]

            ptl["general_progressive_source_flag"] = reader.read_flag()
            ptl["general_interlaced_source_flag"] = reader.read_flag()
            ptl["general_non_packed_constraint_flag"] = reader.read_flag()
            ptl["general_frame_only_constraint_flag"] = reader.read_flag()

            detailed = general_profile_idc in (4, 5, 6, 7, 8, 9, 10, 11) or \
                any(compat[i] for i in (4, 5, 6, 7, 8, 9, 10, 11))
            if detailed:
                ptl["general_max_12bit_constraint_flag"] = reader.read_flag()
                ptl["general_max_10bit_constraint_flag"] = reader.read_flag()
                ptl["general_max_8bit_constraint_flag"] = reader.read_flag()
                ptl["general_max_422chroma_constraint_flag"] = reader.read_flag()
                ptl["general_max_420chroma_constraint_flag"] = reader.read_flag()
                ptl["general_max_monochrome_constraint_flag"] = reader.read_flag()
                ptl["general_intra_constraint_flag"] = reader.read_flag()
                ptl["general_one_picture_only_constraint_flag"] = reader.read_flag()
                ptl["general_lower_bit_rate_constraint_flag"] = reader.read_flag()
                if general_profile_idc in (5, 9, 10, 11) or \
                        any(compat[i] for i in (5, 9, 10, 11)):
                    ptl["general_max_14bit_constraint_flag"] = reader.read_flag()
                    # 33/34 reserved bits -- split, read_bits caps at 32.
                    ptl["general_reserved_zero_33bits"] = (reader.read_u(17) << 16) | reader.read_u(16)
                else:
                    ptl["general_reserved_zero_34bits"] = (reader.read_u(17) << 17) | reader.read_u(17)
            else:
                ptl["general_reserved_zero_7bits"] = reader.read_u(7)
                ptl["general_one_picture_only_constraint_flag"] = reader.read_flag()
                # 35 reserved bits (read in two halves -- read_bits caps at 32).
                ptl["general_reserved_zero_35bits"] = (reader.read_u(24) << 11) | reader.read_u(11)

            if general_profile_idc in (1, 2, 3, 4, 5, 9, 11) or \
                    any(compat[i] for i in (1, 2, 3, 4, 5, 9, 11)):
                ptl["general_inbld_flag"] = reader.read_flag()
            else:
                ptl["general_reserved_zero_bit"] = reader.read_bit()

        general_level_idc = reader.read_u(8)
        ptl["general_level_idc"] = f"{general_level_idc} ({general_level_idc // 30}.{(general_level_idc % 30) // 3})"

        sub_layer_profile_present = []
        sub_layer_level_present = []
        for i in range(max_sub_layers_minus1):
            spp = reader.read_flag()
            slp = reader.read_flag()
            sub_layer_profile_present.append(spp)
            sub_layer_level_present.append(slp)
            ptl[f"sub_layer_profile_present_flag[{i}]"] = spp
            ptl[f"sub_layer_level_present_flag[{i}]"] = slp

        if max_sub_layers_minus1 > 0:
            for i in range(max_sub_layers_minus1, 8):
                reader.read_u(2)  # reserved_zero_2bits

        for i in range(max_sub_layers_minus1):
            if sub_layer_profile_present[i]:
                reader.read_u(2)            # sub_layer_profile_space
                reader.read_flag()          # sub_layer_tier_flag
                reader.read_u(5)            # sub_layer_profile_idc
                reader.read_u(32)           # sub_layer_profile_compatibility_flag[32]
                reader.read_u(4)            # 4 source/constraint flags
                reader.read_u(32)           # 44 constraint/reserved bits + inbld
                reader.read_u(12)
            if sub_layer_level_present[i]:
                reader.read_u(8)            # sub_layer_level_idc

        return ptl

    # ----------------------------------------------------------- st_ref_pic_set

    def _parse_st_ref_pic_set(self, reader: BitstreamReader, target: dict,
                              st_rps_idx: int, num_st_rps: int,
                              num_delta_pocs: list) -> int:
        """Parse short_term_ref_pic_set(stRpsIdx); returns NumDeltaPocs."""
        inter_pred = False
        if st_rps_idx != 0:
            inter_pred = reader.read_flag()
            target["inter_ref_pic_set_prediction_flag"] = inter_pred

        if inter_pred:
            delta_idx_minus1 = 0
            if st_rps_idx == num_st_rps:
                delta_idx_minus1 = reader.read_ue()
                target["delta_idx_minus1"] = delta_idx_minus1
            target["delta_rps_sign"] = reader.read_flag()
            target["abs_delta_rps_minus1"] = reader.read_ue()
            ref_idx = st_rps_idx - (delta_idx_minus1 + 1)
            ref_num_delta = num_delta_pocs[ref_idx] if 0 <= ref_idx < len(num_delta_pocs) else 0
            num_used = 0
            num_delta = 0
            for j in range(ref_num_delta + 1):
                used = reader.read_flag()
                target[f"used_by_curr_pic_flag[{j}]"] = int(used)
                use_delta = True
                if not used:
                    use_delta = reader.read_flag()
                    target[f"use_delta_flag[{j}]"] = int(use_delta)
                if used:
                    num_used += 1
                if used or use_delta:
                    num_delta += 1
            target["_num_used_curr"] = num_used
            return num_delta

        num_negative_pics = reader.read_ue()
        num_positive_pics = reader.read_ue()
        target["num_negative_pics"] = num_negative_pics
        target["num_positive_pics"] = num_positive_pics
        num_used = 0
        for i in range(min(num_negative_pics, 16)):
            target[f"delta_poc_s0_minus1[{i}]"] = reader.read_ue()
            used = reader.read_flag()
            target[f"used_by_curr_pic_s0_flag[{i}]"] = int(used)
            num_used += int(used)
        for i in range(min(num_positive_pics, 16)):
            target[f"delta_poc_s1_minus1[{i}]"] = reader.read_ue()
            used = reader.read_flag()
            target[f"used_by_curr_pic_s1_flag[{i}]"] = int(used)
            num_used += int(used)
        target["_num_used_curr"] = num_used
        return num_negative_pics + num_positive_pics

    # -------------------------------------------------------- ref list / weights

    def _parse_ref_pic_lists_modification(self, reader: BitstreamReader, is_b: bool,
                                          nb_l0: int, nb_l1: int,
                                          num_pic_total_curr: int) -> dict:
        out = OrderedDict()
        bits = max(1, (num_pic_total_curr - 1).bit_length())
        ref_pic_list_modification_flag_l0 = reader.read_flag()
        out["ref_pic_list_modification_flag_l0"] = ref_pic_list_modification_flag_l0
        if ref_pic_list_modification_flag_l0:
            for i in range(nb_l0 + 1):
                out[f"list_entry_l0[{i}]"] = reader.read_u(bits)
        if is_b:
            ref_pic_list_modification_flag_l1 = reader.read_flag()
            out["ref_pic_list_modification_flag_l1"] = ref_pic_list_modification_flag_l1
            if ref_pic_list_modification_flag_l1:
                for i in range(nb_l1 + 1):
                    out[f"list_entry_l1[{i}]"] = reader.read_u(bits)
        return out

    def _parse_pred_weight_table(self, reader: BitstreamReader, is_b: bool,
                                 nb_l0: int, nb_l1: int, chroma_array_type: int) -> dict:
        out = OrderedDict()
        out["luma_log2_weight_denom"] = reader.read_ue()
        if chroma_array_type != 0:
            out["delta_chroma_log2_weight_denom"] = reader.read_se()
        self._parse_pred_weight_list(reader, out, "l0", nb_l0, chroma_array_type)
        if is_b:
            self._parse_pred_weight_list(reader, out, "l1", nb_l1, chroma_array_type)
        return out

    def _parse_pred_weight_list(self, reader: BitstreamReader, out: dict,
                                lst: str, nb: int, chroma_array_type: int) -> None:
        luma_flags = []
        for i in range(nb + 1):
            f = reader.read_flag()
            luma_flags.append(f)
            out[f"luma_weight_{lst}_flag[{i}]"] = int(f)
        chroma_flags = [False] * (nb + 1)
        if chroma_array_type != 0:
            for i in range(nb + 1):
                f = reader.read_flag()
                chroma_flags[i] = f
                out[f"chroma_weight_{lst}_flag[{i}]"] = int(f)
        for i in range(nb + 1):
            if luma_flags[i]:
                out[f"delta_luma_weight_{lst}[{i}]"] = reader.read_se()
                out[f"luma_offset_{lst}[{i}]"] = reader.read_se()
            if chroma_flags[i]:
                for j in range(2):
                    out[f"delta_chroma_weight_{lst}[{i}][{j}]"] = reader.read_se()
                    out[f"delta_chroma_offset_{lst}[{i}][{j}]"] = reader.read_se()

    # --------------------------------------------------------- scaling_list_data

    def _parse_scaling_list_data(self, reader: BitstreamReader) -> dict:
        out = OrderedDict()
        for size_id in range(4):
            matrix_id = 0
            while matrix_id < 6:
                pred_mode = reader.read_flag()
                out[f"scaling_list_pred_mode_flag[{size_id}][{matrix_id}]"] = pred_mode
                if not pred_mode:
                    out[f"scaling_list_pred_matrix_id_delta[{size_id}][{matrix_id}]"] = reader.read_ue()
                else:
                    coef_num = min(64, 1 << (4 + (size_id << 1)))
                    if size_id > 1:
                        out[f"scaling_list_dc_coef_minus8[{size_id - 2}][{matrix_id}]"] = reader.read_se()
                    for i in range(coef_num):
                        out[f"scaling_list_delta_coef[{size_id}][{matrix_id}][{i}]"] = reader.read_se()
                matrix_id += 3 if size_id == 3 else 1
        return out

    # ------------------------------------------------------------ VUI / HRD

    def _parse_vui_parameters(self, reader: BitstreamReader, max_sub_layers_minus1: int) -> dict:
        vui = OrderedDict()

        aspect_ratio_info_present_flag = reader.read_flag()
        vui["aspect_ratio_info_present_flag"] = aspect_ratio_info_present_flag
        if aspect_ratio_info_present_flag:
            aspect_ratio_idc = reader.read_u(8)
            vui["aspect_ratio_idc"] = aspect_ratio_idc
            if aspect_ratio_idc == 255:  # EXTENDED_SAR
                vui["sar_width"] = reader.read_u(16)
                vui["sar_height"] = reader.read_u(16)

        overscan_info_present_flag = reader.read_flag()
        vui["overscan_info_present_flag"] = overscan_info_present_flag
        if overscan_info_present_flag:
            vui["overscan_appropriate_flag"] = reader.read_flag()

        video_signal_type_present_flag = reader.read_flag()
        vui["video_signal_type_present_flag"] = video_signal_type_present_flag
        if video_signal_type_present_flag:
            video_format = reader.read_u(3)
            vui["video_format"] = f"{video_format} ({self.VIDEO_FORMATS.get(video_format, 'Reserved')})"
            vui["video_full_range_flag"] = reader.read_flag()
            colour_description_present_flag = reader.read_flag()
            vui["colour_description_present_flag"] = colour_description_present_flag
            if colour_description_present_flag:
                vui["colour_primaries"] = reader.read_u(8)
                vui["transfer_characteristics"] = reader.read_u(8)
                vui["matrix_coeffs"] = reader.read_u(8)

        chroma_loc_info_present_flag = reader.read_flag()
        vui["chroma_loc_info_present_flag"] = chroma_loc_info_present_flag
        if chroma_loc_info_present_flag:
            vui["chroma_sample_loc_type_top_field"] = reader.read_ue()
            vui["chroma_sample_loc_type_bottom_field"] = reader.read_ue()

        vui["neutral_chroma_indication_flag"] = reader.read_flag()
        vui["field_seq_flag"] = reader.read_flag()
        vui["frame_field_info_present_flag"] = reader.read_flag()

        default_display_window_flag = reader.read_flag()
        vui["default_display_window_flag"] = default_display_window_flag
        if default_display_window_flag:
            vui["def_disp_win_left_offset"] = reader.read_ue()
            vui["def_disp_win_right_offset"] = reader.read_ue()
            vui["def_disp_win_top_offset"] = reader.read_ue()
            vui["def_disp_win_bottom_offset"] = reader.read_ue()

        vui_timing_info_present_flag = reader.read_flag()
        vui["vui_timing_info_present_flag"] = vui_timing_info_present_flag
        if vui_timing_info_present_flag:
            vui["vui_num_units_in_tick"] = reader.read_u(32)
            vui["vui_time_scale"] = reader.read_u(32)
            vui_poc_proportional_to_timing_flag = reader.read_flag()
            vui["vui_poc_proportional_to_timing_flag"] = vui_poc_proportional_to_timing_flag
            if vui_poc_proportional_to_timing_flag:
                vui["vui_num_ticks_poc_diff_one_minus1"] = reader.read_ue()
            vui_hrd_parameters_present_flag = reader.read_flag()
            vui["vui_hrd_parameters_present_flag"] = vui_hrd_parameters_present_flag
            if vui_hrd_parameters_present_flag:
                vui["hrd_parameters"] = self._parse_hrd_parameters(
                    reader, True, max_sub_layers_minus1)

        bitstream_restriction_flag = reader.read_flag()
        vui["bitstream_restriction_flag"] = bitstream_restriction_flag
        if bitstream_restriction_flag:
            vui["tiles_fixed_structure_flag"] = reader.read_flag()
            vui["motion_vectors_over_pic_boundaries_flag"] = reader.read_flag()
            vui["restricted_ref_pic_lists_flag"] = reader.read_flag()
            vui["min_spatial_segmentation_idc"] = reader.read_ue()
            vui["max_bytes_per_pic_denom"] = reader.read_ue()
            vui["max_bits_per_min_cu_denom"] = reader.read_ue()
            vui["log2_max_mv_length_horizontal"] = reader.read_ue()
            vui["log2_max_mv_length_vertical"] = reader.read_ue()

        return vui

    def _parse_hrd_parameters(self, reader: BitstreamReader, common_inf_present: bool,
                              max_sub_layers_minus1: int) -> dict:
        hrd = OrderedDict()
        nal_hrd_parameters_present_flag = False
        vcl_hrd_parameters_present_flag = False
        sub_pic_hrd_params_present_flag = False

        if common_inf_present:
            nal_hrd_parameters_present_flag = reader.read_flag()
            vcl_hrd_parameters_present_flag = reader.read_flag()
            hrd["nal_hrd_parameters_present_flag"] = nal_hrd_parameters_present_flag
            hrd["vcl_hrd_parameters_present_flag"] = vcl_hrd_parameters_present_flag
            if nal_hrd_parameters_present_flag or vcl_hrd_parameters_present_flag:
                sub_pic_hrd_params_present_flag = reader.read_flag()
                hrd["sub_pic_hrd_params_present_flag"] = sub_pic_hrd_params_present_flag
                if sub_pic_hrd_params_present_flag:
                    hrd["tick_divisor_minus2"] = reader.read_u(8)
                    hrd["du_cpb_removal_delay_increment_length_minus1"] = reader.read_u(5)
                    hrd["sub_pic_cpb_params_in_pic_timing_sei_flag"] = reader.read_flag()
                    hrd["dpb_output_delay_du_length_minus1"] = reader.read_u(5)
                hrd["bit_rate_scale"] = reader.read_u(4)
                hrd["cpb_size_scale"] = reader.read_u(4)
                if sub_pic_hrd_params_present_flag:
                    hrd["cpb_size_du_scale"] = reader.read_u(4)
                hrd["initial_cpb_removal_delay_length_minus1"] = reader.read_u(5)
                hrd["au_cpb_removal_delay_length_minus1"] = reader.read_u(5)
                hrd["dpb_output_delay_length_minus1"] = reader.read_u(5)

        for i in range(max_sub_layers_minus1 + 1):
            fixed_pic_rate_general_flag = reader.read_flag()
            hrd[f"fixed_pic_rate_general_flag[{i}]"] = fixed_pic_rate_general_flag
            fixed_pic_rate_within_cvs_flag = True
            if not fixed_pic_rate_general_flag:
                fixed_pic_rate_within_cvs_flag = reader.read_flag()
                hrd[f"fixed_pic_rate_within_cvs_flag[{i}]"] = fixed_pic_rate_within_cvs_flag
            low_delay_hrd_flag = False
            if fixed_pic_rate_within_cvs_flag:
                hrd[f"elemental_duration_in_tc_minus1[{i}]"] = reader.read_ue()
            else:
                low_delay_hrd_flag = reader.read_flag()
                hrd[f"low_delay_hrd_flag[{i}]"] = low_delay_hrd_flag
            cpb_cnt_minus1 = 0
            if not low_delay_hrd_flag:
                cpb_cnt_minus1 = reader.read_ue()
                hrd[f"cpb_cnt_minus1[{i}]"] = cpb_cnt_minus1
            if nal_hrd_parameters_present_flag:
                self._parse_sub_layer_hrd(reader, hrd, "nal", i, cpb_cnt_minus1,
                                          sub_pic_hrd_params_present_flag)
            if vcl_hrd_parameters_present_flag:
                self._parse_sub_layer_hrd(reader, hrd, "vcl", i, cpb_cnt_minus1,
                                          sub_pic_hrd_params_present_flag)

        return hrd

    def _parse_sub_layer_hrd(self, reader: BitstreamReader, hrd: dict, kind: str,
                             sub_layer: int, cpb_cnt_minus1: int,
                             sub_pic_hrd_params_present_flag: bool) -> None:
        # Match the oracle's naming: plain name for the (common) NAL,
        # single-sub-layer case; prefix/qualify only when needed to stay unique.
        pre = "" if kind == "nal" else "vcl_"
        for i in range(min(cpb_cnt_minus1 + 1, 32)):
            idx = f"[{i}]" if sub_layer == 0 else f"[{sub_layer}][{i}]"
            hrd[f"{pre}bit_rate_value_minus1{idx}"] = reader.read_ue()
            hrd[f"{pre}cpb_size_value_minus1{idx}"] = reader.read_ue()
            if sub_pic_hrd_params_present_flag:
                hrd[f"{pre}cpb_size_du_value_minus1{idx}"] = reader.read_ue()
                hrd[f"{pre}bit_rate_du_value_minus1{idx}"] = reader.read_ue()
            hrd[f"{pre}cbr_flag{idx}"] = reader.read_flag()

    # ----------------------------------------------------------- range extension

    def _parse_sps_range_extension(self, reader: BitstreamReader) -> dict:
        out = OrderedDict()
        out["transform_skip_rotation_enabled_flag"] = reader.read_flag()
        out["transform_skip_context_enabled_flag"] = reader.read_flag()
        out["implicit_rdpcm_enabled_flag"] = reader.read_flag()
        out["explicit_rdpcm_enabled_flag"] = reader.read_flag()
        out["extended_precision_processing_flag"] = reader.read_flag()
        out["intra_smoothing_disabled_flag"] = reader.read_flag()
        out["high_precision_offsets_enabled_flag"] = reader.read_flag()
        out["persistent_rice_adaptation_enabled_flag"] = reader.read_flag()
        out["cabac_bypass_alignment_enabled_flag"] = reader.read_flag()
        return out

    def _parse_pps_range_extension(self, reader: BitstreamReader,
                                   transform_skip_enabled: bool) -> dict:
        out = OrderedDict()
        if transform_skip_enabled:
            out["log2_max_transform_skip_block_size_minus2"] = reader.read_ue()
        out["cross_component_prediction_enabled_flag"] = reader.read_flag()
        chroma_qp_offset_list_enabled_flag = reader.read_flag()
        out["chroma_qp_offset_list_enabled_flag"] = chroma_qp_offset_list_enabled_flag
        if chroma_qp_offset_list_enabled_flag:
            out["diff_cu_chroma_qp_offset_depth"] = reader.read_ue()
            chroma_qp_offset_list_len_minus1 = reader.read_ue()
            out["chroma_qp_offset_list_len_minus1"] = chroma_qp_offset_list_len_minus1
            for i in range(min(chroma_qp_offset_list_len_minus1 + 1, 8)):
                out[f"cb_qp_offset_list[{i}]"] = reader.read_se()
                out[f"cr_qp_offset_list[{i}]"] = reader.read_se()
        out["log2_sao_offset_scale_luma"] = reader.read_ue()
        out["log2_sao_offset_scale_chroma"] = reader.read_ue()
        return out

    # ----------------------------------------------------------------- helpers

    def get_slice_type(self, nalu: NALUnit) -> Optional[str]:
        """Get slice type from slice NAL unit (lightweight, frame-typing path)."""
        if not nalu.is_slice():
            return None

        try:
            reader = BitstreamReader.from_rbsp(nalu.data[2:])

            first_slice_flag = reader.read_flag()
            is_irap = 16 <= nalu.nal_unit_type <= 23
            if is_irap:
                reader.read_flag()  # no_output_of_prior_pics_flag

            pps_id = reader.read_ue()
            pps = self.pps_ctx.get(pps_id, {})
            sps = self.sps_ctx.get(pps.get("seq_parameter_set_id", 0), {})

            if not first_slice_flag:
                if pps.get("dependent_slice_segments_enabled_flag"):
                    if reader.read_flag():  # dependent_slice_segment_flag
                        return None  # slice_type not present in dependent segments
                pic_w = sps.get("pic_width", 0)
                pic_h = sps.get("pic_height", 0)
                ctb = sps.get("ctb_size", 64)
                if pic_w and pic_h:
                    pic_size = ((pic_w + ctb - 1) // ctb) * ((pic_h + ctb - 1) // ctb)
                    reader.read_u(max(1, (pic_size - 1).bit_length()))

            for _ in range(pps.get("num_extra_slice_header_bits", 0)):
                reader.read_flag()

            slice_type = reader.read_ue()
            return self.SLICE_TYPES.get(slice_type, "?")
        except Exception:
            return None
