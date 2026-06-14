"""NAL Unit parser for H.264/H.265 bitstreams."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class H264NaluType(IntEnum):
    """H.264 NAL unit types."""
    UNSPECIFIED = 0
    SLICE_NON_IDR = 1
    SLICE_PART_A = 2
    SLICE_PART_B = 3
    SLICE_PART_C = 4
    SLICE_IDR = 5
    SEI = 6
    SPS = 7
    PPS = 8
    AUD = 9
    END_SEQ = 10
    END_STREAM = 11
    FILLER = 12
    SPS_EXT = 13
    PREFIX = 14
    SUBSET_SPS = 15
    DPS = 16
    # 17-18 reserved
    SLICE_AUX = 19
    SLICE_EXT = 20
    SLICE_EXT_DEPTH = 21
    # 22-23 reserved
    # 24-31 unspecified

    @classmethod
    def name_for_type(cls, nalu_type: int) -> str:
        """Get human-readable name for NAL unit type."""
        names = {
            0: "Unspecified",
            1: "Non-IDR Slice",
            2: "Slice Part A",
            3: "Slice Part B",
            4: "Slice Part C",
            5: "IDR Slice",
            6: "SEI",
            7: "SPS",
            8: "PPS",
            9: "AUD",
            10: "End of Sequence",
            11: "End of Stream",
            12: "Filler Data",
            13: "SPS Extension",
            14: "Prefix NAL",
            15: "Subset SPS",
            16: "DPS",
            19: "Auxiliary Slice",
            20: "Slice Extension",
            21: "Depth Slice Extension",
        }
        return names.get(nalu_type, f"Reserved/Unspecified ({nalu_type})")


class H265NaluType(IntEnum):
    """H.265/HEVC NAL unit types."""
    TRAIL_N = 0
    TRAIL_R = 1
    TSA_N = 2
    TSA_R = 3
    STSA_N = 4
    STSA_R = 5
    RADL_N = 6
    RADL_R = 7
    RASL_N = 8
    RASL_R = 9
    RSV_VCL_N10 = 10
    RSV_VCL_R11 = 11
    RSV_VCL_N12 = 12
    RSV_VCL_R13 = 13
    RSV_VCL_N14 = 14
    RSV_VCL_R15 = 15
    BLA_W_LP = 16
    BLA_W_RADL = 17
    BLA_N_LP = 18
    IDR_W_RADL = 19
    IDR_N_LP = 20
    CRA = 21
    RSV_IRAP_VCL22 = 22
    RSV_IRAP_VCL23 = 23
    # 24-31 reserved VCL
    VPS = 32
    SPS = 33
    PPS = 34
    AUD = 35
    EOS = 36
    EOB = 37
    FILLER = 38
    PREFIX_SEI = 39
    SUFFIX_SEI = 40
    # 41-47 reserved
    # 48-63 unspecified

    @classmethod
    def name_for_type(cls, nalu_type: int) -> str:
        """Get human-readable name for NAL unit type."""
        names = {
            0: "TRAIL_N (Trailing, non-ref)",
            1: "TRAIL_R (Trailing, ref)",
            2: "TSA_N (Temporal Sub-layer Access, non-ref)",
            3: "TSA_R (Temporal Sub-layer Access, ref)",
            4: "STSA_N (Step-wise Temporal Sub-layer Access, non-ref)",
            5: "STSA_R (Step-wise Temporal Sub-layer Access, ref)",
            6: "RADL_N (Random Access Decodable Leading, non-ref)",
            7: "RADL_R (Random Access Decodable Leading, ref)",
            8: "RASL_N (Random Access Skipped Leading, non-ref)",
            9: "RASL_R (Random Access Skipped Leading, ref)",
            16: "BLA_W_LP (Broken Link Access)",
            17: "BLA_W_RADL (Broken Link Access)",
            18: "BLA_N_LP (Broken Link Access)",
            19: "IDR_W_RADL (IDR)",
            20: "IDR_N_LP (IDR)",
            21: "CRA (Clean Random Access)",
            32: "VPS",
            33: "SPS",
            34: "PPS",
            35: "AUD",
            36: "End of Sequence",
            37: "End of Bitstream",
            38: "Filler Data",
            39: "Prefix SEI",
            40: "Suffix SEI",
        }
        if 10 <= nalu_type <= 15:
            return f"Reserved VCL ({nalu_type})"
        if 22 <= nalu_type <= 23:
            return f"Reserved IRAP VCL ({nalu_type})"
        if 24 <= nalu_type <= 31:
            return f"Reserved VCL ({nalu_type})"
        if 41 <= nalu_type <= 47:
            return f"Reserved ({nalu_type})"
        if 48 <= nalu_type <= 63:
            return f"Unspecified ({nalu_type})"
        return names.get(nalu_type, f"Unknown ({nalu_type})")


@dataclass
class NALUnit:
    """Represents a single NAL unit."""

    offset: int  # Offset in packet data
    size: int  # Size including start code
    data: bytes = field(repr=False)  # Raw NAL unit data (without start code)

    # H.264 fields
    nal_ref_idc: int = 0  # 2 bits
    nal_unit_type: int = 0  # 5 bits for H.264, 6 bits for H.265

    # H.265 fields
    nuh_layer_id: int = 0  # 6 bits
    nuh_temporal_id_plus1: int = 0  # 3 bits

    # Codec type
    is_h265: bool = False

    # Parsed syntax (populated by H264Parser/H265Parser)
    parsed_syntax: dict = field(default_factory=dict)

    @property
    def type_name(self) -> str:
        """Get human-readable NAL unit type name."""
        if self.is_h265:
            return H265NaluType.name_for_type(self.nal_unit_type)
        return H264NaluType.name_for_type(self.nal_unit_type)

    def is_vcl(self) -> bool:
        """Check if this is a VCL (Video Coding Layer) NAL unit."""
        if self.is_h265:
            return 0 <= self.nal_unit_type <= 31
        return 1 <= self.nal_unit_type <= 5

    def is_slice(self) -> bool:
        """Check if this NAL unit contains slice data."""
        if self.is_h265:
            return 0 <= self.nal_unit_type <= 21
        return 1 <= self.nal_unit_type <= 5

    def is_idr(self) -> bool:
        """Check if this is an IDR frame."""
        if self.is_h265:
            return self.nal_unit_type in (19, 20)  # IDR_W_RADL, IDR_N_LP
        return self.nal_unit_type == 5

    def is_parameter_set(self) -> bool:
        """Check if this is a parameter set (SPS, PPS, VPS)."""
        if self.is_h265:
            return self.nal_unit_type in (32, 33, 34)  # VPS, SPS, PPS
        return self.nal_unit_type in (7, 8)  # SPS, PPS


class NALUParser:
    """Parses NAL units from H.264/H.265 bitstream data."""

    def __init__(self, is_h265: bool = False, is_avc: bool = False, nal_length_size: int = 4):
        """
        Initialize parser.

        Args:
            is_h265: True for H.265/HEVC, False for H.264/AVC
            is_avc: True for length-prefixed NALUs (MP4), False for start code format
            nal_length_size: Size of length prefix in AVC format (usually 4)
        """
        self.is_h265 = is_h265
        self.is_avc = is_avc
        self.nal_length_size = nal_length_size

    def parse(self, data: bytes) -> list[NALUnit]:
        """Parse NAL units from packet data."""
        if self.is_avc:
            return self._parse_avc(data)
        return self._parse_annexb(data)

    def _parse_avc(self, data: bytes) -> list[NALUnit]:
        """Parse NAL units from length-prefixed (AVC/HVCC) format."""
        nalus = []
        offset = 0

        # A zero length-size (malformed config) would make offset never advance.
        if self.nal_length_size <= 0:
            return nalus

        while offset < len(data):
            if offset + self.nal_length_size > len(data):
                break

            # Read NAL unit length
            length = 0
            for i in range(self.nal_length_size):
                length = (length << 8) | data[offset + i]

            nalu_start = offset + self.nal_length_size
            nalu_end = nalu_start + length

            if nalu_end > len(data):
                break

            nalu_data = data[nalu_start:nalu_end]
            nalu = self._parse_nalu_header(nalu_data, offset, length + self.nal_length_size)
            nalus.append(nalu)

            offset = nalu_end

        return nalus

    def _parse_annexb(self, data: bytes) -> list[NALUnit]:
        """Parse NAL units from Annex B format (start code prefixed)."""
        nalus = []
        start_codes = self._find_start_codes(data)

        for i, sc_offset in enumerate(start_codes):
            # Determine start code length (3 or 4 bytes)
            if sc_offset >= 1 and data[sc_offset - 1] == 0:
                actual_offset = sc_offset - 1
                sc_len = 4
            else:
                actual_offset = sc_offset
                sc_len = 3

            nalu_start = sc_offset + 3  # After 0x000001

            # Find end of this NAL unit
            if i + 1 < len(start_codes):
                next_sc = start_codes[i + 1]
                # Check for 4-byte start code
                if next_sc >= 1 and data[next_sc - 1] == 0:
                    nalu_end = next_sc - 1
                else:
                    nalu_end = next_sc
            else:
                nalu_end = len(data)

            nalu_data = data[nalu_start:nalu_end]
            if len(nalu_data) == 0:
                continue

            nalu = self._parse_nalu_header(nalu_data, actual_offset, nalu_end - actual_offset)
            nalus.append(nalu)

        return nalus

    def _find_start_codes(self, data: bytes) -> list[int]:
        """Find all start code positions (0x000001)."""
        positions = []
        i = 0
        while i < len(data) - 2:
            if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
                positions.append(i)
                i += 3
            else:
                i += 1
        return positions

    def _parse_nalu_header(self, data: bytes, offset: int, total_size: int) -> NALUnit:
        """Parse NAL unit header."""
        if len(data) == 0:
            return NALUnit(offset=offset, size=total_size, data=data, is_h265=self.is_h265)

        if self.is_h265:
            # H.265 NAL unit header is 2 bytes
            if len(data) >= 2:
                byte0 = data[0]
                byte1 = data[1]

                # forbidden_zero_bit = (byte0 >> 7) & 1
                nal_unit_type = (byte0 >> 1) & 0x3F
                nuh_layer_id = ((byte0 & 1) << 5) | ((byte1 >> 3) & 0x1F)
                nuh_temporal_id_plus1 = byte1 & 0x07

                return NALUnit(
                    offset=offset,
                    size=total_size,
                    data=data,
                    nal_unit_type=nal_unit_type,
                    nuh_layer_id=nuh_layer_id,
                    nuh_temporal_id_plus1=nuh_temporal_id_plus1,
                    is_h265=True
                )
        else:
            # H.264 NAL unit header is 1 byte
            byte0 = data[0]
            # forbidden_zero_bit = (byte0 >> 7) & 1
            nal_ref_idc = (byte0 >> 5) & 0x03
            nal_unit_type = byte0 & 0x1F

            return NALUnit(
                offset=offset,
                size=total_size,
                data=data,
                nal_ref_idc=nal_ref_idc,
                nal_unit_type=nal_unit_type,
                is_h265=False
            )

        return NALUnit(offset=offset, size=total_size, data=data, is_h265=self.is_h265)

    def parse_extradata_h264(self, extradata: bytes) -> list[NALUnit]:
        """Parse SPS/PPS from H.264 AVCDecoderConfigurationRecord."""
        nalus = []
        if len(extradata) < 7:
            return nalus

        # AVCDecoderConfigurationRecord structure
        # configurationVersion = extradata[0]
        # AVCProfileIndication = extradata[1]
        # profile_compatibility = extradata[2]
        # AVCLevelIndication = extradata[3]
        # lengthSizeMinusOne = extradata[4] & 0x03

        num_sps = extradata[5] & 0x1F
        offset = 6

        # Parse SPS
        for _ in range(num_sps):
            if offset + 2 > len(extradata):
                break
            sps_length = (extradata[offset] << 8) | extradata[offset + 1]
            offset += 2
            if offset + sps_length > len(extradata):
                break
            sps_data = extradata[offset:offset + sps_length]
            nalu = self._parse_nalu_header(sps_data, 0, sps_length)
            nalus.append(nalu)
            offset += sps_length

        # Parse PPS
        if offset < len(extradata):
            num_pps = extradata[offset]
            offset += 1
            for _ in range(num_pps):
                if offset + 2 > len(extradata):
                    break
                pps_length = (extradata[offset] << 8) | extradata[offset + 1]
                offset += 2
                if offset + pps_length > len(extradata):
                    break
                pps_data = extradata[offset:offset + pps_length]
                nalu = self._parse_nalu_header(pps_data, 0, pps_length)
                nalus.append(nalu)
                offset += pps_length

        return nalus

    def parse_extradata_h265(self, extradata: bytes) -> list[NALUnit]:
        """Parse VPS/SPS/PPS from H.265 HEVCDecoderConfigurationRecord."""
        nalus = []
        if len(extradata) < 23:
            return nalus

        # Skip to numOfArrays
        num_arrays = extradata[22]
        offset = 23

        for _ in range(num_arrays):
            if offset + 3 > len(extradata):
                break

            # array_completeness = (extradata[offset] >> 7) & 1
            # nal_unit_type = extradata[offset] & 0x3F
            num_nalus = (extradata[offset + 1] << 8) | extradata[offset + 2]
            offset += 3

            for _ in range(num_nalus):
                if offset + 2 > len(extradata):
                    break
                nalu_length = (extradata[offset] << 8) | extradata[offset + 1]
                offset += 2
                if offset + nalu_length > len(extradata):
                    break
                nalu_data = extradata[offset:offset + nalu_length]
                nalu = self._parse_nalu_header(nalu_data, 0, nalu_length)
                nalu.is_h265 = True
                nalus.append(nalu)
                offset += nalu_length

        return nalus
